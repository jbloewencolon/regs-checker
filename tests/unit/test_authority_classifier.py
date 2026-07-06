"""Tests for PNE-3b authority-type classification."""

from __future__ import annotations

from src.core.authority_classifier import classify_authority


class TestClassifyAuthority:
    def test_bill_number_is_statute(self):
        r = classify_authority("SB 205", "Colorado AI Act", "https://leg.colorado.gov/x")
        assert r["authority_type"] == "statute"
        assert r["binding_effect"] == "binding"
        assert r["authority_confidence"] == "high"
        assert r["needs_review"] is False

    def test_various_bill_prefixes(self):
        for bn in ["HB 1234", "AB 331", "SF 262", "LD 1705", "S.2024", "H 45"]:
            assert classify_authority(bn, None, None)["authority_type"] == "statute"

    def test_guidance_title_overrides_bill_number(self):
        # A bill number in tracker metadata must not label a guidance doc as a
        # statute — the non-statute title wins.
        r = classify_authority("SB 1", "Agency Guidance on AI Use", None)
        assert r["authority_type"] == "guidance"
        assert r["binding_effect"] == "non_binding"

    def test_executive_order(self):
        r = classify_authority(None, "Executive Order on Safe AI", None)
        assert r["authority_type"] == "executive_order"
        assert r["binding_effect"] == "binding"

    def test_ordinance(self):
        assert classify_authority(None, "City AI Ordinance", None)["authority_type"] == "ordinance"

    def test_regulation(self):
        r = classify_authority(None, "Final Rule on Automated Decision Systems", None)
        assert r["authority_type"] == "regulation"

    def test_proposed_rule_is_not_binding(self):
        r = classify_authority(None, "Proposed Rule on Automated Systems", None)
        assert r["authority_type"] == "regulation"
        assert r["binding_effect"] == "proposed"

    def test_court_opinion(self):
        r = classify_authority(None, "Smith v. Acme Corp", None)
        assert r["authority_type"] == "court_opinion"

    def test_url_fallback_statute(self):
        r = classify_authority(None, "Some Untitled Measure", "https://capitol.texas.gov/bill")
        assert r["authority_type"] == "statute"
        assert r["authority_confidence"] == "medium"

    def test_federal_register_proposed(self):
        r = classify_authority(None, None, "https://federalregister.gov/2026/proposed/x")
        assert r["authority_type"] == "regulation"
        assert r["binding_effect"] == "proposed"

    def test_no_signal_is_unknown_and_needs_review(self):
        r = classify_authority(None, None, None)
        assert r["authority_type"] == "unknown"
        assert r["binding_effect"] == "unknown"
        assert r["needs_review"] is True
        assert r["authority_confidence"] == "low"

    def test_unrecognized_title_is_unknown(self):
        r = classify_authority(None, "A Measure Concerning Widgets", None)
        assert r["authority_type"] == "unknown"
        assert r["needs_review"] is True
