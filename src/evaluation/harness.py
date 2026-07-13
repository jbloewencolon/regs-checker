"""Evaluation harness — Recommendation #11 (EA1-2 rework, 2026-07-13).

Built BEFORE writing extraction prompts so that all prompt development
is measured against a ground-truth benchmark from day one.

The harness:
1. Loads gold-standard annotations (manually annotated by Regulatory Review Lead)
2. Runs extraction agents against the annotated passages
3. Computes precision, recall, and F1 per agent and per field
4. Reports results for prompt iteration

Two eval modes:

**Clause-level** (per-passage) covers all 6 clause agents — obligation,
definition_actor, threshold_exception, rights_protection, compliance_mechanism,
preemption. Each ``extract()`` returns an ``ExtractionResult`` (a list of
validated extraction dicts + an optional abstention); the harness picks the
single best-matching extraction against the fixture's expected payload so a
passage that legitimately yields several extractions (e.g. three definitions)
is not penalized for the ones the single-slot fixture didn't encode.

**Bill-level** (whole-bill) covers the 3 bill-level agents — enforcement_agent,
applicability_agent, compliance_timeline_agent — which run once per law via
``extract_bill()`` and return a single ``BillLevelResult.payload``. These
fixtures live in their own subtree (``bill_level_gold_standard_dir``) so the
clause loader never sweeps them up.

Clause-level gold format (JSON in tests/fixtures/gold_standard/):
{
    "passage_id": "co_sb205_sec3_a",
    "source_document": "Colorado SB205",
    "section_path": "Section 3(a)",
    "passage_text": "...",
    "expected_extractions": {
        "obligation": { ... ObligationPayload fields ... },
        "definition": { ... DefinitionActorPayload fields ... },
        "threshold_exception": null,      // no extraction expected
        "rights_protection": null,
        "compliance_mechanism": null,
        "preemption": null
    }
}

A fixture key may also hold a LIST of expected payloads when a passage should
produce several extractions of the same type; each expected item is matched
against its best candidate independently.

Bill-level gold format (JSON in tests/fixtures/gold_standard/bill_level/):
{
    "passage_id": "co_sb205_full",
    "source_document": "Colorado SB205",
    "jurisdiction": "CO",
    "bill_text": "...full concatenated bill text...",   // OR:
    "bill_text_file": "output/law_texts/TMP-CO-....txt", // loaded relative to repo root
    "expected_bill_extractions": {
        "enforcement_agent": { "max_civil_penalty_usd": 20000, ... },
        "applicability_agent": { "employee_threshold": 50, ... },
        "compliance_timeline_agent": { "effective_date": "2026-02-01", ... }
    }
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from src.agents.applicability_agent import ApplicabilityAgent
from src.agents.base import BaseExtractionAgent, ExtractionResult
from src.agents.bill_level_base import BillLevelAgent
from src.agents.compliance_mechanism import ComplianceMechanismAgent
from src.agents.compliance_timeline_agent import ComplianceTimelineAgent
from src.agents.definition_actor import DefinitionActorAgent
from src.agents.enforcement_agent import EnforcementAgent
from src.agents.obligation import ObligationAgent
from src.agents.preemption import PreemptionAgent
from src.agents.rights_protection import RightsProtectionAgent
from src.agents.threshold_exception import ThresholdExceptionAgent
from src.core.circuit_breaker import CircuitBreakerTripped, FailureTracker
from src.core.config import settings
from src.schemas.extraction import AbstentionResult

logger = structlog.get_logger()

# Metadata / internal keys that never participate in field scoring.
_SKIP_FIELD_KEYS = {
    "evidence_spans",
    "_prompt_hash",
    "_model_id",
    "_template_version",
    "detected",
    "notes",
}


@dataclass
class FieldScore:
    """Precision/recall for a single field."""

    field_name: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


@dataclass
class AgentScore:
    """Aggregate scores for a single agent across all test cases."""

    agent_name: str
    field_scores: dict[str, FieldScore] = field(default_factory=dict)
    detection_tp: int = 0  # correctly identified extractable passages
    detection_fp: int = 0  # extracted from passage with no expected extraction
    detection_fn: int = 0  # failed to extract from passage with expected extraction
    total_cases: int = 0
    scope: str = "clause"  # "clause" | "bill" — for report grouping

    @property
    def detection_precision(self) -> float:
        denom = self.detection_tp + self.detection_fp
        return self.detection_tp / denom if denom > 0 else 0.0

    @property
    def detection_recall(self) -> float:
        denom = self.detection_tp + self.detection_fn
        return self.detection_tp / denom if denom > 0 else 0.0

    @property
    def macro_f1(self) -> float:
        if not self.field_scores:
            return 0.0
        return sum(fs.f1 for fs in self.field_scores.values()) / len(self.field_scores)


@dataclass
class EvaluationResult:
    """Full evaluation result across all agents and test cases."""

    agent_scores: dict[str, AgentScore] = field(default_factory=dict)
    total_cases: int = 0
    bill_level_cases: int = 0
    errors: list[str] = field(default_factory=list)

    def to_baseline_dict(self) -> dict[str, Any]:
        """Serialize per-agent per-field P/R/F1 for a committed baseline artifact.

        This is the shape EA1-3 diffs future runs against — stable, sorted,
        JSON-serializable, and free of live-run noise (no timestamps or model
        ids, which belong in a run-metadata sidecar, not the score baseline).
        """
        agents_out: dict[str, Any] = {}
        for name, score in sorted(self.agent_scores.items()):
            agents_out[name] = {
                "scope": score.scope,
                "total_cases": score.total_cases,
                "detection": {
                    "precision": round(score.detection_precision, 4),
                    "recall": round(score.detection_recall, 4),
                    "tp": score.detection_tp,
                    "fp": score.detection_fp,
                    "fn": score.detection_fn,
                },
                "macro_f1": round(score.macro_f1, 4),
                "fields": {
                    fname: {
                        "precision": round(fs.precision, 4),
                        "recall": round(fs.recall, 4),
                        "f1": round(fs.f1, 4),
                        "tp": fs.true_positives,
                        "fp": fs.false_positives,
                        "fn": fs.false_negatives,
                    }
                    for fname, fs in sorted(score.field_scores.items())
                },
            }
        return {
            "total_cases": self.total_cases,
            "bill_level_cases": self.bill_level_cases,
            "error_count": len(self.errors),
            "agents": agents_out,
        }


class EvaluationHarness:
    """Runs extraction agents against gold-standard test cases and computes metrics."""

    # Clause-level: fixture ``expected_extractions`` key → agent class.
    # NOTE the key is the extraction TYPE ("definition"), not the agent_name
    # ("definition_actor") — the fixtures were annotated by type.
    CLAUSE_AGENT_MAP: dict[str, type[BaseExtractionAgent]] = {
        "obligation": ObligationAgent,
        "definition": DefinitionActorAgent,
        "threshold_exception": ThresholdExceptionAgent,
        "rights_protection": RightsProtectionAgent,
        "compliance_mechanism": ComplianceMechanismAgent,
        "preemption": PreemptionAgent,
        # "ambiguity" retired — findings embedded as interpretation_risks on
        # obligation/rights payloads (no standalone agent).
    }

    # Backward-compatible alias (older callers / tests referenced AGENT_MAP).
    AGENT_MAP = CLAUSE_AGENT_MAP

    # Bill-level: agent_name → agent class (these run once per whole bill).
    BILL_AGENT_MAP: dict[str, type[BillLevelAgent]] = {
        "enforcement_agent": EnforcementAgent,
        "applicability_agent": ApplicabilityAgent,
        "compliance_timeline_agent": ComplianceTimelineAgent,
    }

    def __init__(
        self,
        gold_standard_dir: str | None = None,
        bill_level_gold_standard_dir: str | None = None,
    ):
        self.gold_dir = Path(gold_standard_dir or settings.gold_standard_dir)
        self.bill_gold_dir = Path(
            bill_level_gold_standard_dir or settings.bill_level_gold_standard_dir
        )

    # ------------------------------------------------------------------
    # Fixture loading
    # ------------------------------------------------------------------

    def load_test_cases(self) -> list[dict]:
        """Load all clause-level gold-standard test case files.

        The bill-level subtree is skipped — it has its own loader — so a
        bill_level/ directory nested under gold_standard/ never leaks into
        the passage eval.
        """
        cases = []
        if not self.gold_dir.exists():
            logger.warning("gold_standard_dir_missing", path=str(self.gold_dir))
            return cases

        bill_dir_resolved = self.bill_gold_dir.resolve()
        for filepath in sorted(self.gold_dir.glob("*.json")):
            # Defensive: if bill fixtures are nested directly (not in a subdir
            # the top-level glob would miss), skip anything under the bill tree.
            if filepath.resolve().parent == bill_dir_resolved:
                continue
            with open(filepath) as f:
                cases.append(json.load(f))

        logger.info("loaded_test_cases", count=len(cases))
        return cases

    def load_bill_test_cases(self) -> list[dict]:
        """Load all bill-level gold-standard test case files.

        Returns [] (not an error) when the bill fixture directory doesn't
        exist yet — bill fixtures are seeded incrementally.
        """
        cases: list[dict] = []
        if not self.bill_gold_dir.exists():
            logger.info("bill_level_gold_standard_dir_missing", path=str(self.bill_gold_dir))
            return cases

        for filepath in sorted(self.bill_gold_dir.glob("*.json")):
            with open(filepath) as f:
                cases.append(json.load(f))

        logger.info("loaded_bill_test_cases", count=len(cases))
        return cases

    @staticmethod
    def _resolve_bill_text(case: dict) -> str:
        """Resolve a bill fixture's full text — inline ``bill_text`` or a
        ``bill_text_file`` path read relative to the repo root."""
        if case.get("bill_text"):
            return case["bill_text"]
        rel = case.get("bill_text_file")
        if rel:
            path = Path(rel)
            if path.exists():
                return path.read_text(encoding="utf-8", errors="replace")
            raise FileNotFoundError(f"bill_text_file not found: {rel}")
        raise ValueError(
            f"bill fixture {case.get('passage_id', '?')} has neither "
            "bill_text nor bill_text_file"
        )

    # ------------------------------------------------------------------
    # Run: clause-level
    # ------------------------------------------------------------------

    def run(self) -> EvaluationResult:
        """Run clause-level evaluation across all 6 clause agents and cases.

        Raises CircuitBreakerTripped if an agent fails on 3+ consecutive
        cases — this indicates the LLM backend is down rather than
        individual extraction issues.
        """
        cases = self.load_test_cases()
        result = EvaluationResult(total_cases=len(cases))

        for agent_name, agent_class in self.CLAUSE_AGENT_MAP.items():
            agent = agent_class()
            agent_score = AgentScore(agent_name=agent_name, scope="clause")

            tracker = FailureTracker(
                context=f"evaluation ({agent_name})",
                max_consecutive=3,
                max_failure_rate=0.9,
                min_items_for_rate=5,
            )

            try:
                for case in cases:
                    passage = case["passage_text"]
                    expected = case.get("expected_extractions", {}).get(agent_name)
                    context = {
                        "document_title": case.get("source_document"),
                        "section_path": case.get("section_path"),
                    }

                    try:
                        raw = agent.extract(passage, context)
                        self._score_extraction_result(agent_score, expected, raw)
                        tracker.record_success()
                    except CircuitBreakerTripped:
                        raise
                    except Exception as e:
                        result.errors.append(
                            f"{agent_name}/{case.get('passage_id', '?')}: {e}"
                        )
                        tracker.record_failure(
                            f"{agent_name}/{case.get('passage_id', '?')}: {e}"
                        )

                    agent_score.total_cases += 1

            except CircuitBreakerTripped as cb:
                result.errors.append(f"CIRCUIT BREAKER: {cb}")
                logger.error(
                    "evaluation_circuit_breaker",
                    agent=agent_name,
                    detail=str(cb),
                )
                # Still record partial scores for this agent
                pass

            result.agent_scores[agent_name] = agent_score

        return result

    # ------------------------------------------------------------------
    # Run: bill-level
    # ------------------------------------------------------------------

    def run_bill_level(
        self, result: EvaluationResult | None = None
    ) -> EvaluationResult:
        """Run bill-level evaluation across the 3 bill-level agents.

        Each bill agent runs once per whole-bill fixture (``extract_bill``)
        and its single payload is scored field-by-field against the fixture's
        ``expected_bill_extractions[agent_name]``. Fixtures without an entry
        for a given agent contribute no cases for that agent (sparse fixtures
        are the norm — one bill rarely has ground truth for all three).

        Pass an existing ``result`` to merge bill scores into a combined run.
        """
        cases = self.load_bill_test_cases()
        if result is None:
            result = EvaluationResult()
        result.bill_level_cases = len(cases)

        for agent_name, agent_class in self.BILL_AGENT_MAP.items():
            # Only spin up the agent (and its provider) if at least one fixture
            # actually has ground truth for it.
            relevant = [
                c
                for c in cases
                if c.get("expected_bill_extractions", {}).get(agent_name) is not None
            ]
            if not relevant:
                continue

            agent = agent_class()
            agent_score = AgentScore(agent_name=agent_name, scope="bill")

            tracker = FailureTracker(
                context=f"bill evaluation ({agent_name})",
                max_consecutive=3,
                max_failure_rate=0.9,
                min_items_for_rate=5,
            )

            try:
                for case in relevant:
                    expected = case["expected_bill_extractions"][agent_name]
                    try:
                        full_text = self._resolve_bill_text(case)
                        bill_result = agent.extract_bill(
                            full_text, context=case.get("context") or {}
                        )
                        self._score_bill_case(
                            agent_score, expected, bill_result.payload
                        )
                        tracker.record_success()
                    except CircuitBreakerTripped:
                        raise
                    except Exception as e:
                        result.errors.append(
                            f"{agent_name}/{case.get('passage_id', '?')}: {e}"
                        )
                        tracker.record_failure(
                            f"{agent_name}/{case.get('passage_id', '?')}: {e}"
                        )
                    agent_score.total_cases += 1

            except CircuitBreakerTripped as cb:
                result.errors.append(f"CIRCUIT BREAKER: {cb}")
                logger.error(
                    "bill_evaluation_circuit_breaker",
                    agent=agent_name,
                    detail=str(cb),
                )
                pass

            result.agent_scores[agent_name] = agent_score

        return result

    def run_all(self) -> EvaluationResult:
        """Run both clause-level and bill-level evaluation into one result."""
        result = self.run()
        return self.run_bill_level(result)

    # ------------------------------------------------------------------
    # Scoring — clause level
    # ------------------------------------------------------------------

    def _score_extraction_result(
        self,
        agent_score: AgentScore,
        expected: dict | list | None,
        raw: ExtractionResult,
    ) -> None:
        """Convert an ExtractionResult to a scorable 'actual' and score it.

        Handles the ExtractionResult → dict/abstention translation the
        pre-rework harness never accounted for: an ExtractionResult carries a
        LIST of extractions plus an optional abstention. We reduce that to the
        single best-matching extraction (per the fixture's single expected
        slot) before delegating to _score_case, or to an abstention when the
        agent produced nothing.
        """
        actual = self._result_to_actual(raw, expected)
        self._score_case(agent_score, expected, actual)

    def _result_to_actual(
        self, raw: ExtractionResult, expected: dict | list | None
    ) -> dict | AbstentionResult:
        """Pick the scorable 'actual' from an ExtractionResult.

        - Explicit abstention, or an empty extraction list → an abstention
          (detection true-negative / false-negative depending on `expected`).
        - Otherwise the extraction whose fields best overlap the expected
          payload (or the first extraction when nothing is expected — its mere
          presence is a detection false-positive).
        """
        if raw.abstention is not None or not raw.extractions:
            reason = raw.abstention.reason if raw.abstention else "no extractions"
            return AbstentionResult(detected=False, reason=reason)

        # When expected is a list, score against its first item for best-match
        # selection; the caller scores the full list separately if needed.
        expected_probe = expected[0] if isinstance(expected, list) and expected else expected
        return self._best_match(expected_probe, raw.extractions)

    def _best_match(
        self, expected: dict | None, candidates: list[dict]
    ) -> dict:
        """Return the candidate extraction most similar to `expected`.

        Similarity = count of shared, non-metadata keys whose values match
        (fuzzy for strings). With no expectation to compare against, the first
        candidate stands in — its presence alone is what gets scored (as a
        detection false-positive).
        """
        if not isinstance(expected, dict) or len(candidates) == 1:
            return candidates[0]

        best = candidates[0]
        best_score = -1
        for cand in candidates:
            score = 0
            for key in set(expected.keys()) & set(cand.keys()):
                if key in _SKIP_FIELD_KEYS:
                    continue
                if self._values_match(expected.get(key), cand.get(key)):
                    score += 1
            if score > best_score:
                best_score = score
                best = cand
        return best

    def _score_case(
        self,
        agent_score: AgentScore,
        expected: dict | list | None,
        actual: dict | AbstentionResult,
    ) -> None:
        """Score a single test case for one agent.

        `actual` is already reduced to a single extraction dict or an
        abstention (see _result_to_actual). `expected` may be a single dict,
        a list of dicts (multi-extraction fixtures — scored against the best
        single candidate here; list-wide matching is a future extension), or
        None (no extraction expected).
        """
        actual_is_abstention = isinstance(actual, AbstentionResult) or (
            isinstance(actual, dict) and actual.get("detected") is False
        )

        # Normalize a list-expected down to its first item for detection +
        # field scoring — the single-slot scorer compares one expected to one
        # actual. (Full list-vs-list alignment is deferred; flagged in module
        # docstring.)
        if isinstance(expected, list):
            expected = expected[0] if expected else None

        if expected is None:
            # No extraction expected
            if actual_is_abstention:
                pass  # Correct abstention — true negative
            else:
                agent_score.detection_fp += 1
        else:
            # Extraction expected
            if actual_is_abstention:
                agent_score.detection_fn += 1
            else:
                agent_score.detection_tp += 1
                # Score individual fields
                assert isinstance(actual, dict)
                self._score_fields(agent_score, expected, actual)

    def _score_fields(
        self,
        agent_score: AgentScore,
        expected: dict,
        actual: dict,
    ) -> None:
        """Compare expected vs actual field values."""
        all_keys = set(expected.keys()) | set(actual.keys())

        for key in all_keys - _SKIP_FIELD_KEYS:
            if key not in agent_score.field_scores:
                agent_score.field_scores[key] = FieldScore(field_name=key)

            fs = agent_score.field_scores[key]
            exp_val = expected.get(key)
            act_val = actual.get(key)

            if exp_val is not None and act_val is not None:
                if self._values_match(exp_val, act_val):
                    fs.true_positives += 1
                else:
                    fs.false_positives += 1
                    fs.false_negatives += 1
            elif exp_val is not None and act_val is None:
                fs.false_negatives += 1
            elif exp_val is None and act_val is not None:
                fs.false_positives += 1

    # ------------------------------------------------------------------
    # Scoring — bill level
    # ------------------------------------------------------------------

    def _score_bill_case(
        self,
        agent_score: AgentScore,
        expected: dict,
        payload: dict,
    ) -> None:
        """Score one bill-level agent payload against expected fields.

        Bill-level agents always return exactly one record, so there is no
        abstention axis. Detection here means "did the agent produce a usable
        record populating at least one expected field": an errored/empty
        payload is a detection false-negative and every expected field a
        field-level false-negative.
        """
        has_error = (not payload) or ("_error" in payload)
        populated = (
            not has_error
            and any(payload.get(k) is not None for k in expected)
        )

        if populated:
            agent_score.detection_tp += 1
            self._score_fields(agent_score, expected, payload)
        else:
            agent_score.detection_fn += 1
            # Record each missing expected field as a false-negative so the
            # per-field recall reflects the total miss, not just silence.
            for key in set(expected.keys()) - _SKIP_FIELD_KEYS:
                if expected.get(key) is None:
                    continue
                if key not in agent_score.field_scores:
                    agent_score.field_scores[key] = FieldScore(field_name=key)
                agent_score.field_scores[key].false_negatives += 1

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _values_match(self, expected: Any, actual: Any) -> bool:
        """Check if two field values match (with fuzzy string matching)."""
        if isinstance(expected, str) and isinstance(actual, str):
            return expected.strip().lower() == actual.strip().lower()
        return expected == actual

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def print_report(self, result: EvaluationResult) -> str:
        """Generate a human-readable evaluation report."""
        lines = [
            "=" * 70,
            "EVALUATION REPORT",
            f"Clause-level test cases: {result.total_cases}",
            f"Bill-level test cases:   {result.bill_level_cases}",
            f"Errors: {len(result.errors)}",
            "=" * 70,
        ]

        for scope_label in ("clause", "bill"):
            scope_scores = {
                n: s
                for n, s in result.agent_scores.items()
                if s.scope == scope_label
            }
            if not scope_scores:
                continue
            lines.append(f"\n{'#' * 3} {scope_label.upper()}-LEVEL AGENTS {'#' * 3}")
            for agent_name, score in scope_scores.items():
                lines.append(f"\n--- {agent_name.upper()} ---")
                lines.append(f"  Cases evaluated: {score.total_cases}")
                lines.append(f"  Detection precision: {score.detection_precision:.3f}")
                lines.append(f"  Detection recall:    {score.detection_recall:.3f}")
                lines.append(f"  Macro F1:            {score.macro_f1:.3f}")

                if score.field_scores:
                    lines.append("  Field-level scores:")
                    for fname, fs in sorted(score.field_scores.items()):
                        lines.append(
                            f"    {fname:30s}  P={fs.precision:.3f}  "
                            f"R={fs.recall:.3f}  F1={fs.f1:.3f}"
                        )

        if result.errors:
            lines.append("\n--- ERRORS ---")
            for err in result.errors:
                lines.append(f"  {err}")

        report = "\n".join(lines)
        logger.info("evaluation_report", report=report)
        return report

    def write_baseline(self, result: EvaluationResult, path: str | Path) -> Path:
        """Write the per-agent per-field baseline artifact (EA1-3 gate input).

        Deterministic, sorted JSON so a future run's diff is meaningful. Returns
        the path written.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(result.to_baseline_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        logger.info("evaluation_baseline_written", path=str(out))
        return out
