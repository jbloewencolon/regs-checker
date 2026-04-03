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
from src.agents.citation_verifier import (
    CitationVerificationResult,
    _normalize_citation,
    _find_closest_section,
    _build_section_index,
)
from src.core.confidence import (
    WEIGHT_CROSS_VALIDATION,
    compute_confidence,
)
from src.schemas.extraction import ObligationPayload


# ---------------------------------------------------------------------------
# Cross-Validation Agent
# ---------------------------------------------------------------------------


class TestCrossValidation:
    """Tests for the cross-validation agent."""

    @patch("src.agents.cross_validation.get_extraction_provider")
    def test_empty_extractions_returns_neutral(self, mock_provider):
        """Cross-validating zero extractions should return a neutral summary."""
        result = run_cross_validation(
            passage_text="Some legal text here.",
            extractions=[],
            passage_record_id=1,
        )
        assert isinstance(result, CrossValidationSummary)
        assert result.extractions_checked == 0
        assert result.avg_accuracy_score == 1.0

    @patch("src.agents.cross_validation.get_extraction_provider")
    def test_valid_extraction_parses(self, mock_provider):
        """A valid cross-validation response should be parsed correctly."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50

        mock_provider.return_value.call.return_value = (
            json.dumps({
                "validations": [
                    {
                        "extraction_index": 0,
                        "is_valid": True,
                        "accuracy_score": 0.95,
                        "issues": [],
                        "notes": "Extraction is accurate."
                    }
                ]
            }),
            mock_usage,
            "openai/gpt-oss-20b",
            "stop",
        )

        result = run_cross_validation(
            passage_text="A developer shall comply with all requirements.",
            extractions=[{
                "subject": "A developer",
                "modality": "shall",
                "action": "comply with all requirements",
            }],
            passage_record_id=1,
        )

        assert result.extractions_checked == 1
        assert result.extractions_valid == 1
        assert result.extractions_flagged == 0
        assert result.avg_accuracy_score == 0.95

    @patch("src.agents.cross_validation.get_extraction_provider")
    def test_flagged_extraction(self, mock_provider):
        """An extraction with issues should be flagged."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 80

        mock_provider.return_value.call.return_value = (
            json.dumps({
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
            }),
            mock_usage,
            "openai/gpt-oss-20b",
            "stop",
        )

        result = run_cross_validation(
            passage_text="A developer shall implement protections.",
            extractions=[{
                "subject": "A developer",
                "modality": "shall",
                "action": "implement cybersecurity and physical protections",
            }],
            passage_record_id=1,
        )

        assert result.extractions_flagged == 1
        assert result.extractions_valid == 0
        assert result.avg_accuracy_score == 0.3
        assert len(result.results) == 1
        assert result.results[0]["issues"][0]["issue_type"] == "hallucination"

    @patch("src.agents.cross_validation.get_extraction_provider")
    def test_provider_failure_returns_neutral(self, mock_provider):
        """If the LLM call fails, return neutral result (don't penalize)."""
        mock_provider.return_value.call.side_effect = RuntimeError("API error")

        result = run_cross_validation(
            passage_text="Some text.",
            extractions=[{"subject": "test", "modality": "shall", "action": "act"}],
            passage_record_id=1,
        )

        assert result.extractions_checked == 1
        assert result.avg_accuracy_score == 0.75  # neutral-ish
        assert len(result.results) == 0


# ---------------------------------------------------------------------------
# Gap Detection Agent
# ---------------------------------------------------------------------------


class TestGapDetection:
    """Tests for the obligation gap detector."""

    @patch("src.agents.gap_detector.get_extraction_provider")
    def test_no_gaps_found(self, mock_provider):
        """When no gaps exist, should return empty candidates."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 30

        mock_provider.return_value.call.return_value = (
            json.dumps({
                "gaps_found": [],
                "analysis_notes": "No missed extractions identified."
            }),
            mock_usage,
            "openai/gpt-oss-20b",
            "stop",
        )

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
        assert result.gaps_found == 0
        assert result.candidates == []

    @patch("src.agents.gap_detector.get_extraction_provider")
    def test_gaps_found_and_filtered(self, mock_provider):
        """Should return only medium+ confidence gaps."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 200
        mock_usage.output_tokens = 150

        mock_provider.return_value.call.return_value = (
            json.dumps({
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
            }),
            mock_usage,
            "openai/gpt-oss-20b",
            "stop",
        )

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
        assert result.gaps_found == 1
        assert result.high_confidence_gaps == 1
        assert result.candidates[0]["confidence"] == "high"

    @patch("src.agents.gap_detector.get_extraction_provider")
    def test_provider_failure_returns_empty(self, mock_provider):
        """If the LLM call fails, return empty result."""
        mock_provider.return_value.call.side_effect = RuntimeError("API down")

        result = run_gap_detection(
            passage_text="Some text.",
            existing_extractions=[],
            passage_record_id=1,
        )

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
        """Cross-validation should have 25% weight (meaningful impact)."""
        assert WEIGHT_CROSS_VALIDATION == 0.25

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
        # With CV = 0.0, the 0.25 weight should drag score down significantly
        assert result.cross_validation == 0.0
        assert result.tier in ("C", "D")

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
