"""Tests for jurisdiction cross-check validation."""

import pytest

from src.core.jurisdiction_check import (
    JurisdictionMismatch,
    check_jurisdiction_metadata,
    detect_jurisdiction_from_text,
    validate_extraction_jurisdiction,
)


class TestCheckJurisdictionMetadata:
    def test_matching_codes(self):
        assert check_jurisdiction_metadata("CO", "CO") is True

    def test_mismatched_codes(self):
        assert check_jurisdiction_metadata("NY", "CT") is False

    def test_case_insensitive(self):
        assert check_jurisdiction_metadata("co", "CO") is True

    def test_empty_expected(self):
        assert check_jurisdiction_metadata("", "CO") is True

    def test_empty_source(self):
        assert check_jurisdiction_metadata("CO", "") is True

    def test_both_empty(self):
        assert check_jurisdiction_metadata("", "") is True


class TestDetectJurisdictionFromText:
    def test_colorado_text(self):
        text = (
            "Colorado Revised Statutes § 6-1-1701. This section of the Colorado "
            "Consumer Protection Act requires that developers of high-risk artificial "
            "intelligence systems in the state of Colorado shall..."
        )
        assert detect_jurisdiction_from_text(text) == "CO"

    def test_connecticut_text(self):
        text = (
            "Connecticut General Statutes § 13b-116. Transportation network company "
            "pricing in the state of Connecticut. The Commissioner of Transportation "
            "for Connecticut shall establish..."
        )
        assert detect_jurisdiction_from_text(text) == "CT"

    def test_no_clear_signal(self):
        text = "This is a generic passage with no state-specific language."
        assert detect_jurisdiction_from_text(text) is None

    def test_weak_signal_below_threshold(self):
        text = "The term Colorado is mentioned once but nothing else."
        # Single mention should be below the threshold of 2
        assert detect_jurisdiction_from_text(text) is None

    def test_strong_signal_with_legislature_pattern(self):
        text = "South Carolina Code Ann. § 40-57 provides for licensing requirements."
        assert detect_jurisdiction_from_text(text) == "SC"


class TestValidateExtractionJurisdiction:
    def test_valid_metadata_match(self):
        result = validate_extraction_jurisdiction(
            expected_jurisdiction="CO",
            source_jurisdiction="CO",
            strict=False,
        )
        assert result["valid"] is True

    def test_metadata_mismatch_non_strict(self):
        result = validate_extraction_jurisdiction(
            expected_jurisdiction="NY",
            source_jurisdiction="CT",
            strict=False,
        )
        assert result["valid"] is False
        assert result["method"] == "metadata"

    def test_metadata_mismatch_strict_raises(self):
        with pytest.raises(JurisdictionMismatch) as exc_info:
            validate_extraction_jurisdiction(
                expected_jurisdiction="NY",
                source_jurisdiction="CT",
                strict=True,
            )
        assert exc_info.value.expected_code == "NY"
        assert exc_info.value.detected_code == "CT"

    def test_text_mismatch_detected(self):
        ct_text = (
            "Connecticut General Statutes § 13b-116. Transportation network "
            "company pricing in Connecticut. The state of Connecticut requires..."
        )
        result = validate_extraction_jurisdiction(
            expected_jurisdiction="NY",
            source_jurisdiction="NY",
            passage_text=ct_text,
            strict=False,
        )
        assert result["valid"] is False
        assert result["method"] == "text_signal"
        assert result["detected"] == "CT"

    def test_text_match_passes(self):
        co_text = (
            "Colorado Revised Statutes require that developers in Colorado "
            "comply with the Colorado AI Act provisions."
        )
        result = validate_extraction_jurisdiction(
            expected_jurisdiction="CO",
            source_jurisdiction="CO",
            passage_text=co_text,
            strict=False,
        )
        assert result["valid"] is True

    def test_document_family_id_in_error(self):
        with pytest.raises(JurisdictionMismatch) as exc_info:
            validate_extraction_jurisdiction(
                expected_jurisdiction="NY",
                source_jurisdiction="CT",
                document_family_id=159,
                strict=True,
            )
        assert exc_info.value.document_family_id == 159
