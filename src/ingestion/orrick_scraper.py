"""Orrick AI Law Center scraper for discovering and seeding AI legislation.

Scrapes the Orrick "U.S. AI Law Tracker" table at:
  https://ai-law-center.orrick.com/us-ai-law-tracker-see-all-states/

The tracker is a TablePress table (id="tablepress-1") with columns:
  State/Territory | AI Scope | Relevant Law | Law Link | Effective Date |
  Key Requirements | Enforcements & Penalties

Each row represents one state AI law. This module:
1. Fetches and parses the tracker HTML with requests + BeautifulSoup
2. Converts each row into Source → DocumentFamily → DocumentVersion records
3. Follows bill links to fetch the actual bill text (PDFs / HTML)
4. Creates IngestionJobs for the Dagster pipeline to process

Replaces LegiScan connector while their API key pipeline is delayed.
"""

from __future__ import annotations

import re
from datetime import date, datetime

import httpx
import structlog
from bs4 import BeautifulSoup

from src.core.config import settings
from src.db.models import (
    DocumentFamily,
    DocumentVersion,
    IngestionJob,
    IngestionStatus,
    LegalEvent,
    LegalEventType,
    Source,
    TemporalStatus,
)

logger = structlog.get_logger()

TRACKER_URL = "https://ai-law-center.orrick.com/us-ai-law-tracker-see-all-states/"

# Map state names to two-letter codes
STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC", "Puerto Rico": "PR",
}


class OrrickScraperError(Exception):
    """Error during Orrick tracker scraping."""
    pass


def _parse_effective_date(date_str: str) -> date | None:
    """Parse effective date from various formats found in the tracker.

    Handles: "10/1/2024", "1/1/2025", "January 1, 2025", "TBD", etc.
    """
    if not date_str:
        return None
    date_str = date_str.strip()
    if date_str.lower() in ("tbd", "n/a", "pending", "varies", ""):
        return None

    # Try M/D/YYYY
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    # Try to find a date-like pattern in the string
    match = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", date_str)
    if match:
        for fmt in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(match.group(1), fmt).date()
            except ValueError:
                continue

    logger.debug("unparseable_date", raw=date_str)
    return None


def _extract_link(cell) -> tuple[str, str]:
    """Extract the first link's URL and text from a table cell.

    Returns (url, link_text). If no link, returns ("", cell_text).
    """
    a_tag = cell.find("a")
    if a_tag and a_tag.get("href"):
        return a_tag["href"].strip(), a_tag.get_text(strip=True)
    return "", cell.get_text(strip=True)


def scrape_tracker(url: str = TRACKER_URL) -> list[dict]:
    """Fetch and parse the Orrick AI law tracker table.

    Returns a list of dicts, one per table row, with keys:
        state, state_code, ai_scope, law_name, law_url, effective_date,
        key_requirements, enforcement
    """
    logger.info("scraping_orrick_tracker", url=url)

    response = httpx.get(url, follow_redirects=True, timeout=60.0, headers={
        "User-Agent": "regs-checker/0.1 (AI legislation research tool)",
    })
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "lxml")

    # Find the TablePress table — ID is "tablepress-1" per Orrick's setup
    table = soup.find("table", id=re.compile(r"tablepress-\d+"))
    if not table:
        # Fallback: find any table with the right column count
        tables = soup.find_all("table")
        for t in tables:
            headers = [th.get_text(strip=True).lower() for th in t.find_all("th")]
            if "state" in " ".join(headers) and "effective" in " ".join(headers):
                table = t
                break

    if not table:
        raise OrrickScraperError(
            "Could not find the AI law tracker table on the page. "
            "The page structure may have changed."
        )

    # Parse header row to determine column mapping
    header_cells = table.find_all("th")
    headers = [th.get_text(strip=True).lower() for th in header_cells]
    logger.info("tracker_headers", headers=headers)

    # Build column index map (flexible matching)
    col_map = {}
    for idx, h in enumerate(headers):
        if "state" in h:
            col_map["state"] = idx
        elif "scope" in h:
            col_map["ai_scope"] = idx
        elif "relevant" in h and "law" in h:
            col_map["law_name"] = idx
        elif "link" in h or (h == "law link"):
            col_map["law_url"] = idx
        elif "effective" in h:
            col_map["effective_date"] = idx
        elif "requirement" in h:
            col_map["key_requirements"] = idx
        elif "enforce" in h or "penal" in h:
            col_map["enforcement"] = idx

    tbody = table.find("tbody") or table
    rows = tbody.find_all("tr")
    records = []

    for row in rows:
        cells = row.find_all("td")
        if not cells:
            continue

        def cell_text(key: str) -> str:
            idx = col_map.get(key)
            if idx is not None and idx < len(cells):
                return cells[idx].get_text(strip=True)
            return ""

        def cell_elem(key: str):
            idx = col_map.get(key)
            if idx is not None and idx < len(cells):
                return cells[idx]
            return None

        state_name = cell_text("state")
        state_code = STATE_CODES.get(state_name, "")
        if not state_code:
            # Try partial match
            for name, code in STATE_CODES.items():
                if name.lower() in state_name.lower():
                    state_code = code
                    break

        # Extract law link — could be in "law_url" column or "law_name" column
        law_url = ""
        law_display = ""
        url_cell = cell_elem("law_url")
        if url_cell:
            law_url, law_display = _extract_link(url_cell)
        if not law_url:
            name_cell = cell_elem("law_name")
            if name_cell:
                law_url, law_display = _extract_link(name_cell)

        law_name = cell_text("law_name") or law_display

        record = {
            "state": state_name,
            "state_code": state_code,
            "ai_scope": cell_text("ai_scope"),
            "law_name": law_name,
            "law_url": law_url,
            "effective_date": cell_text("effective_date"),
            "key_requirements": cell_text("key_requirements"),
            "enforcement": cell_text("enforcement"),
        }
        records.append(record)

    logger.info("tracker_parsed", total_rows=len(records))
    return records


