"""Tests for PNE-3a law-level covered-entity rollup."""

from __future__ import annotations

from src.core.law_summary import (
    LAW_SUMMARY_ID_BASE,
    build_law_summary,
    build_law_summary_payload,
    build_law_summary_row,
)


class TestBuildLawSummary:
    def test_min_employees_is_smallest_floor(self):
        exts = [
            {"extraction_type": "threshold", "payload": {
                "threshold_type": "numeric", "threshold_value": "500",
                "threshold_unit": "employees", "threshold_condition": "more than 500 employees"}},
            {"extraction_type": "threshold", "payload": {
                "threshold_type": "numeric", "threshold_value": "50",
                "threshold_unit": "employees", "threshold_condition": "at least 50 employees"}},
        ]
        assert build_law_summary(exts)["min_employees"] == 50

    def test_min_revenue_parsed(self):
        exts = [
            {"extraction_type": "threshold", "payload": {
                "threshold_value": "$25 million",
                "threshold_condition": "revenue over $25 million"}},
        ]
        assert build_law_summary(exts)["min_revenue"] == 25_000_000.0

    def test_consumer_count_trigger(self):
        exts = [
            {"extraction_type": "threshold", "payload": {
                "threshold_value": "100,000",
                "threshold_condition": "100,000 or more consumers"}},
        ]
        assert build_law_summary(exts)["consumer_count_trigger"] == 100_000

    def test_small_business_exempt_from_exception(self):
        exts = [
            {"extraction_type": "exception", "payload": {
                "exceptions": [{"exception_type": "carve-out",
                                "description": "Small businesses are exempt"}]}},
        ]
        assert build_law_summary(exts)["small_business_exempt"] is True

    def test_private_right_of_action_true_when_any_asserts(self):
        exts = [
            {"extraction_type": "obligation", "payload": {
                "enforcement": {"private_right_of_action": True}}},
            {"extraction_type": "obligation", "payload": {
                "enforcement": {"private_right_of_action": False}}},
        ]
        # A single positive assertion wins over a False elsewhere.
        assert build_law_summary(exts)["private_right_of_action"] is True

    def test_private_right_of_action_false_only_when_explicit(self):
        exts = [
            {"extraction_type": "obligation", "payload": {
                "enforcement": {"private_right_of_action": False}}},
        ]
        assert build_law_summary(exts)["private_right_of_action"] is False

    def test_booleans_none_when_no_signal(self):
        # The honesty rule: absence is never coerced to False (avoids the
        # legal-overclaim class the coverage audit flagged).
        exts = [{"extraction_type": "obligation", "payload": {"action": "notify"}}]
        s = build_law_summary(exts)
        assert s["private_right_of_action"] is None
        assert s["small_business_exempt"] is None
        assert s["min_employees"] is None
        assert s["min_revenue"] is None
        assert s["consumer_count_trigger"] is None

    def test_bill_level_private_right_of_action(self):
        # Bill-level enforcement payload carries PROA at the top level, not
        # under an "enforcement" sub-dict.
        exts = [
            {"extraction_type": "enforcement", "payload": {
                "private_right_of_action": True}},
        ]
        assert build_law_summary(exts)["private_right_of_action"] is True

    def test_non_numeric_threshold_ignored_for_floor(self):
        exts = [
            {"extraction_type": "threshold", "payload": {
                "threshold_type": "entity_type", "threshold_value": "high-risk systems",
                "threshold_condition": "applies to high-risk systems"}},
        ]
        # No numeric employee/revenue floor should be fabricated.
        s = build_law_summary(exts)
        assert s["min_employees"] is None
        assert s["min_revenue"] is None

    def test_provenance_records_contributors(self):
        exts = [
            {"extraction_type": "threshold", "payload": {
                "threshold_value": "50", "threshold_unit": "employees",
                "threshold_condition": "at least 50 employees"}},
            {"extraction_type": "obligation", "payload": {
                "enforcement": {"private_right_of_action": True}}},
        ]
        prov = build_law_summary(exts)["_provenance"]
        assert prov["min_employees_from"] == ["employee_count"]
        assert prov["private_right_of_action_from"] == ["obligation"]

    def test_empty_input(self):
        s = build_law_summary([])
        assert all(
            s[k] is None
            for k in ("min_employees", "min_revenue", "consumer_count_trigger",
                      "small_business_exempt", "private_right_of_action")
        )


class TestBuildLawSummaryPayload:
    def test_merges_rollup_and_authority(self):
        payload = build_law_summary_payload(
            extractions=[{"extraction_type": "obligation",
                          "payload": {"enforcement": {"private_right_of_action": True}}}],
            bill_number="SB 205",
            title="Colorado AI Act",
            source_url="https://leg.colorado.gov/x",
        )
        # Rollup field...
        assert payload["private_right_of_action"] is True
        # ...and authority field in the same payload.
        assert payload["authority_type"] == "statute"
        assert payload["binding_effect"] == "binding"


class TestBuildLawSummaryRow:
    def test_synthetic_id_scheme(self):
        row = build_law_summary_row(
            family_id=42, law_id=7, jurisdiction_code="CO", extractions=[]
        )
        assert row["system_a_extraction_id"] == LAW_SUMMARY_ID_BASE + 42
        assert row["extraction_type"] == "law_summary"

    def test_not_null_columns_populated(self):
        # synced_extractions requires these columns NOT NULL — the synthetic row
        # must supply all of them so the insert can't fail.
        row = build_law_summary_row(
            family_id=1, law_id=1, jurisdiction_code="CA", extractions=[]
        )
        assert row["jurisdiction_code"] == "CA"
        assert row["evidence_spans"] == []
        assert row["confidence_score"] == 1.0
        assert row["confidence_tier"] == "A"
        assert row["payload"]  # non-empty dict

    def test_authority_from_metadata(self):
        row = build_law_summary_row(
            family_id=3, law_id=9, jurisdiction_code="US",
            extractions=[],
            title="NIST AI Guidance", source_url=None, bill_number=None,
        )
        assert row["payload"]["authority_type"] == "guidance"
        assert row["payload"]["needs_review"] is False

    def test_unknown_authority_flags_review(self):
        row = build_law_summary_row(
            family_id=4, law_id=10, jurisdiction_code="US", extractions=[]
        )
        assert row["payload"]["authority_type"] == "unknown"
        assert row["payload"]["needs_review"] is True
