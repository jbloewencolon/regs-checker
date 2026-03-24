"""IAPP US State AI Legislation Tracker scraper.

Scrapes the IAPP tracker at:
  https://iapp.org/resources/article/us-state-ai-governance-legislation-tracker/

The IAPP tracker publishes a table of US state AI bills with columns like:
  State | Bill Number | Status | Bill Title | AI Topic | Last Action | Effective Date

This module:
1. Fetches the tracker page and parses the legislation table
2. Extracts bill status (introduced, pending, enacted, dead, vetoed, etc.)
3. Returns structured records that the status checker can cross-reference
   against our existing database
"""

from __future__ import annotations

import re

import httpx
import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger()

IAPP_TRACKER_URL = (
    "https://iapp.org/resources/article/us-state-ai-governance-legislation-tracker/"
)

# Map IAPP status labels to our TemporalStatus values
STATUS_MAP: dict[str, str] = {
    # Active / enacted
    "enacted": "enacted",
    "signed": "enacted",
    "signed by governor": "enacted",
    "effective": "active",
    "in effect": "active",
    "chaptered": "enacted",
    "approved": "enacted",
    # Pre-enactment
    "introduced": "introduced",
    "pending": "pending",
    "in committee": "pending",
    "referred": "pending",
    "passed house": "passed_one_chamber",
    "passed senate": "passed_one_chamber",
    "passed assembly": "passed_one_chamber",
    "passed one chamber": "passed_one_chamber",
    "crossed over": "passed_one_chamber",
    "engrossed": "passed_one_chamber",
    "enrolled": "passed_one_chamber",
    # Dead / failed
    "dead": "dead",
    "failed": "dead",
    "tabled": "dead",
    "died": "dead",
    "died in committee": "dead",
    "stalled": "dead",
    "carried over": "pending",
    # Vetoed / withdrawn
    "vetoed": "vetoed",
    "withdrawn": "withdrawn",
}

from src.core.us_states import STATE_CODES  # noqa: F401


class IAPPScraperError(Exception):
    """Error during IAPP tracker scraping."""


def _normalize_status(raw_status: str) -> str:
    """Map a raw IAPP status string to our TemporalStatus value.

    Tries exact match first, then substring matching for compound statuses
    like "Passed House; In Senate Committee".
    """
    cleaned = raw_status.strip().lower()
    # Exact match
    if cleaned in STATUS_MAP:
        return STATUS_MAP[cleaned]

    # Substring matching (order matters — check terminal states first)
    for keyword, status in [
        ("veto", "vetoed"),
        ("dead", "dead"),
        ("failed", "dead"),
        ("died", "dead"),
        ("withdrawn", "withdrawn"),
        ("tabled", "dead"),
        ("signed", "enacted"),
        ("enacted", "enacted"),
        ("approved", "enacted"),
        ("chaptered", "enacted"),
        ("effective", "active"),
        ("in effect", "active"),
        ("passed house", "passed_one_chamber"),
        ("passed senate", "passed_one_chamber"),
        ("passed assembly", "passed_one_chamber"),
        ("crossed", "passed_one_chamber"),
        ("engrossed", "passed_one_chamber"),
        ("enrolled", "passed_one_chamber"),
        ("committee", "pending"),
        ("referred", "pending"),
        ("introduced", "introduced"),
        ("pending", "pending"),
    ]:
        if keyword in cleaned:
            return status

    logger.debug("iapp_unknown_status", raw=raw_status)
    return "pending"  # safe default for unknown


def _resolve_state_code(state_str: str) -> str:
    """Convert state name or abbreviation to 2-letter code."""
    state_str = state_str.strip()
    # Already a code?
    if len(state_str) == 2 and state_str.upper() in STATE_CODES.values():
        return state_str.upper()
    # Full name match
    code = STATE_CODES.get(state_str, "")
    if code:
        return code
    # Partial match
    for name, code in STATE_CODES.items():
        if name.lower() in state_str.lower():
            return code
    return ""


