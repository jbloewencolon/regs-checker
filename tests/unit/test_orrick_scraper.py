"""Unit tests for the Orrick AI Law Tracker scraper."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.orrick_scraper import (
    OrrickScraperError,
    STATE_CODES,
    TRACKER_URL,
    _normalize_scope,
    _parse_effective_date,
    scrape_tracker,
    seed_from_tracker,
)

# Minimal HTML fixture that mimics the Orrick tracker table structure
MOCK_TRACKER_HTML = """
<html><body>
<table id="tablepress-1">
<thead>
<tr>
    <th>State/Territory</th>
    <th>AI Scope</th>
    <th>Relevant Law</th>
    <th>Law Link</th>
    <th>Effective Date</th>
    <th>Key Requirements</th>
    <th>Enforcements &amp; Penalties</th>
</tr>
</thead>
<tbody>
<tr>
    <td>Colorado</td>
    <td>Automated Decision-Making</td>
    <td>SB 205</td>
    <td><a href="https://leg.colorado.gov/bills/sb24-205">SB 205</a></td>
    <td>2/1/2026</td>
    <td>Impact assessments for high-risk AI systems</td>
    <td>AG enforcement; deceptive trade practice</td>
</tr>
<tr>
    <td>California</td>
    <td>AI Transparency</td>
    <td>AB 2885</td>
    <td><a href="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202320240AB2885">AB 2885</a></td>
    <td>1/1/2025</td>
    <td>Establishes uniform AI definition</td>
    <td>N/A</td>
</tr>
<tr>
    <td>Illinois</td>
    <td>AI Employment</td>
    <td>HB 3773</td>
    <td><a href="https://www.ilga.gov/legislation/billstatus.asp?DocNum=3773&GAID=17&GA=103&DocTypeID=HB">HB 3773</a></td>
    <td>1/1/2026</td>
    <td>Prohibits AI-driven discrimination in employment</td>
    <td>Existing civil rights enforcement</td>
</tr>
</tbody>
</table>
</body></html>
"""


class TestParseEffectiveDate:
    def test_slash_format(self):
        assert _parse_effective_date("10/1/2024") == date(2024, 10, 1)

    def test_slash_format_short_year(self):
        assert _parse_effective_date("1/1/25") == date(2025, 1, 1)

    def test_long_month_format(self):
        assert _parse_effective_date("January 1, 2025") == date(2025, 1, 1)

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

    def test_discrimination(self):
        assert _normalize_scope("Algorithmic Discrimination") == "ai_discrimination"

    def test_transparency(self):
        assert _normalize_scope("AI Transparency") == "ai_transparency"

    def test_automated_decision(self):
        assert _normalize_scope("Automated Decision-Making") == "automated_decision_making"

    def test_employment(self):
        assert _normalize_scope("AI Employment/Hiring") == "ai_employment"

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


class TestScrapeTracker:
    @patch("src.ingestion.orrick_scraper.httpx.get")
    def test_parses_table_rows(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = MOCK_TRACKER_HTML.encode()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        records = scrape_tracker()

        assert len(records) == 3

        # Colorado row
        co = records[0]
        assert co["state"] == "Colorado"
        assert co["state_code"] == "CO"
        assert co["ai_scope"] == "Automated Decision-Making"
        assert co["law_name"] == "SB 205"
        assert "colorado.gov" in co["law_url"]
        assert co["effective_date"] == "2/1/2026"

        # California row
        ca = records[1]
        assert ca["state_code"] == "CA"
        assert ca["law_name"] == "AB 2885"
        assert "leginfo.legislature.ca.gov" in ca["law_url"]

        # Illinois row
        il = records[2]
        assert il["state_code"] == "IL"
        assert il["law_name"] == "HB 3773"

    @patch("src.ingestion.orrick_scraper.httpx.get")
    def test_raises_on_missing_table(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"<html><body><p>No table here</p></body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with pytest.raises(OrrickScraperError, match="Could not find"):
            scrape_tracker()

    @patch("src.ingestion.orrick_scraper.httpx.get")
    def test_extracts_links_from_law_name_column_fallback(self, mock_get):
        """Test link extraction when link is in Relevant Law column, not Law Link."""
        html = """
        <html><body>
        <table id="tablepress-1">
        <thead><tr>
            <th>State/Territory</th><th>AI Scope</th>
            <th>Relevant Law</th><th>Law Link</th>
            <th>Effective Date</th><th>Key Requirements</th>
            <th>Enforcements</th>
        </tr></thead>
        <tbody><tr>
            <td>Texas</td><td>AI Governance</td>
            <td><a href="https://capitol.texas.gov/123">HB 2060</a></td>
            <td></td>
            <td>9/1/2025</td><td>Creates AI advisory council</td>
            <td>N/A</td>
        </tr></tbody>
        </table></body></html>
        """
        mock_response = MagicMock()
        mock_response.content = html.encode()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        records = scrape_tracker()
        assert len(records) == 1
        assert records[0]["law_url"] == "https://capitol.texas.gov/123"
        assert records[0]["state_code"] == "TX"


class TestSeedFromTracker:
    @patch("src.ingestion.orrick_scraper.httpx.get")
    def test_skips_rows_without_state_code(self, mock_get):
        """Rows with unknown state names should be skipped."""
        mock_db = MagicMock()
        records = [
            {"state": "Atlantis", "state_code": "", "law_name": "X", "law_url": "http://x",
             "ai_scope": "AI", "effective_date": "", "key_requirements": "", "enforcement": ""},
        ]
        jobs = seed_from_tracker(mock_db, records)
        assert len(jobs) == 0

    @patch("src.ingestion.orrick_scraper.httpx.get")
    def test_skips_rows_without_url(self, mock_get):
        """Rows with no bill link should be skipped."""
        mock_db = MagicMock()
        records = [
            {"state": "Colorado", "state_code": "CO", "law_name": "X", "law_url": "",
             "ai_scope": "AI", "effective_date": "", "key_requirements": "", "enforcement": ""},
        ]
        jobs = seed_from_tracker(mock_db, records)
        assert len(jobs) == 0
