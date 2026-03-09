"""LegiScan API connector for discovering and fetching AI-related legislation.

Uses the LegiScan API (https://legiscan.com/legiscan) to:
1. Search for AI-related bills across multiple states
2. Fetch full bill text for ingestion
3. Track bill status changes for the legal events feed

Requires REGS_LEGISCAN_API_KEY environment variable.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import date, datetime

import httpx
import structlog

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

# AI-related search terms for LegiScan queries
AI_SEARCH_TERMS = [
    "artificial intelligence",
    "algorithmic discrimination",
    "automated decision",
    "machine learning regulation",
    "AI governance",
    "high-risk AI",
]

# State codes supported for multi-state expansion (Week 5)
SUPPORTED_STATES = {
    "CO": {"name": "Colorado", "legiscan_id": 6},
    "CA": {"name": "California", "legiscan_id": 5},
    "CT": {"name": "Connecticut", "legiscan_id": 7},
    "IL": {"name": "Illinois", "legiscan_id": 13},
    "TX": {"name": "Texas", "legiscan_id": 43},
    "VA": {"name": "Virginia", "legiscan_id": 46},
    "NY": {"name": "New York", "legiscan_id": 32},
    "US": {"name": "United States (Federal)", "legiscan_id": 0},
}


class LegiScanClient:
    """Client for the LegiScan API."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.legiscan_api_key
        self.base_url = settings.legiscan_base_url
        if not self.api_key:
            raise ValueError(
                "LegiScan API key required. Set REGS_LEGISCAN_API_KEY environment variable."
            )

    def _request(self, operation: str, **params) -> dict:
        """Make a LegiScan API request."""
        params["key"] = self.api_key
        params["op"] = operation
        response = httpx.get(self.base_url, params=params, timeout=30.0)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "ERROR":
            raise LegiScanError(data.get("alert", {}).get("message", "Unknown error"))
        return data

    def search_bills(self, query: str, state: str = "ALL", year: int = 2) -> list[dict]:
        """Search for bills matching a query.

        Args:
            query: Search terms
            state: State abbreviation or "ALL"
            year: 1=current, 2=recent, 3=prior, 4=all
        """
        data = self._request("search", query=query, state=state, year=year)
        results = data.get("searchresult", {})
        # LegiScan returns results as numbered keys + summary
        bills = []
        for key, val in results.items():
            if key == "summary" or not isinstance(val, dict):
                continue
            bills.append(val)
        return bills

    def get_bill(self, bill_id: int) -> dict:
        """Get full bill details by LegiScan bill_id."""
        data = self._request("getBill", id=bill_id)
        return data.get("bill", {})

    def get_bill_text(self, doc_id: int) -> tuple[bytes, str]:
        """Get the full text of a bill document.

        Returns (content_bytes, content_type).
        LegiScan returns base64-encoded document content.
        """
        data = self._request("getBillText", id=doc_id)
        text_data = data.get("text", {})
        mime_type = text_data.get("mime", "text/html")
        doc_b64 = text_data.get("doc", "")
        content = base64.b64decode(doc_b64)
        return content, mime_type

    def get_session_list(self, state: str) -> list[dict]:
        """Get legislative sessions for a state."""
        data = self._request("getSessionList", state=state)
        return data.get("sessions", [])

    def get_master_list(self, state: str, session_id: int | None = None) -> list[dict]:
        """Get master list of bills for a state/session."""
        params = {"state": state}
        if session_id:
            params["id"] = session_id
        data = self._request("getMasterList", **params)
        master = data.get("masterlist", {})
        bills = []
        for key, val in master.items():
            if key == "session" or not isinstance(val, dict):
                continue
            bills.append(val)
        return bills


class LegiScanError(Exception):
    """Error from LegiScan API."""
    pass


def discover_ai_bills(
    db,
    states: list[str] | None = None,
) -> list[dict]:
    """Discover AI-related bills via LegiScan and return bill metadata.

    Does NOT create database records — that's done by seed_bill_for_ingestion().
    """
    client = LegiScanClient()
    states = states or ["CO"]
    discovered = []

    for state in states:
        for term in AI_SEARCH_TERMS:
            try:
                bills = client.search_bills(query=term, state=state)
                for bill in bills:
                    discovered.append({
                        "legiscan_bill_id": bill.get("bill_id"),
                        "bill_number": bill.get("bill_number"),
                        "title": bill.get("title"),
                        "state": bill.get("state"),
                        "session": bill.get("session"),
                        "status": bill.get("status"),
                        "last_action": bill.get("last_action"),
                        "last_action_date": bill.get("last_action_date"),
                        "url": bill.get("url"),
                    })
                logger.info(
                    "legiscan_search",
                    state=state,
                    term=term,
                    results=len(bills),
                )
            except (LegiScanError, httpx.HTTPError) as e:
                logger.warning("legiscan_search_failed", state=state, term=term, error=str(e))

    # Deduplicate by bill_id
    seen = set()
    unique = []
    for bill in discovered:
        bid = bill.get("legiscan_bill_id")
        if bid and bid not in seen:
            seen.add(bid)
            unique.append(bill)

    logger.info("discovery_complete", total_unique=len(unique))
    return unique


