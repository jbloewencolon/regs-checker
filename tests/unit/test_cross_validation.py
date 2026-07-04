"""Unit tests for src/agents/cross_validation.py — attribution safety (EA0-1).

The bug: `run_cross_validation` trusted the model-reported `extraction_index`
with a `len(results)` fallback when absent, and defaulted a missing
`accuracy_score` to 1.0 whenever `is_valid` was true. Either defect lets a
malformed/adversarial model response write a CV score — and a confidence/tier
recompute downstream — onto the wrong extraction, or onto a fabricated
"perfect" score with no real evaluation behind it.

These tests mock the provider so no live LLM is required.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from src.agents.cross_validation import run_cross_validation
from src.core.llm_provider import LLMResponse, LLMUsage

PASSAGE = "A developer shall conduct an annual bias audit of any high-risk AI system."


def _mock_provider(raw_json: dict | str):
    """Build a mock provider whose .call() returns the given JSON body."""
    text = raw_json if isinstance(raw_json, str) else json.dumps(raw_json)
    provider = MagicMock()
    provider.call.return_value = LLMResponse(
        text=text,
        usage=LLMUsage(input_tokens=100, output_tokens=50),
        model_id="test-model",
    )
    return provider


def _run(raw_json, extractions, extraction_ids):
    with patch(
        "src.agents.cross_validation.get_extraction_provider",
        return_value=_mock_provider(raw_json),
    ):
        return run_cross_validation(
            passage_text=PASSAGE,
            extractions=extractions,
            passage_record_id=1,
            extraction_ids=extraction_ids,
        )


class TestWellFormedResponse:
    def test_single_extraction_correctly_attributed(self):
        result = _run(
            {
                "validations": [
                    {
                        "extraction_index": 0,
                        "is_valid": True,
                        "accuracy_score": 0.95,
                        "issues": [],
                    }
                ]
            },
            extractions=[{"subject": "developer", "action": "audit"}],
            extraction_ids=[101],
        )
        assert result.status == "completed"
        assert result.extractions_checked == 1
        assert result.results[0]["extraction_id"] == 101
        assert result.results[0]["accuracy_score"] == 0.95
        assert result.discarded_count == 0
        assert result.unmatched_extraction_ids == []

    def test_multiple_extractions_attributed_by_index_not_order(self):
        # Model returns validations out of order — attribution must follow
        # extraction_index, not list position.
        result = _run(
            {
                "validations": [
                    {"extraction_index": 1, "is_valid": True, "accuracy_score": 0.9},
                    {"extraction_index": 0, "is_valid": False, "accuracy_score": 0.2},
                ]
            },
            extractions=[{"a": 1}, {"a": 2}],
            extraction_ids=[201, 202],
        )
        by_id = {r["extraction_id"]: r for r in result.results}
        assert by_id[201]["accuracy_score"] == 0.2
        assert by_id[201]["is_valid"] is False
        assert by_id[202]["accuracy_score"] == 0.9
        assert by_id[202]["is_valid"] is True


class TestMissingExtractionIndex:
    def test_missing_index_is_discarded_not_guessed(self):
        # Old behavior: fell back to len(results), silently attributing to
        # whichever extraction happened to be processed next. New behavior:
        # discard the item rather than guess.
        result = _run(
            {
                "validations": [
                    {"is_valid": True, "accuracy_score": 1.0},  # no extraction_index
                ]
            },
            extractions=[{"a": 1}],
            extraction_ids=[301],
        )
        # The only validation was unattributable -> nothing usable -> fail closed
        assert result.status == "failed"
        assert result.results == []
        assert result.discarded_count == 1

    def test_one_missing_one_present_only_present_is_kept(self):
        result = _run(
            {
                "validations": [
                    {"is_valid": True, "accuracy_score": 1.0},  # missing index -> discarded
                    {"extraction_index": 1, "is_valid": True, "accuracy_score": 0.8},
                ]
            },
            extractions=[{"a": 1}, {"a": 2}],
            extraction_ids=[401, 402],
        )
        assert result.status == "completed"
        assert result.discarded_count == 1
        assert len(result.results) == 1
        assert result.results[0]["extraction_id"] == 402
        # extraction 401 was never actually validated
        assert result.unmatched_extraction_ids == [401]


class TestOutOfRangeAndDuplicateIndex:
    def test_out_of_range_index_discarded(self):
        result = _run(
            {"validations": [{"extraction_index": 5, "is_valid": True, "accuracy_score": 1.0}]},
            extractions=[{"a": 1}],
            extraction_ids=[501],
        )
        assert result.status == "failed"
        assert result.discarded_count == 1

    def test_negative_index_discarded(self):
        result = _run(
            {"validations": [{"extraction_index": -1, "is_valid": True, "accuracy_score": 1.0}]},
            extractions=[{"a": 1}],
            extraction_ids=[502],
        )
        assert result.status == "failed"
        assert result.discarded_count == 1

    def test_duplicate_index_second_occurrence_discarded(self):
        # Two validation items both claim extraction_index 0 — the second
        # (which would otherwise silently overwrite the first's tier
        # recompute) must be dropped, not merged or last-write-wins.
        result = _run(
            {
                "validations": [
                    {"extraction_index": 0, "is_valid": True, "accuracy_score": 0.9},
                    {"extraction_index": 0, "is_valid": False, "accuracy_score": 0.1},
                ]
            },
            extractions=[{"a": 1}],
            extraction_ids=[601],
        )
        assert result.status == "completed"
        assert len(result.results) == 1
        assert result.results[0]["accuracy_score"] == 0.9  # first occurrence wins
        assert result.discarded_count == 1

    def test_bool_is_not_accepted_as_index(self):
        # json.loads(...) can hand back a bool for extraction_index if the
        # model emits `true`/`false`; bool is a subclass of int in Python and
        # must not silently pass as a valid index (True == 1).
        result = _run(
            {"validations": [{"extraction_index": True, "is_valid": True, "accuracy_score": 1.0}]},
            extractions=[{"a": 1}, {"a": 2}],
            extraction_ids=[701, 702],
        )
        assert result.status == "failed"
        assert result.discarded_count == 1


class TestMissingAccuracyScore:
    def test_missing_score_does_not_default_to_perfect(self):
        result = _run(
            {"validations": [{"extraction_index": 0, "is_valid": True}]},  # no accuracy_score
            extractions=[{"a": 1}],
            extraction_ids=[801],
        )
        assert result.status == "completed"
        assert result.results[0]["accuracy_score"] == 0.5
        assert result.results[0]["score_missing"] is True

    def test_present_score_is_used_verbatim(self):
        result = _run(
            {"validations": [{"extraction_index": 0, "is_valid": True, "accuracy_score": 0.77}]},
            extractions=[{"a": 1}],
            extraction_ids=[802],
        )
        assert result.results[0]["accuracy_score"] == 0.77
        assert result.results[0]["score_missing"] is False


class TestNoExtractions:
    def test_empty_extractions_list_skips_call(self):
        with patch(
            "src.agents.cross_validation.get_extraction_provider",
        ) as mock_get_provider:
            result = run_cross_validation(
                passage_text=PASSAGE,
                extractions=[],
                passage_record_id=1,
            )
        mock_get_provider.assert_not_called()
        assert result.status == "skipped"


class TestParseAndCallFailures:
    def test_malformed_json_fails_closed(self):
        result = _run(
            "not json at all {{{",
            extractions=[{"a": 1}],
            extraction_ids=[901],
        )
        assert result.status == "failed"
        assert result.avg_accuracy_score == 0.0
        assert result.results == []

    def test_provider_exception_fails_closed(self):
        provider = MagicMock()
        provider.call.side_effect = RuntimeError("connection refused")
        with patch(
            "src.agents.cross_validation.get_extraction_provider",
            return_value=provider,
        ):
            result = run_cross_validation(
                passage_text=PASSAGE,
                extractions=[{"a": 1}],
                passage_record_id=1,
                extraction_ids=[1001],
            )
        assert result.status == "failed"
        assert result.results == []
