"""Unit tests for the Orrick PDF tracker parser."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.pdf_tracker import (
    PDFParseError,
    STATE_CODES,
    _match_state_name,
    _normalize_scope,
    _parse_effective_date,
    _parse_table_rows,
    seed_from_tracker,
)


class TestParseEffectiveDate:
    def test_slash_format(self):
        assert _parse_effective_date("10/1/2024") == date(2024, 10, 1)

    def test_slash_format_short_year(self):
        assert _parse_effective_date("1/1/25") == date(2025, 1, 1)

    def test_long_month_format(self):
        assert _parse_effective_date("January 1, 2025") == date(2025, 1, 1)

    def test_october_format(self):
        assert _parse_effective_date("October 1, 2024") == date(2024, 10, 1)

    def test_tbd(self):
        assert _parse_effective_date("TBD") is None

    def test_na(self):
        assert _parse_effective_date("N/A") is None

    def test_empty(self):
        assert _parse_effective_date("") is None

    def test_embedded_date(self):
        assert _parse_effective_date("Effective 10/1/2024") == date(2024, 10, 1)


class TestNormalizeScope:
    def test_deepfake(self):
        assert _normalize_scope("AI Deepfakes") == "ai_content_safety"

    def test_csam(self):
        assert _normalize_scope("AI CSAM") == "ai_content_safety"

    def test_intimate(self):
        assert _normalize_scope("AI Intimate Images") == "ai_content_safety"

    def test_discrimination(self):
        assert _normalize_scope("Algorithmic Discrimination") == "ai_discrimination"

    def test_transparency(self):
        assert _normalize_scope("AI Transparency") == "ai_transparency"

    def test_automated_decision(self):
        assert _normalize_scope("Automated Decision-Making") == "automated_decision_making"

    def test_employment(self):
        assert _normalize_scope("AI Employment/Hiring") == "ai_employment"

    def test_political(self):
        assert _normalize_scope("AI in Political Advertising") == "ai_political_advertising"

    def test_ownership(self):
        assert _normalize_scope("AI Ownership") == "ai_ownership"

    def test_generic(self):
        assert _normalize_scope("General AI") == "artificial_intelligence"


class TestStateCodes:
    def test_all_50_states(self):
        assert len(STATE_CODES) >= 50

    def test_colorado(self):
        assert STATE_CODES["Colorado"] == "CO"

    def test_california(self):
        assert STATE_CODES["California"] == "CA"

    def test_dc(self):
        assert STATE_CODES["District of Columbia"] == "DC"


class TestMatchStateName:
    def test_exact_match(self):
        assert _match_state_name("California") == "California"

    def test_multiline_cell(self):
        assert _match_state_name("Colorado\n(cont.)") == "Colorado"

    def test_state_with_trailing_text(self):
        assert _match_state_name("New York something") == "New York"

    def test_unknown(self):
        assert _match_state_name("Atlantis") == ""

    def test_empty(self):
        assert _match_state_name("") == ""

    def test_dc(self):
        assert _match_state_name("District of Columbia") == "District of Columbia"


class TestParseTableRows:
    """Tests for the pdfplumber table-extraction based parser."""

    def _make_rows(self, data_rows):
        header = ["State/Terr", "AI Scope", "Relevant Law", "Law Link",
                  "Effective Date", "Key Requirements", "Enforcements Penalties"]
        return [header] + data_rows

    def test_single_record(self):
        rows = self._make_rows([
            ["California", "AI Transparency", "SB 1001", "SB 1001",
             "1/1/2026", "Requires disclosure", "AG enforcement"],
        ])
        records = _parse_table_rows(rows, ["https://example.com/sb1001"])
        assert len(records) == 1
        r = records[0]
        assert r["state"] == "California"
        assert r["state_code"] == "CA"
        assert r["ai_scope"] == "AI Transparency"
        assert r["law_name"] == "SB 1001"
        assert r["law_url"] == "https://example.com/sb1001"
        assert r["effective_date"] == "1/1/2026"

    def test_state_carries_forward(self):
        """Empty state cell means same state as previous row."""
        rows = self._make_rows([
            ["Colorado", "AI Governance", "SB 205", "SB 205",
             "2/1/2026", "Impact assessments", "DORA enforcement"],
            ["", "AI Transparency", "HB 1468", "HB 1468",
             "8/7/2024", "Disclosure", "AG action"],
        ])
        records = _parse_table_rows(rows, [
            "https://example.com/sb205",
            "https://example.com/hb1468",
        ])
        assert len(records) == 2
        assert records[0]["state"] == "Colorado"
        assert records[1]["state"] == "Colorado"
        assert records[1]["state_code"] == "CO"
        assert records[1]["law_name"] == "HB 1468"

    def test_skips_empty_content_rows(self):
        rows = self._make_rows([
            ["", "", "", "", "", "", ""],
            ["Texas", "AI Employment", "HB 2060", "HB 2060",
             "9/1/2025", "Notice required", "Civil penalty"],
        ])
        records = _parse_table_rows(rows, ["https://example.com/hb2060"])
        assert len(records) == 1
        assert records[0]["state"] == "Texas"

    def test_no_urls_still_produces_records(self):
        rows = self._make_rows([
            ["Illinois", "AI Discrimination", "HB 3773", "HB 3773",
             "1/1/2026", "Bias audit", "Private right of action"],
        ])
        records = _parse_table_rows(rows, [])
        assert len(records) == 1
        assert records[0]["law_url"] == ""


class TestSeedFromTracker:
    def test_skips_rows_without_state_code(self):
        """Rows with unknown state names should be skipped."""
        mock_db = MagicMock()
        records = [
            {"state": "Atlantis", "state_code": "", "law_name": "X", "law_url": "http://x",
             "ai_scope": "AI", "effective_date": "", "key_requirements": "", "enforcement": ""},
        ]
        jobs = seed_from_tracker(mock_db, records)
        assert len(jobs) == 0

    def test_skips_rows_without_url(self):
        """Rows with no bill link should be skipped."""
        mock_db = MagicMock()
        records = [
            {"state": "Colorado", "state_code": "CO", "law_name": "X", "law_url": "",
             "ai_scope": "AI", "effective_date": "", "key_requirements": "", "enforcement": ""},
        ]
        jobs = seed_from_tracker(mock_db, records)
        assert len(jobs) == 0