def scrape_tracker(url: str = IAPP_TRACKER_URL) -> list[dict]:
    """Fetch and parse the IAPP AI legislation tracker.

    Returns a list of dicts with keys:
        state, state_code, bill_number, bill_title, status, normalized_status,
        ai_topic, last_action, effective_date, bill_url
    """
    logger.info("scraping_iapp_tracker", url=url)

    response = httpx.get(url, follow_redirects=True, timeout=60.0, headers={
        "User-Agent": "regs-checker/0.1 (AI legislation research tool)",
    })
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "lxml")

    # IAPP uses standard HTML tables or embedded iframes with Google Sheets.
    # Try to find the main legislation table.
    table = _find_legislation_table(soup)
    if not table:
        raise IAPPScraperError(
            "Could not find the legislation tracker table on the IAPP page. "
            "The page structure may have changed — check the URL manually."
        )

    # Parse header row
    header_cells = table.find_all("th")
    if not header_cells:
        # Some tables use the first row as headers
        first_row = table.find("tr")
        if first_row:
            header_cells = first_row.find_all("td")

    headers = [h.get_text(strip=True).lower() for h in header_cells]
    col_map = _build_column_map(headers)
    logger.info("iapp_headers", headers=headers, col_map=col_map)

    # Parse data rows
    tbody = table.find("tbody") or table
    rows = tbody.find_all("tr")
    records = []

    for row in rows:
        cells = row.find_all("td")
        if not cells or len(cells) < 3:
            continue

        # Skip header-like rows
        first_cell_text = cells[0].get_text(strip=True).lower()
        if first_cell_text in headers or first_cell_text == "state":
            continue

        record = _parse_row(cells, col_map)
        if record and record["state_code"]:
            records.append(record)

    logger.info("iapp_tracker_parsed", total_rows=len(records))
    return records


def _find_legislation_table(soup: BeautifulSoup):
    """Find the legislation table in the IAPP page.

    IAPP sometimes embeds tables directly, sometimes via iframes.
    We look for tables with recognizable column headers.
    """
    target_keywords = {"state", "bill", "status"}

    for table in soup.find_all("table"):
        header_text = " ".join(
            th.get_text(strip=True).lower()
            for th in table.find_all("th")
        )
        if not header_text:
            first_row = table.find("tr")
            if first_row:
                header_text = " ".join(
                    td.get_text(strip=True).lower()
                    for td in first_row.find_all("td")
                )

        matches = sum(1 for kw in target_keywords if kw in header_text)
        if matches >= 2:
            return table

    return None


def _build_column_map(headers: list[str]) -> dict[str, int]:
    """Build a flexible column index map from header text."""
    col_map = {}
    for idx, h in enumerate(headers):
        if "state" in h and "state" not in col_map:
            col_map["state"] = idx
        elif "bill" in h and "number" in h:
            col_map["bill_number"] = idx
        elif "bill" in h and "title" in h:
            col_map["bill_title"] = idx
        elif h == "bill" or (h.startswith("bill") and "bill_number" not in col_map):
            col_map["bill_number"] = idx
        elif "status" in h and "status" not in col_map:
            col_map["status"] = idx
        elif "topic" in h or "subject" in h or "scope" in h:
            col_map["ai_topic"] = idx
        elif "action" in h or "last" in h:
            col_map["last_action"] = idx
        elif "effective" in h or "date" in h:
            col_map["effective_date"] = idx
        elif "title" in h and "bill_title" not in col_map:
            col_map["bill_title"] = idx
    return col_map


def _parse_row(cells, col_map: dict) -> dict | None:
    """Parse a single table row into a structured record."""

    def cell_text(key: str) -> str:
        idx = col_map.get(key)
        if idx is not None and idx < len(cells):
            return cells[idx].get_text(strip=True)
        return ""

    def cell_link(key: str) -> str:
        idx = col_map.get(key)
        if idx is not None and idx < len(cells):
            a_tag = cells[idx].find("a")
            if a_tag and a_tag.get("href"):
                return a_tag["href"].strip()
        return ""

    state_str = cell_text("state")
    state_code = _resolve_state_code(state_str)
    if not state_code:
        return None

    raw_status = cell_text("status")
    bill_number = cell_text("bill_number")

    # Try to get a link from the bill number or title cell
    bill_url = cell_link("bill_number") or cell_link("bill_title")

    return {
        "state": state_str,
        "state_code": state_code,
        "bill_number": bill_number,
        "bill_title": cell_text("bill_title"),
        "status": raw_status,
        "normalized_status": _normalize_status(raw_status),
        "ai_topic": cell_text("ai_topic"),
        "last_action": cell_text("last_action"),
        "effective_date": cell_text("effective_date"),
        "bill_url": bill_url,
    }
