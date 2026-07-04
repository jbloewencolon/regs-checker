"""Unit tests for EA6-3 — cross-agent interpretation_risks deduplication.

Bug: obligation and rights_protection each populate interpretation_risks
inline during their own primary extraction pass (the retired standalone
AmbiguityAgent's replacement) but neither agent sees the other's output —
both independently flagging the same ambiguous term on the same passage
(e.g. both notice "reasonable" is a vague_term) produces two review-queue
items describing what is really one finding.

Fix: _dedupe_interpretation_risks() runs once per passage, after both
agents' results are collected but before they're persisted as separate
Extraction rows, and strips later duplicates (same term + risk_type,
case/whitespace-insensitive) in a fixed agent-precedence order so the same
passage dedupes identically on every run regardless of thread-completion
order.
"""

from __future__ import annotations

from src.agents.base import ExtractionResult
from src.ingestion.extractor import _dedupe_interpretation_risks


def _result(extractions: list[dict]) -> ExtractionResult:
    return ExtractionResult(
        extractions=extractions,
        abstention=None,
        input_tokens=100,
        output_tokens=50,
        prompt_hash="abc123",
        model_id="test-model",
        template_version="1.0",
    )


def _risk(term: str, risk_type: str = "vague_term") -> dict:
    return {"risk_type": risk_type, "term": term, "concern": "unclear scope", "severity": "medium"}


def _agent_results(
    obligation_result=None, rights_result=None, extra: list | None = None
) -> list[tuple]:
    entries = []
    if obligation_result is not None:
        entries.append(("obligation", "hash1", 1, obligation_result, 10))
    if rights_result is not None:
        entries.append(("rights_protection", "hash2", 2, rights_result, 10))
    if extra:
        entries.extend(extra)
    return entries


class TestCrossAgentDedup:
    def test_duplicate_across_agents_removed(self):
        obligation = _result([
            {"subject": "developer", "interpretation_risks": [_risk("reasonable")]},
        ])
        rights = _result([
            {"subject": "consumer", "interpretation_risks": [_risk("reasonable")]},
        ])
        agent_results = _agent_results(obligation, rights)

        _dedupe_interpretation_risks(agent_results)

        assert obligation.extractions[0]["interpretation_risks"] == [_risk("reasonable")]
        assert rights.extractions[0]["interpretation_risks"] == []

    def test_case_and_whitespace_insensitive_match(self):
        obligation = _result([
            {"interpretation_risks": [_risk("Reasonable")]},
        ])
        rights = _result([
            {"interpretation_risks": [_risk("  reasonable  ")]},
        ])
        agent_results = _agent_results(obligation, rights)

        _dedupe_interpretation_risks(agent_results)

        assert len(obligation.extractions[0]["interpretation_risks"]) == 1
        assert rights.extractions[0]["interpretation_risks"] == []

    def test_different_risk_type_same_term_not_deduped(self):
        # Same term flagged for genuinely different reasons is two findings.
        obligation = _result([
            {"interpretation_risks": [_risk("promptly", risk_type="vague_term")]},
        ])
        rights = _result([
            {"interpretation_risks": [_risk("promptly", risk_type="temporal_ambiguity")]},
        ])
        agent_results = _agent_results(obligation, rights)

        _dedupe_interpretation_risks(agent_results)

        assert len(obligation.extractions[0]["interpretation_risks"]) == 1
        assert len(rights.extractions[0]["interpretation_risks"]) == 1

    def test_distinct_terms_both_kept(self):
        obligation = _result([
            {"interpretation_risks": [_risk("reasonable")]},
        ])
        rights = _result([
            {"interpretation_risks": [_risk("appropriate")]},
        ])
        agent_results = _agent_results(obligation, rights)

        _dedupe_interpretation_risks(agent_results)

        assert len(obligation.extractions[0]["interpretation_risks"]) == 1
        assert len(rights.extractions[0]["interpretation_risks"]) == 1

    def test_precedence_is_fixed_regardless_of_list_order(self):
        # agent_results order reflects thread-completion order in production
        # (as_completed()) — precedence must come from _INTERPRETATION_RISK_
        # AGENTS, not list position, so results are deterministic.
        obligation = _result([{"interpretation_risks": [_risk("reasonable")]}])
        rights = _result([{"interpretation_risks": [_risk("reasonable")]}])
        # rights_protection appears FIRST in the list here.
        agent_results = [
            ("rights_protection", "h2", 2, rights, 10),
            ("obligation", "h1", 1, obligation, 10),
        ]

        _dedupe_interpretation_risks(agent_results)

        # obligation still wins (fixed precedence), not "whichever came first".
        assert len(obligation.extractions[0]["interpretation_risks"]) == 1
        assert rights.extractions[0]["interpretation_risks"] == []

    def test_only_one_agent_present_no_op(self):
        obligation = _result([{"interpretation_risks": [_risk("reasonable")]}])
        agent_results = _agent_results(obligation_result=obligation)

        _dedupe_interpretation_risks(agent_results)

        assert len(obligation.extractions[0]["interpretation_risks"]) == 1

    def test_multiple_obligation_items_same_passage_also_deduped(self):
        # Incidental but correct consequence of a shared `seen` set across
        # one passage: two obligations from the SAME agent both citing the
        # same ambiguous term really is one underlying finding too.
        obligation = _result([
            {"subject": "developer", "interpretation_risks": [_risk("reasonable")]},
            {"subject": "deployer", "interpretation_risks": [_risk("reasonable")]},
        ])
        agent_results = _agent_results(obligation_result=obligation, rights_result=_result([]))

        _dedupe_interpretation_risks(agent_results)

        assert len(obligation.extractions[0]["interpretation_risks"]) == 1
        assert obligation.extractions[1]["interpretation_risks"] == []

    def test_missing_interpretation_risks_key_untouched(self):
        obligation = _result([{"subject": "developer"}])
        rights = _result([{"subject": "consumer"}])
        agent_results = _agent_results(obligation, rights)

        _dedupe_interpretation_risks(agent_results)  # must not raise

        assert "interpretation_risks" not in obligation.extractions[0]

    def test_empty_interpretation_risks_list_untouched(self):
        obligation = _result([{"interpretation_risks": []}])
        agent_results = _agent_results(obligation_result=obligation, rights_result=_result([]))

        _dedupe_interpretation_risks(agent_results)

        assert obligation.extractions[0]["interpretation_risks"] == []

    def test_exception_result_skipped_without_error(self):
        obligation = _result([{"interpretation_risks": [_risk("reasonable")]}])
        agent_results = [
            ("obligation", "h1", 1, obligation, 10),
            ("rights_protection", "h2", 2, RuntimeError("agent failed"), 10),
        ]

        _dedupe_interpretation_risks(agent_results)  # must not raise

        assert len(obligation.extractions[0]["interpretation_risks"]) == 1

    def test_other_agents_ignored(self):
        # A non-interpretation-risk agent in the same passage's agent_results
        # must not interfere.
        obligation = _result([{"interpretation_risks": [_risk("reasonable")]}])
        rights = _result([{"interpretation_risks": [_risk("reasonable")]}])
        other = _result([{"subject": "developer"}])
        agent_results = _agent_results(
            obligation, rights, extra=[("definition_actor", "h3", 3, other, 10)]
        )

        _dedupe_interpretation_risks(agent_results)

        assert len(obligation.extractions[0]["interpretation_risks"]) == 1
        assert rights.extractions[0]["interpretation_risks"] == []

    def test_no_agent_results_no_op(self):
        _dedupe_interpretation_risks([])  # must not raise