def seed_bill_for_ingestion(
    db,
    legiscan_bill_id: int,
    jurisdiction_code: str,
    jurisdiction_name: str,
    connector_id: str = "legiscan",
) -> IngestionJob | None:
    """Fetch bill details from LegiScan and create database records for ingestion.

    Creates: Source (if needed), DocumentFamily, DocumentVersion, IngestionJob.
    Returns the IngestionJob ready for the Dagster pipeline.
    """
    client = LegiScanClient()
    bill = client.get_bill(legiscan_bill_id)

    if not bill:
        logger.warning("bill_not_found", bill_id=legiscan_bill_id)
        return None

    # Get or create Source
    source = db.query(Source).filter_by(
        jurisdiction_code=jurisdiction_code, connector_id=connector_id
    ).first()
    if not source:
        source = Source(
            jurisdiction_code=jurisdiction_code,
            jurisdiction_name=jurisdiction_name,
            source_type="state_statute" if jurisdiction_code != "US" else "federal_statute",
            connector_id=connector_id,
            metadata_={"legiscan_state_id": SUPPORTED_STATES.get(jurisdiction_code, {}).get("legiscan_id")},
        )
        db.add(source)
        db.flush()

    # Check for existing family by title
    bill_title = bill.get("title", "Unknown Bill")
    bill_number = bill.get("bill_number", "")
    family = db.query(DocumentFamily).filter_by(
        source_id=source.id, short_cite=bill_number
    ).first()
    if not family:
        family = DocumentFamily(
            source_id=source.id,
            canonical_title=bill_title,
            short_cite=bill_number,
            subject_area="artificial_intelligence",
            metadata_={
                "legiscan_bill_id": legiscan_bill_id,
                "session": bill.get("session", {}).get("session_title"),
                "bill_type": bill.get("bill_type"),
                "status": bill.get("status"),
                "subjects": [s.get("subject_name") for s in bill.get("subjects", [])],
            },
        )
        db.add(family)
        db.flush()

    # Determine the latest text document
    texts = bill.get("texts", [])
    if not texts:
        logger.warning("no_text_available", bill_id=legiscan_bill_id)
        return None

    latest_text = texts[-1]  # Most recent version
    doc_id = latest_text.get("doc_id")
    version_label = latest_text.get("type", "Introduced")

    # Check for existing version
    existing_version = db.query(DocumentVersion).filter_by(
        family_id=family.id, version_label=version_label
    ).first()
    if existing_version:
        logger.info("version_exists", bill=bill_number, version=version_label)
        return None

    # Determine temporal status from bill status
    status_map = {
        1: TemporalStatus.enacted,    # Introduced
        2: TemporalStatus.enacted,    # Engrossed
        3: TemporalStatus.enacted,    # Enrolled
        4: TemporalStatus.active,     # Passed
        5: TemporalStatus.active,     # Vetoed (still active law if override)
        6: TemporalStatus.active,     # Signed/Enacted
    }
    bill_status = bill.get("status", 1)
    temporal_status = status_map.get(bill_status, TemporalStatus.enacted)

    # Parse effective date if available
    effective_date = None
    history = bill.get("history", [])
    for event in reversed(history):
        if "effective" in event.get("action", "").lower():
            try:
                effective_date = date.fromisoformat(event["date"])
            except (ValueError, KeyError):
                pass
            break

    # Create DocumentVersion
    version = DocumentVersion(
        family_id=family.id,
        version_label=version_label,
        temporal_status=temporal_status,
        effective_date=effective_date,
        metadata_={
            "legiscan_doc_id": doc_id,
            "legiscan_bill_id": legiscan_bill_id,
            "text_url": latest_text.get("state_link") or latest_text.get("url"),
        },
    )
    db.add(version)
    db.flush()

    # Create IngestionJob — the pipeline will use the LegiScan connector to fetch
    fetch_url = latest_text.get("state_link") or latest_text.get("url", "")
    job = IngestionJob(
        document_version_id=version.id,
        status=IngestionStatus.pending,
        fetch_url=fetch_url,
        metadata_={
            "legiscan_doc_id": doc_id,
            "legiscan_bill_id": legiscan_bill_id,
        },
    )
    db.add(job)

    # Create legal events from bill history
    for event in history:
        try:
            event_date = date.fromisoformat(event["date"])
        except (ValueError, KeyError):
            continue

        action = event.get("action", "").lower()
        if "introduced" in action or "first reading" in action:
            event_type = LegalEventType.enactment
        elif "signed" in action or "enacted" in action or "approved" in action:
            event_type = LegalEventType.effective
        elif "amend" in action:
            event_type = LegalEventType.amendment
        elif "veto" in action:
            event_type = LegalEventType.stay
        else:
            continue

        legal_event = LegalEvent(
            document_version_id=version.id,
            event_type=event_type,
            event_date=event_date,
            description=event.get("action"),
            authority=event.get("chamber"),
        )
        db.add(legal_event)

    db.flush()
    logger.info(
        "bill_seeded",
        bill=bill_number,
        jurisdiction=jurisdiction_code,
        version=version_label,
        job_id=job.id,
    )
    return job
