"""Unit tests for the evaluation harness.

Tests fixture loading, scoring logic, and report generation
without requiring LLM API calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.base import ExtractionResult
from src.agents.bill_level_base import BillLevelResult
from src.evaluation.harness import (
    AgentScore,
    EvaluationHarness,
    EvaluationResult,
    FieldScore,
)
from src.schemas.extraction import AbstentionResult


class TestFieldScore:
    def test_perfect_precision_recall(self):
        fs = FieldScore(field_name="subject", true_positives=5)
        assert fs.precision == 1.0
        assert fs.recall == 1.0
        assert fs.f1 == 1.0

    def test_zero_scores(self):
        fs = FieldScore(field_name="subject")
        assert fs.precision == 0.0
        assert fs.recall == 0.0
        assert fs.f1 == 0.0

    def test_mixed_scores(self):
        fs = FieldScore(field_name="subject", true_positives=3, false_positives=1, false_negatives=2)
        assert fs.precision == 0.75
        assert fs.recall == 0.6
        assert abs(fs.f1 - 2 * 0.75 * 0.6 / (0.75 + 0.6)) < 0.001


class TestAgentScore:
    def test_detection_metrics(self):
        score = AgentScore(agent_name="obligation", detection_tp=8, detection_fp=2, detection_fn=1)
        assert score.detection_precision == 0.8
        assert abs(score.detection_recall - 8 / 9) < 0.001

    def test_macro_f1(self):
        score = AgentScore(agent_name="obligation")
        score.field_scores["a"] = FieldScore(field_name="a", true_positives=5)
        score.field_scores["b"] = FieldScore(field_name="b", true_positives=0)
        assert score.macro_f1 == 0.5  # (1.0 + 0.0) / 2


class TestHarnessScoring:
    def test_correct_abstention(self):
        harness = EvaluationHarness()
        agent_score = AgentScore(agent_name="test")
        harness._score_case(agent_score, None, AbstentionResult(detected=False, reason="nothing"))
        assert agent_score.detection_fp == 0
        assert agent_score.detection_fn == 0

    def test_false_positive(self):
        harness = EvaluationHarness()
        agent_score = AgentScore(agent_name="test")
        harness._score_case(agent_score, None, {"subject": "developer", "modality": "shall"})
        assert agent_score.detection_fp == 1

    def test_false_negative(self):
        harness = EvaluationHarness()
        agent_score = AgentScore(agent_name="test")
        harness._score_case(
            agent_score,
            {"subject": "developer"},
            AbstentionResult(detected=False, reason="no obligation"),
        )
        assert agent_score.detection_fn == 1

    def test_true_positive_with_fields(self):
        harness = EvaluationHarness()
        agent_score = AgentScore(agent_name="test")
        harness._score_case(
            agent_score,
            {"subject": "developer", "modality": "shall"},
            {"subject": "developer", "modality": "shall"},
        )
        assert agent_score.detection_tp == 1
        assert agent_score.field_scores["subject"].true_positives == 1
        assert agent_score.field_scores["modality"].true_positives == 1

    def test_fuzzy_string_matching(self):
        harness = EvaluationHarness()
        assert harness._values_match("Developer", "developer")
        assert harness._values_match("  shall  ", "shall")
        assert not harness._values_match("shall", "must")


class TestHarnessFixtureLoading:
    def test_loads_all_fixtures(self):
        harness = EvaluationHarness(gold_standard_dir="tests/fixtures/gold_standard")
        cases = harness.load_test_cases()
        assert len(cases) >= 10

    def test_fixture_structure(self):
        harness = EvaluationHarness(gold_standard_dir="tests/fixtures/gold_standard")
        cases = harness.load_test_cases()
        for case in cases:
            assert "passage_id" in case
            assert "passage_text" in case
            assert "expected_extractions" in case
            ee = case["expected_extractions"]
            assert all(k in ee for k in ["obligation", "definition", "threshold_exception", "ambiguity"])


def _mk_result(extractions=None, abstention=None):
    """Build an ExtractionResult with test-friendly defaults."""
    return ExtractionResult(
        extractions=extractions or [],
        abstention=abstention,
        input_tokens=10,
        output_tokens=20,
        prompt_hash="deadbeef",
        model_id="test-model",
        template_version="v1.0",
    )


class _FakeAgent:
    """Stand-in for a clause agent whose extract() returns a canned result."""

    def __init__(self, result: ExtractionResult):
        self._result = result

    def extract(self, passage, context=None, call_max_tokens=None):
        return self._result


class _FakeBillAgent:
    """Stand-in for a bill agent whose extract_bill() returns a canned payload."""

    def __init__(self, payload: dict):
        self._payload = payload

    def extract_bill(self, full_text, context=None):
        return BillLevelResult(
            payload=self._payload,
            model_id="test-model",
            input_tokens=10,
            output_tokens=20,
            raw_output="{}",
        )


class TestAgentMapCompleteness:
    def test_all_six_clause_agents_wired(self):
        assert set(EvaluationHarness.CLAUSE_AGENT_MAP.keys()) == {
            "obligation",
            "definition",
            "threshold_exception",
            "rights_protection",
            "compliance_mechanism",
            "preemption",
        }

    def test_all_three_bill_agents_wired(self):
        assert set(EvaluationHarness.BILL_AGENT_MAP.keys()) == {
            "enforcement_agent",
            "applicability_agent",
            "compliance_timeline_agent",
        }

    def test_agent_map_alias_preserved(self):
        # Older callers referenced AGENT_MAP directly.
        assert EvaluationHarness.AGENT_MAP is EvaluationHarness.CLAUSE_AGENT_MAP


class TestResultToActual:
    def test_explicit_abstention_becomes_abstention(self):
        harness = EvaluationHarness()
        raw = _mk_result(abstention=AbstentionResult(detected=False, reason="none"))
        actual = harness._result_to_actual(raw, {"subject": "developer"})
        assert isinstance(actual, AbstentionResult)
        assert actual.reason == "none"

    def test_empty_extractions_becomes_abstention(self):
        harness = EvaluationHarness()
        raw = _mk_result(extractions=[])
        actual = harness._result_to_actual(raw, {"subject": "developer"})
        assert isinstance(actual, AbstentionResult)
        assert actual.reason == "no extractions"

    def test_single_extraction_returned_directly(self):
        harness = EvaluationHarness()
        ext = {"subject": "developer", "modality": "shall"}
        raw = _mk_result(extractions=[ext])
        actual = harness._result_to_actual(raw, {"subject": "developer"})
        assert actual == ext

    def test_best_match_picks_closest_of_several(self):
        """A definition passage yields 3 terms; the fixture encodes 1 — the
        harness must score against the matching term, not an arbitrary one."""
        harness = EvaluationHarness()
        expected = {"term": "SYNTHETIC MEDIA", "definition_text": "an image..."}
        candidates = [
            {"term": "CREATOR", "definition_text": "any person..."},
            {"term": "SYNTHETIC MEDIA", "definition_text": "an image..."},
            {"term": "DEEPFAKE", "definition_text": "media that..."},
        ]
        raw = _mk_result(extractions=candidates)
        actual = harness._result_to_actual(raw, expected)
        assert actual["term"] == "SYNTHETIC MEDIA"

    def test_no_expectation_returns_first(self):
        harness = EvaluationHarness()
        candidates = [{"term": "A"}, {"term": "B"}]
        raw = _mk_result(extractions=candidates)
        actual = harness._result_to_actual(raw, None)
        assert actual["term"] == "A"

    def test_list_expected_probes_first_item(self):
        harness = EvaluationHarness()
        expected = [{"term": "B"}, {"term": "A"}]
        candidates = [{"term": "A"}, {"term": "B"}]
        raw = _mk_result(extractions=candidates)
        actual = harness._result_to_actual(raw, expected)
        assert actual["term"] == "B"  # best-match against expected[0]


class TestScoreExtractionResult:
    def test_true_positive_scores_fields(self):
        harness = EvaluationHarness()
        score = AgentScore(agent_name="obligation")
        expected = {"subject": "developer", "modality": "shall"}
        raw = _mk_result(extractions=[dict(expected)])
        harness._score_extraction_result(score, expected, raw)
        assert score.detection_tp == 1
        assert score.field_scores["subject"].true_positives == 1
        assert score.field_scores["modality"].true_positives == 1

    def test_abstention_when_expected_is_false_negative(self):
        harness = EvaluationHarness()
        score = AgentScore(agent_name="obligation")
        raw = _mk_result(abstention=AbstentionResult(detected=False, reason="x"))
        harness._score_extraction_result(score, {"subject": "developer"}, raw)
        assert score.detection_fn == 1

    def test_extraction_when_none_expected_is_false_positive(self):
        harness = EvaluationHarness()
        score = AgentScore(agent_name="preemption")
        raw = _mk_result(extractions=[{"conflict_type": "cross_state_conflict"}])
        harness._score_extraction_result(score, None, raw)
        assert score.detection_fp == 1

    def test_metadata_keys_not_scored_as_fields(self):
        harness = EvaluationHarness()
        score = AgentScore(agent_name="definition")
        expected = {"term": "CREATOR"}
        actual = {
            "term": "CREATOR",
            "_prompt_hash": "abc",
            "_model_id": "m",
            "_template_version": "v1",
            "evidence_spans": [{"text": "..."}],
        }
        raw = _mk_result(extractions=[actual])
        harness._score_extraction_result(score, expected, raw)
        assert set(score.field_scores.keys()) == {"term"}


class TestClauseRunWithMockedAgents:
    def test_run_scores_all_clause_agents(self, tmp_path, monkeypatch):
        # One fixture: obligation expected, everything else null.
        fixture = {
            "passage_id": "t1",
            "passage_text": "A developer shall register the model.",
            "expected_extractions": {
                "obligation": {"subject": "developer", "modality": "shall"},
                "definition": None,
                "threshold_exception": None,
                "rights_protection": None,
                "compliance_mechanism": None,
                "preemption": None,
            },
        }
        (tmp_path / "t1.json").write_text(json.dumps(fixture))

        # Obligation agent finds the obligation; all others abstain.
        oblig_result = _mk_result(
            extractions=[{"subject": "developer", "modality": "shall"}]
        )
        abstain = _mk_result(
            abstention=AbstentionResult(detected=False, reason="n/a")
        )
        fake_map = {
            "obligation": lambda: _FakeAgent(oblig_result),
            "definition": lambda: _FakeAgent(abstain),
            "threshold_exception": lambda: _FakeAgent(abstain),
            "rights_protection": lambda: _FakeAgent(abstain),
            "compliance_mechanism": lambda: _FakeAgent(abstain),
            "preemption": lambda: _FakeAgent(abstain),
        }
        monkeypatch.setattr(EvaluationHarness, "CLAUSE_AGENT_MAP", fake_map)

        harness = EvaluationHarness(gold_standard_dir=str(tmp_path))
        result = harness.run()

        assert result.total_cases == 1
        assert set(result.agent_scores.keys()) == set(fake_map.keys())
        # Obligation: true positive.
        assert result.agent_scores["obligation"].detection_tp == 1
        # Everything else correctly abstained (no false positives).
        for name in fake_map:
            if name != "obligation":
                assert result.agent_scores[name].detection_fp == 0
                assert result.agent_scores[name].detection_fn == 0

    def test_run_records_errors_without_crashing(self, tmp_path, monkeypatch):
        fixture = {
            "passage_id": "t1",
            "passage_text": "text",
            "expected_extractions": {"obligation": {"subject": "x"}},
        }
        (tmp_path / "t1.json").write_text(json.dumps(fixture))

        class _BoomAgent:
            def extract(self, passage, context=None, call_max_tokens=None):
                raise RuntimeError("provider down")

        monkeypatch.setattr(
            EvaluationHarness,
            "CLAUSE_AGENT_MAP",
            {"obligation": lambda: _BoomAgent()},
        )
        harness = EvaluationHarness(gold_standard_dir=str(tmp_path))
        result = harness.run()
        assert len(result.errors) == 1
        assert "provider down" in result.errors[0]


class TestBillLevelScoring:
    def test_populated_payload_is_true_positive(self):
        harness = EvaluationHarness()
        score = AgentScore(agent_name="enforcement_agent", scope="bill")
        expected = {"max_civil_penalty_usd": 20000, "private_right_of_action": False}
        payload = {"max_civil_penalty_usd": 20000, "private_right_of_action": False}
        harness._score_bill_case(score, expected, payload)
        assert score.detection_tp == 1
        assert score.field_scores["max_civil_penalty_usd"].true_positives == 1
        assert score.field_scores["private_right_of_action"].true_positives == 1

    def test_errored_payload_is_false_negative(self):
        harness = EvaluationHarness()
        score = AgentScore(agent_name="enforcement_agent", scope="bill")
        expected = {"max_civil_penalty_usd": 20000}
        payload = {"_error": "timeout"}
        harness._score_bill_case(score, expected, payload)
        assert score.detection_fn == 1
        assert score.field_scores["max_civil_penalty_usd"].false_negatives == 1

    def test_wrong_value_is_fp_and_fn(self):
        harness = EvaluationHarness()
        score = AgentScore(agent_name="enforcement_agent", scope="bill")
        expected = {"max_civil_penalty_usd": 20000}
        payload = {"max_civil_penalty_usd": 500}
        harness._score_bill_case(score, expected, payload)
        assert score.detection_tp == 1
        fs = score.field_scores["max_civil_penalty_usd"]
        assert fs.false_positives == 1
        assert fs.false_negatives == 1

    def test_run_bill_level_with_mocked_agents(self, tmp_path, monkeypatch):
        bill_fixture = {
            "passage_id": "bill1",
            "jurisdiction": "CO",
            "bill_text": "SECTION 1. A civil penalty of $20,000 applies.",
            "expected_bill_extractions": {
                "enforcement_agent": {"max_civil_penalty_usd": 20000},
                # No ground truth for the other two agents on this bill.
            },
        }
        (tmp_path / "bill1.json").write_text(json.dumps(bill_fixture))

        monkeypatch.setattr(
            EvaluationHarness,
            "BILL_AGENT_MAP",
            {
                "enforcement_agent": lambda: _FakeBillAgent(
                    {"max_civil_penalty_usd": 20000}
                ),
                "applicability_agent": lambda: _FakeBillAgent({}),
                "compliance_timeline_agent": lambda: _FakeBillAgent({}),
            },
        )
        harness = EvaluationHarness(bill_level_gold_standard_dir=str(tmp_path))
        result = harness.run_bill_level()

        assert result.bill_level_cases == 1
        # Only the enforcement agent had ground truth → only it is scored.
        assert "enforcement_agent" in result.agent_scores
        assert "applicability_agent" not in result.agent_scores
        assert result.agent_scores["enforcement_agent"].detection_tp == 1
        assert result.agent_scores["enforcement_agent"].scope == "bill"


class TestResolveBillText:
    def test_inline_bill_text(self):
        harness = EvaluationHarness()
        assert harness._resolve_bill_text({"bill_text": "hello"}) == "hello"

    def test_bill_text_file(self, tmp_path):
        f = tmp_path / "bill.txt"
        f.write_text("full bill text")
        harness = EvaluationHarness()
        assert harness._resolve_bill_text({"bill_text_file": str(f)}) == "full bill text"

    def test_missing_file_raises(self):
        harness = EvaluationHarness()
        with pytest.raises(FileNotFoundError):
            harness._resolve_bill_text({"bill_text_file": "does/not/exist.txt"})

    def test_neither_source_raises(self):
        harness = EvaluationHarness()
        with pytest.raises(ValueError):
            harness._resolve_bill_text({"passage_id": "x"})


class TestBillFixturesExcludedFromClauseLoader:
    def test_clause_loader_skips_bill_subtree(self, tmp_path):
        # gold_standard/ has one clause fixture + a bill_level/ subdir the
        # clause glob would otherwise not see (glob is non-recursive), so this
        # asserts the intended non-recursive isolation holds.
        (tmp_path / "clause1.json").write_text(
            json.dumps(
                {
                    "passage_id": "c1",
                    "passage_text": "x",
                    "expected_extractions": {"obligation": None},
                }
            )
        )
        bill_dir = tmp_path / "bill_level"
        bill_dir.mkdir()
        (bill_dir / "bill1.json").write_text(json.dumps({"passage_id": "b1"}))

        harness = EvaluationHarness(
            gold_standard_dir=str(tmp_path),
            bill_level_gold_standard_dir=str(bill_dir),
        )
        clause = harness.load_test_cases()
        assert len(clause) == 1
        assert clause[0]["passage_id"] == "c1"
        bill = harness.load_bill_test_cases()
        assert len(bill) == 1
        assert bill[0]["passage_id"] == "b1"


class TestSeededBillFixtures:
    def test_default_harness_discovers_bill_seed(self):
        harness = EvaluationHarness()
        cases = harness.load_bill_test_cases()
        # Both enforcement seeds committed with EA1-2/EA1-1: AZ (civil,
        # per-day; inline text) and AR (criminal, Class B felony; file ref).
        ids = {c["passage_id"] for c in cases}
        assert "az_sb1359_enforcement" in ids
        assert "ar_hb1877_enforcement" in ids

    def test_bill_seed_covers_two_enforcement_shapes(self):
        """The bill-level set should not be an enforcement monoculture — AZ is
        a civil per-day penalty, AR is a criminal felony class."""
        harness = EvaluationHarness()
        by_id = {c["passage_id"]: c for c in harness.load_bill_test_cases()}
        az = by_id["az_sb1359_enforcement"]["expected_bill_extractions"]["enforcement_agent"]
        ar = by_id["ar_hb1877_enforcement"]["expected_bill_extractions"]["enforcement_agent"]
        assert az.get("penalty_per") == "day"
        assert ar.get("criminal_penalties") is True

    def test_bill_seed_structure(self):
        harness = EvaluationHarness()
        for case in harness.load_bill_test_cases():
            assert "passage_id" in case
            assert case.get("bill_text") or case.get("bill_text_file")
            assert "expected_bill_extractions" in case
            # Every named agent must be a real bill-level agent.
            for agent_name in case["expected_bill_extractions"]:
                assert agent_name in EvaluationHarness.BILL_AGENT_MAP

    def test_bill_seed_text_resolves(self):
        harness = EvaluationHarness()
        for case in harness.load_bill_test_cases():
            text = harness._resolve_bill_text(case)
            assert len(text) > 100  # real bill text, not a stub


class TestBaselineArtifact:
    def test_to_baseline_dict_shape(self):
        result = EvaluationResult(total_cases=3, bill_level_cases=1)
        score = AgentScore(
            agent_name="obligation",
            detection_tp=2,
            detection_fp=1,
            detection_fn=0,
            total_cases=3,
        )
        score.field_scores["subject"] = FieldScore(
            field_name="subject", true_positives=2, false_positives=0, false_negatives=1
        )
        result.agent_scores["obligation"] = score

        d = result.to_baseline_dict()
        assert d["total_cases"] == 3
        assert d["bill_level_cases"] == 1
        assert d["agents"]["obligation"]["detection"]["tp"] == 2
        assert d["agents"]["obligation"]["fields"]["subject"]["fn"] == 1
        assert d["agents"]["obligation"]["scope"] == "clause"

    def test_write_baseline_roundtrip(self, tmp_path):
        result = EvaluationResult(total_cases=1)
        result.agent_scores["obligation"] = AgentScore(
            agent_name="obligation", detection_tp=1, total_cases=1
        )
        harness = EvaluationHarness()
        out = harness.write_baseline(result, tmp_path / "sub" / "baseline.json")
        assert out.exists()
        loaded = json.loads(out.read_text())
        assert loaded["agents"]["obligation"]["detection"]["tp"] == 1


class TestReportGeneration:
    def test_print_report(self):
        result = EvaluationResult(total_cases=5)
        result.agent_scores["obligation"] = AgentScore(
            agent_name="obligation",
            detection_tp=3,
            detection_fp=1,
            detection_fn=1,
            total_cases=5,
        )
        result.agent_scores["obligation"].field_scores["subject"] = FieldScore(
            field_name="subject", true_positives=3, false_positives=0, false_negatives=0
        )

        harness = EvaluationHarness()
        report = harness.print_report(result)

        assert "EVALUATION REPORT" in report
        assert "OBLIGATION" in report
        assert "subject" in report
        assert "Clause-level test cases: 5" in report
