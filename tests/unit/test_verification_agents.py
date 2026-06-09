"""Unit tests for verification agent layers.

Tests cover:
  - CrossValidationAgent: result parsing, issue detection, graceful failure
  - GapDetector: gap identification, confidence filtering, graceful failure
  - CitationVerifier: section index building, citation matching, fuzzy matching
  - Confidence model: cross-validation score integration
  - Pipeline: VerificationResult dataclass
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.agents.cross_validation import (
    CrossValidationSummary,
    run_cross_validation,
)
from src.agents.gap_detector import (
    GapDetectionSummary,
    run_gap_detection,
)
from src.core.llm_provider import LLMResponse, LLMUsage


def _llm_response(payload: dict, input_tokens: int = 100, output_tokens: int = 50) -> LLMResponse:
    """Build a real LLMResponse exactly as a provider returns it.

    The verification agents consume ``provider.call(...).text`` etc. These tests
    deliberately use a genuine LLMResponse (not a tuple) so the mocked interface
    matches production. A regression to the old tuple-unpacking bug would make
    these tests fail instead of silently passing.
    """
    return LLMResponse(
        text=json.dumps(payload),
        usage=LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        model_id="openai/gpt-oss-20b",
        stop_reason="stop",
    )
from src.agents.citation_verifier import (
    CitationVerificationResult,
    _normalize_citation,
    _find_closest_section,
    _build_section_index,
)
from src.core.confidence import (
    WEIGHT_CV_TARGET,
    compute_confidence,
)
from src.schemas.extraction import ObligationPayload


# ---------------------------------------------------------------------------
# Cross-Validation Agent
# ---------------------------------------------------------------------------


class TestCrossValidation:
    """Tests for the cross-validation agent."""

    @patch("src.agents.cross_validation.get_extraction_provider")
    def test_empty_extractions_is_skipped(self, mock_provider):
        """Cross-validating zero extractions should be a 'skipped' (not failed) summary."""
        result = run_cross_validation(
            passage_text="Some legal text here.",
            extractions=[],
            passage_record_id=1,
        )
        assert isinstance(result, CrossValidationSummary)
        assert result.extractions_checked == 0
        assert result.avg_accuracy_score == 1.0
        assert result.status == "skipped"

    @patch("src.agents.cross_validation.get_extraction_provider")
    def test_valid_extraction_parses(self, mock_provider):
        """A valid cross-validation response should be parsed correctly."""
        mock_provider.return_value.call.return_value = _llm_response({
            "validations": [
                {
                    "extraction_index": 0,
                    "is_valid": True,
                    "accuracy_score": 0.95,
                    "issues": [],
                    "notes": "Extraction is accurate."
                }
            ]
        })

        result = run_cross_validation(
            passage_text="A developer shall comply with all requirements.",
            extractions=[{
                "subject": "A developer",
                "modality": "shall",
                "action": "comply with all requirements",
            }],
            passage_record_id=1,
        )

        assert result.status == "completed"
        assert result.extractions_checked == 1
        assert result.extractions_valid == 1
        assert result.extractions_flagged == 0
        assert result.avg_accuracy_score == 0.95

    @patch("src.agents.cross_validation.get_extraction_provider")
    def test_flagged_extraction(self, mock_provider):
        """An extraction with issues should be flagged."""
        mock_provider.return_value.call.return_value = _llm_response({
            "validations": [
                {
                    "extraction_index": 0,
                    "is_valid": False,
                    "accuracy_score": 0.3,
                    "issues": [
                        {
                            "issue_type": "hallucination",
                            "severity": "high",
                            "field_name": "action",
                            "explanation": "Action not supported by text",
                        }
                    ],
                }
            ]
        }, output_tokens=80)

        result = run_cross_validation(
            passage_text="A developer shall implement protections.",
            extractions=[{
                "subject": "A developer",
                "modality": "shall",
                "action": "implement cybersecurity and physical protections",
            }],
            passage_record_id=1,
        )

        assert result.status == "completed"
        assert result.extractions_flagged == 1
        assert result.extractions_valid == 0
        assert result.avg_accuracy_score == 0.3
        assert len(result.results) == 1
        assert result.results[0]["issues"][0]["issue_type"] == "hallucination"

    @patch("src.agents.cross_validation.get_extraction_provider")
    def test_provider_failure_fails_closed(self, mock_provider):
        """A failed LLM call must FAIL CLOSED, not return a neutral pass.

        Regression guard for the trust bug: failure must be an explicit
        "failed" status with empty results and NO neutral 0.75 accuracy that a
        caller could fold into a document average or use to raise confidence.
        """
        mock_provider.return_value.call.side_effect = RuntimeError("API error")

        result = run_cross_validation(
            passage_text="Some text.",
            extractions=[{"subject": "test", "modality": "shall", "action": "act"}],
            passage_record_id=1,
        )

        assert result.status == "failed"
        assert result.avg_accuracy_score != 0.75  # the old neutral sentinel is gone
        assert result.extractions_checked == 0    # not counted as a checked passage
        assert result.results == []

    @patch("src.agents.cross_validation.get_extraction_provider")
    def test_malformed_json_fails_closed(self, mock_provider):
        """Unparseable model output must also fail closed, not pass neutrally."""
        mock_provider.return_value.call.return_value = LLMResponse(
            text="this is not json at all",
            usage=LLMUsage(input_tokens=10, output_tokens=5),
            model_id="openai/gpt-oss-20b",
            stop_reason="stop",
        )

        result = run_cross_validation(
            passage_text="Some text.",
            extractions=[{"subject": "test", "modality": "shall", "action": "act"}],
            passage_record_id=1,
        )

        assert result.status == "failed"
        assert result.results == []


# ---------------------------------------------------------------------------
# Gap Detection Agent
# ---------------------------------------------------------------------------


class TestGapDetection:
    """Tests for the obligation gap detector."""

    @patch("src.agents.gap_detector.get_extraction_provider")
    def test_no_gaps_found(self, mock_provider):
        """When no gaps exist, should return empty candidates with completed status."""
        mock_provider.return_value.call.return_value = _llm_response({
            "gaps_found": [],
            "analysis_notes": "No missed extractions identified."
        }, output_tokens=30)

        result = run_gap_detection(
            passage_text="A developer shall implement protections.",
            existing_extractions=[{
                "subject": "A developer",
                "modality": "shall",
                "action": "implement protections",
            }],
            passage_record_id=1,
        )

        assert isinstance(result, GapDetectionSummary)
        assert result.status == "completed"
        assert result.gaps_found == 0
        assert result.candidates == []

    @patch("src.agents.gap_detector.get_extraction_provider")
    def test_gaps_found_and_filtered(self, mock_provider):
        """Should return only medium+ confidence gaps."""
        mock_provider.return_value.call.return_value = _llm_response({
            "gaps_found": [
                {
                    "extraction_type": "obligation",
                    "summary": "Second obligation in sentence",
                    "subject": "developer",
                    "action": "maintain records",
                    "modality": "shall",
                    "evidence_text": "and maintain records of compliance",
                    "why_missed": "Buried in compound sentence after first obligation",
                    "confidence": "high",
                },
                {
                    "extraction_type": "obligation",
                    "summary": "Possible implicit duty",
                    "subject": "deployer",
                    "action": "maybe something",
                    "modality": "may",
                    "evidence_text": "deployers may",
                    "why_missed": "Very unclear",
                    "confidence": "low",
                },
            ]
        }, input_tokens=200, output_tokens=150)

        result = run_gap_detection(
            passage_text="A developer shall implement protections and maintain records of compliance.",
            existing_extractions=[{
                "subject": "A developer",
                "modality": "shall",
                "action": "implement protections",
            }],
            passage_record_id=1,
        )

        # Only the "high" confidence gap should pass through
        assert result.status == "completed"
        assert result.gaps_found == 1
        assert result.high_confidence_gaps == 1
        assert result.candidates[0]["confidence"] == "high"

    @patch("src.agents.gap_detector.get_extraction_provider")
    def test_provider_failure_fails_closed(self, mock_provider):
        """A failed gap-detection call must FAIL CLOSED, not look like 'no gaps'.

        Regression guard: a failure returns status="failed" so the caller can
        route the passage to review instead of treating zero gaps as clean.
        """
        mock_provider.return_value.call.side_effect = RuntimeError("API down")

        result = run_gap_detection(
            passage_text="Some text.",
            existing_extractions=[],
            passage_record_id=1,
        )

        assert result.status == "failed"
        assert result.gaps_found == 0
        assert result.candidates == []


# ---------------------------------------------------------------------------
# Citation Verification
# ---------------------------------------------------------------------------


class TestCitationVerification:
    """Tests for the citation verification agent."""

    def test_normalize_citation(self):
        """Citation normalization should handle common prefixes."""
        assert _normalize_citation("Section 3") == "section 3"
        assert _normalize_citation("Sec. 3") == "section 3"
        assert _normalize_citation("§ 3") == "section 3"
        assert _normalize_citation("  SECTION  3  ") == "section 3"

    def test_find_closest_section_exact(self):
        """Exact match should find the section."""
        index = {"section 3": 1, "section 4": 2, "section 5 - definitions": 3}
        assert _find_closest_section("Section 3", index) == "section 3"

    def test_find_closest_section_prefix(self):
        """Prefix match should find longer section names."""
        index = {"section 3 - developer requirements": 1}
        assert _find_closest_section("Section 3", index) == "section 3 - developer requirements"

    def test_find_closest_section_number(self):
        """Number-based fallback should work."""
        index = {"part 1 > section 3 > (a)": 1}
        result = _find_closest_section("Section 3", index)
        assert result is not None
        assert "3" in result

    def test_find_closest_section_no_match(self):
        """Should return None when no match exists."""
        index = {"section 1": 1, "section 2": 2}
        assert _find_closest_section("Section 99", index) is None

    def test_find_closest_section_empty_index(self):
        """Should return None for empty index."""
        assert _find_closest_section("Section 3", {}) is None


# ---------------------------------------------------------------------------
# Confidence Model — Cross-Validation Integration
# ---------------------------------------------------------------------------


class TestConfidenceWithCrossValidation:
    """Tests for cross-validation score in the confidence model.

    The Orrick gate forces Tier D when no Orrick data is present. To test
    cross-validation's effect on scoring, we supply mock Orrick data so the
    gate doesn't mask the CV signal.
    """

    @staticmethod
    def _make_orrick():
        """Create a mock OrrickSimilarityResult that passes the gate."""
        sim = MagicMock()
        sim.has_orrick_data = True
        sim.combined_score = 0.30
        sim.matched_tokens = ["ai", "developer"]
        return sim

    def _base_kwargs(self):
        return dict(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
                {"field_name": "action", "text": "y", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "modality": "shall",
                "action": "comply",
                "jurisdiction": "CO",
            },
            schema_class=ObligationPayload,
            parse_quality_score=0.8,
            orrick_similarity=self._make_orrick(),
        )

    def test_excluded_when_not_verified(self):
        """Without cross-validation, component is excluded (weight redistributed)."""
        result = compute_confidence(**self._base_kwargs())
        assert result.cross_validation == 0.0

    def test_high_cv_score_boosts_confidence(self):
        """High cross-validation score should boost overall confidence."""
        base_result = compute_confidence(**self._base_kwargs())

        cv_result = compute_confidence(
            **self._base_kwargs(),
            cross_validation_score=1.0,
        )

        assert cv_result.cross_validation == 1.0
        assert cv_result.total_score > base_result.total_score

    def test_low_cv_score_reduces_confidence(self):
        """Low cross-validation score should reduce overall confidence."""
        base_result = compute_confidence(**self._base_kwargs())

        cv_result = compute_confidence(
            **self._base_kwargs(),
            cross_validation_score=0.2,
        )

        assert cv_result.cross_validation == 0.2
        assert cv_result.total_score < base_result.total_score

    def test_cv_weight_is_significant(self):
        """Cross-validation target weight should be 10% (phases in from 0.25 target)."""
        assert WEIGHT_CV_TARGET == 0.10

    def test_perfect_cv_can_reach_tier_a(self):
        """Perfect scores across all components including CV should reach tier A."""
        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
                {"field_name": "action", "text": "y", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "subject_normalized": "developer",
                "modality": "shall",
                "action": "comply",
                "object": "AI system",
                "condition": "when deployed",
                "jurisdiction": "CO",
                "section_reference": "Sec 3",
                "timeline": {"effective_date": "2025-01-01"},
                "enforcement": {"penalty_type": "fine"},
            },
            schema_class=ObligationPayload,
            parse_quality_score=1.0,
            orrick_similarity=self._make_orrick(),
            cross_validation_score=1.0,
        )
        assert result.tier == "A"
        assert result.total_score >= 0.85

    def test_failed_cv_pushes_to_lower_tier(self):
        """Failed cross-validation should push an otherwise-good extraction down."""
        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "modality": "shall",
                "action": "comply",
            },
            schema_class=ObligationPayload,
            parse_quality_score=0.7,
            orrick_similarity=self._make_orrick(),
            cross_validation_score=0.0,  # Failed cross-validation
        )
        # CV is 0.10 of the target model (phases in from base Orrick+evidence+citation).
        # A zero CV lowers the score but with strong Orrick+evidence the result is B or C.
        assert result.cross_validation == 0.0
        assert result.tier in ("B", "C")

    def test_orrick_gate_overrides_cv(self):
        """Without Orrick data, even perfect CV can't escape Tier D."""
        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "modality": "shall",
                "action": "comply",
            },
            schema_class=ObligationPayload,
            parse_quality_score=1.0,
            cross_validation_score=1.0,  # Perfect CV, no Orrick
        )
        assert result.tier == "D"
        assert result.orrick_gated is True
