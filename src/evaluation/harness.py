"""Evaluation harness — Recommendation #11.

Built BEFORE writing extraction prompts so that all prompt development
is measured against a ground-truth benchmark from day one.

The harness:
1. Loads gold-standard annotations (manually annotated by Regulatory Review Lead)
2. Runs extraction agents against the annotated passages
3. Computes precision, recall, and F1 per agent and per field
4. Reports results for prompt iteration

Gold standard format (JSON files in tests/fixtures/gold_standard/):
{
    "passage_id": "co_sb205_sec3_a",
    "source_document": "Colorado SB205",
    "section_path": "Section 3(a)",
    "passage_text": "...",
    "expected_extractions": {
        "obligation": { ... ObligationPayload fields ... },
        "definition": { ... DefinitionActorPayload fields ... },
        "threshold_exception": null,  // no extraction expected
        "ambiguity": null
    }
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from src.agents.base import BaseExtractionAgent
from src.agents.definition_actor import DefinitionActorAgent
from src.agents.obligation import ObligationAgent
from src.agents.threshold_exception import ThresholdExceptionAgent
from src.core.circuit_breaker import CircuitBreakerTripped, FailureTracker
from src.core.config import settings
from src.schemas.extraction import AbstentionResult

logger = structlog.get_logger()


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
    errors: list[str] = field(default_factory=list)


class EvaluationHarness:
    """Runs extraction agents against gold-standard test cases and computes metrics."""

    AGENT_MAP: dict[str, type[BaseExtractionAgent]] = {
        "obligation": ObligationAgent,
        "definition": DefinitionActorAgent,
        "threshold_exception": ThresholdExceptionAgent,
        # "ambiguity" removed — findings now embedded as interpretation_risks on obligation/rights payloads
    }

    def __init__(self, gold_standard_dir: str | None = None):
        self.gold_dir = Path(gold_standard_dir or settings.gold_standard_dir)

    def load_test_cases(self) -> list[dict]:
        """Load all gold-standard test case files."""
        cases = []
        if not self.gold_dir.exists():
            logger.warning("gold_standard_dir_missing", path=str(self.gold_dir))
            return cases

        for filepath in sorted(self.gold_dir.glob("*.json")):
            with open(filepath) as f:
                cases.append(json.load(f))

        logger.info("loaded_test_cases", count=len(cases))
        return cases

    def run(self) -> EvaluationResult:
        """Run full evaluation across all agents and test cases.

        Raises CircuitBreakerTripped if an agent fails on 3+ consecutive
        cases — this indicates the LLM backend is down rather than
        individual extraction issues.
        """
        cases = self.load_test_cases()
        result = EvaluationResult(total_cases=len(cases))

        for agent_name, agent_class in self.AGENT_MAP.items():
            agent = agent_class()
            agent_score = AgentScore(agent_name=agent_name)

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
                        actual = agent.extract(passage, context)
                        self._score_case(agent_score, expected, actual)
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

    def _score_case(
        self,
        agent_score: AgentScore,
        expected: dict | None,
        actual: dict | AbstentionResult,
    ) -> None:
        """Score a single test case for one agent."""
        actual_is_abstention = isinstance(actual, AbstentionResult) or (
            isinstance(actual, dict) and actual.get("detected") is False
        )

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
        # Skip internal metadata fields
        skip_keys = {"evidence_spans", "_prompt_hash", "_model_id", "detected"}

        for key in all_keys - skip_keys:
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

    def _values_match(self, expected: Any, actual: Any) -> bool:
        """Check if two field values match (with fuzzy string matching)."""
        if isinstance(expected, str) and isinstance(actual, str):
            return expected.strip().lower() == actual.strip().lower()
        return expected == actual

    def print_report(self, result: EvaluationResult) -> str:
        """Generate a human-readable evaluation report."""
        lines = [
            "=" * 70,
            "EVALUATION REPORT",
            f"Total test cases: {result.total_cases}",
            f"Errors: {len(result.errors)}",
            "=" * 70,
        ]

        for agent_name, score in result.agent_scores.items():
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
