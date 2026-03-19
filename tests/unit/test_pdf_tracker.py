"""Unit tests for the Orrick PDF tracker parser."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.pdf_tracker import (
    PDFParseError,
    STATE_CODES,
    _normalize_scope,
    _parse_effective_date,
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