def seed_from_tracker(db, records: list[dict] | None = None) -> list[IngestionJob]:
    """Convert Orrick tracker rows into database records and create ingestion jobs.

    For each row, creates/updates:
        Source → DocumentFamily → DocumentVersion → IngestionJob

    Skips rows that already exist (matched by state_code + law_name).
    """
    if records is None:
        records = scrape_tracker()

    jobs_created = []

    for record in records:
        state_code = record["state_code"]
        state_name = record["state"]
        if not state_code:
            logger.debug("skipping_unknown_state", state=state_name)
            continue

        law_url = record["law_url"]
        if not law_url:
            logger.debug("skipping_no_url", state=state_code, law=record["law_name"])
            continue

        try:
            job = _seed_single_law(db, record)
            if job:
                jobs_created.append(job)
        except Exception as e:
            logger.warning(
                "seed_row_failed",
                state=state_code,
                law=record["law_name"],
                error=str(e),
            )

    db.flush()
    logger.info("seeding_complete", jobs_created=len(jobs_created))
    return jobs_created


def _seed_single_law(db, record: dict) -> IngestionJob | None:
    """Create Source/DocumentFamily/DocumentVersion/IngestionJob for one tracker row."""
    state_code = record["state_code"]
    state_name = record["state"]
    law_name = record["law_name"]
    law_url = record["law_url"]
    ai_scope = record["ai_scope"]
    effective_date = _parse_effective_date(record["effective_date"])

    # --- Source ---
    source = db.query(Source).filter_by(
        jurisdiction_code=state_code, connector_id="orrick_tracker"
    ).first()
    if not source:
        source = Source(
            jurisdiction_code=state_code,
            jurisdiction_name=state_name,
            source_type="state_statute",
            base_url=TRACKER_URL,
            connector_id="orrick_tracker",
            metadata_={"scraped_from": "orrick_ai_law_center"},
        )
        db.add(source)
        db.flush()

    # --- DocumentFamily (one per unique law name within a state) ---
    family = db.query(DocumentFamily).filter_by(
        source_id=source.id, short_cite=law_name
    ).first()
    if not family:
        family = DocumentFamily(
            source_id=source.id,
            canonical_title=f"{state_name} - {law_name}",
            short_cite=law_name,
            subject_area=_normalize_scope(ai_scope),
            metadata_={
                "orrick_ai_scope": ai_scope,
                "key_requirements": record["key_requirements"],
                "enforcement": record["enforcement"],
            },
        )
        db.add(family)
        db.flush()

    # --- DocumentVersion ---
    version_label = "Current"
    existing_version = db.query(DocumentVersion).filter_by(
        family_id=family.id, version_label=version_label
    ).first()
    if existing_version:
        logger.debug(
            "version_exists",
            state=state_code,
            law=law_name,
            version=version_label,
        )
        return None

    temporal_status = TemporalStatus.active if effective_date else TemporalStatus.enacted
    if effective_date and effective_date > date.today():
        temporal_status = TemporalStatus.future_effective

    version = DocumentVersion(
        family_id=family.id,
        version_label=version_label,
        temporal_status=temporal_status,
        effective_date=effective_date,
        metadata_={
            "law_url": law_url,
            "orrick_ai_scope": ai_scope,
        },
    )
    db.add(version)
    db.flush()

    # --- LegalEvent ---
    if effective_date:
        db.add(LegalEvent(
            document_version_id=version.id,
            event_type=LegalEventType.effective,
            event_date=effective_date,
            description=f"{law_name} effective date",
            authority=state_name,
        ))

    # --- IngestionJob ---
    job = IngestionJob(
        document_version_id=version.id,
        status=IngestionStatus.pending,
        fetch_url=law_url,
        metadata_={
            "orrick_ai_scope": ai_scope,
            "scraped_from": TRACKER_URL,
        },
    )
    db.add(job)
    db.flush()

    logger.info(
        "law_seeded",
        state=state_code,
        law=law_name,
        effective_date=str(effective_date),
        job_id=job.id,
    )
    return job


def _normalize_scope(ai_scope: str) -> str:
    """Normalize Orrick's AI scope categories to our subject_area values."""
    scope_lower = ai_scope.lower()
    if "deepfake" in scope_lower or "csam" in scope_lower:
        return "ai_content_safety"
    if "discrim" in scope_lower or "bias" in scope_lower:
        return "ai_discrimination"
    if "transpar" in scope_lower or "disclos" in scope_lower:
        return "ai_transparency"
    if "automat" in scope_lower and "decision" in scope_lower:
        return "automated_decision_making"
    if "govern" in scope_lower:
        return "ai_governance"
    if "health" in scope_lower:
        return "ai_healthcare"
    if "insur" in scope_lower:
        return "ai_insurance"
    if "employ" in scope_lower or "hiring" in scope_lower:
        return "ai_employment"
    return "artificial_intelligence"
