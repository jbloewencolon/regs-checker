"""Unit tests for the IAPP AI Legislation Tracker scraper."""

from __future__ import annotations

import pytest

from src.ingestion.iapp_scraper import (
    IAPPScraperError,
    STATE_CODES,
    _build_column_map,
    _normalize_status,
    _resolve_state_code,
    scrape_tracker,
)


# ---------------------------------------------------------------------------
# _normalize_status
# ---------------------------------------------------------------------------


class TestNormalizeStatus:
    def test_enacted_exact(self):
        assert _normalize_status("enacted") == "enacted"

    def test_signed_by_governor(self):
        assert _normalize_status("Signed by Governor") == "enacted"

    def test_in_effect(self):
        assert _normalize_status("In Effect") == "active"

    def test_introduced(self):
        assert _normalize_status("Introduced") == "introduced"

    def test_in_committee(self):
        assert _normalize_status("In Committee") == "pending"

    def test_passed_house(self):
        assert _normalize_status("Passed House") == "passed_one_chamber"

    def test_passed_senate(self):
        assert _normalize_status("Passed Senate") == "passed_one_chamber"

    def test_dead(self):
        assert _normalize_status("Dead") == "dead"

    def test_failed(self):
        assert _normalize_status("Failed") == "dead"

    def test_died_in_committee(self):
        assert _normalize_status("Died in Committee") == "dead"

    def test_vetoed(self):
        assert _normalize_status("Vetoed") == "vetoed"

    def test_withdrawn(self):
        assert _normalize_status("Withdrawn") == "withdrawn"

    def test_compound_status(self):
        assert _normalize_status("Passed House; In Senate Committee") == "passed_one_chamber"

    def test_unknown_defaults_to_pending(self):
        assert _normalize_status("Something Weird") == "pending"

    def test_empty_defaults_to_pending(self):
        assert _normalize_status("") == "pending"

    def test_chaptered(self):
        assert _normalize_status("Chaptered") == "enacted"

    def test_tabled(self):
        assert _normalize_status("Tabled") == "dead"

    def test_carried_over(self):
        assert _normalize_status("Carried Over") == "pending"


# ---------------------------------------------------------------------------
# _resolve_state_code
# ---------------------------------------------------------------------------


class TestResolveStateCode:
    def test_full_name(self):
        assert _resolve_state_code("California") == "CA"

    def test_abbreviation(self):
        assert _resolve_state_code("TX") == "TX"

    def test_lowercase_abbreviation(self):
        assert _resolve_state_code("ny") == "NY"

    def test_partial_name(self):
        assert _resolve_state_code("New York State") == "NY"

    def test_unknown_returns_empty(self):
        assert _resolve_state_code("Atlantis") == ""

    def test_whitespace_stripped(self):
        assert _resolve_state_code("  Colorado  ") == "CO"


# ---------------------------------------------------------------------------
# _build_column_map
# ---------------------------------------------------------------------------


class TestBuildColumnMap:
    def test_standard_headers(self):
        headers = ["state", "bill number", "status", "bill title", "ai topic", "last action", "effective date"]
        col_map = _build_column_map(headers)
        assert col_map["state"] == 0
        assert col_map["bill_number"] == 1
        assert col_map["status"] == 2
        assert col_map["bill_title"] == 3
        assert col_map["ai_topic"] == 4
        assert col_map["last_action"] == 5
        assert col_map["effective_date"] == 6

    def test_alternative_headers(self):
        headers = ["state", "bill", "status", "subject", "date"]
        col_map = _build_column_map(headers)
        assert col_map["state"] == 0
        assert col_map["bill_number"] == 1
        assert col_map["status"] == 2
        assert col_map["ai_topic"] == 3
        assert col_map["effective_date"] == 4


# ---------------------------------------------------------------------------
# scrape_tracker (with mock HTML)
# ---------------------------------------------------------------------------


MOCK_IAPP_HTML = """
<html><body>
<table>
<thead>
<tr>
    <th>State</th>
    <th>Bill Number</th>
    <th>Bill Title</th>
    <th>Status</th>
    <th>AI Topic</th>
    <th>Last Action</th>
    <th>Effective Date</th>
</tr>
</thead>
<tbody>
<tr>
    <td>Colorado</td>
    <td><a href="https://leg.colorado.gov/bills/sb24-205">SB 205</a></td>
    <td>AI Consumer Protections</td>
    <td>Enacted</td>
    <td>Automated Decision-Making</td>
    <td>Signed by Governor 5/17/2024</td>
    <td>2/1/2026</td>
</tr>
<tr>
    <td>California</td>
    <td><a href="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202320240SB1047">SB 1047</a></td>
    <td>Safe and Secure AI</td>
    <td>Vetoed</td>
    <td>AI Safety</td>
    <td>Vetoed by Governor 9/29/2024</td>
    <td></td>
</tr>
<tr>
    <td>Texas</td>
    <td>HB 2060</td>
    <td>AI Governance</td>
    <td>In Committee</td>
    <td>AI Governance</td>
    <td>Referred to Innovation Committee</td>
    <td></td>
</tr>
</tbody>
</table>
</body></html>
"""


class TestScrapeTracker:
    def test_parses_mock_html(self):
        from unittest.mock import MagicMock, patch

        mock_response = MagicMock()
        mock_response.content = MOCK_IAPP_HTML.encode()
        mock_response.status_code = 200

        with patch("src.ingestion.iapp_scraper.httpx.get", return_value=mock_response):
            records = scrape_tracker(url="https://example.com/mock")

        assert len(records) == 3

        co = next(r for r in records if r["state_code"] == "CO")
        assert co["bill_number"] == "SB 205"
        assert co["normalized_status"] == "enacted"
        assert co["bill_url"] == "https://leg.colorado.gov/bills/sb24-205"

        ca = next(r for r in records if r["state_code"] == "CA")
        assert ca["normalized_status"] == "vetoed"

        tx = next(r for r in records if r["state_code"] == "TX")
        assert tx["normalized_status"] == "pending"

    def test_no_table_raises_error(self):
        from unittest.mock import MagicMock, patch

        mock_response = MagicMock()
        mock_response.content = b"<html><body><p>No table here</p></body></html>"
        mock_response.status_code = 200

        with patch("src.ingestion.iapp_scraper.httpx.get", return_value=mock_response):
            with pytest.raises(IAPPScraperError):
                scrape_tracker(url="https://example.com/mock")


# ---------------------------------------------------------------------------
# STATE_CODES completeness
# ---------------------------------------------------------------------------


class TestStateCodes:
    def test_all_50_states_plus_dc_pr(self):
        assert len(STATE_CODES) == 52
        assert "DC" in STATE_CODES.values()
        assert "PR" in STATE_CODES.values()
