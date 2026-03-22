"""Legislative status checker — cross-references PDF tracker and IAPP data.

Checks the current status of every bill in our database by comparing
against the Orrick PDF tracker data (already in DB metadata) and the
IAPP US State AI Legislation Tracker.

When a bill's status has changed (e.g. pending → enacted, or pending → dead),
updates the DocumentVersion.temporal_status and logs a LegalEvent.

Designed to run on a schedule (weekly) or on-demand from the dashboard.

Strategy:
  1. Load PDF tracker records from the database (seeded by pdf_tracker.py)
  2. Scrape IAPP tracker → get full lifecycle statuses (introduced → dead/enacted)
  3. For each DocumentVersion in our DB, match against both sources
  4. If either source reports a status change, update and log it
  5. IAPP is the primary status source (more lifecycle granularity)
  6. PDF tracker confirms enacted/active status and provides effective dates
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    DocumentFamily,
    DocumentVersion,
    LegalEvent,
    LegalEventType,
    Source,
    TemporalStatus,
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Status transition rules
# ---------------------------------------------------------------------------

# Valid transitions: current_status → set of allowed new statuses
VALID_TRANSITIONS: dict[str, set[str]] = {
    "introduced": {"pending", "passed_one_chamber", "enacted", "active", "dead", "vetoed", "withdrawn"},
    "pending": {"passed_one_chamber", "enacted", "active", "dead", "vetoed", "withdrawn"},
    "passed_one_chamber": {"enacted", "active", "future_effective", "dead", "vetoed", "withdrawn"},
    "enacted": {"active", "future_effective", "repealed", "stayed"},
    "future_effective": {"active", "repealed", "stayed"},
    "active": {"repealed", "stayed"},
    "repealed": set(),  # terminal
    "stayed": {"active", "repealed"},
    "vetoed": set(),  # terminal
    "dead": {"introduced", "pending"},  # can be reintroduced
    "withdrawn": {"introduced", "pending"},  # can be reintroduced
}

# Map LegalEventType for each transition
STATUS_TO_EVENT: dict[str, LegalEventType] = {
    "introduced": LegalEventType.introduction,
    "pending": LegalEventType.introduction,
    "passed_one_chamber": LegalEventType.passage_one_chamber,
    "enacted": LegalEventType.enactment,
    "active": LegalEventType.effective,
    "future_effective": LegalEventType.enactment,
    "repealed": LegalEventType.repeal,
    "stayed": LegalEventType.stay,
    "vetoed": LegalEventType.veto,
    "dead": LegalEventType.death,
    "withdrawn": LegalEventType.withdrawal,
}


@dataclass
class StatusChange:
    """A detected status change for one document version."""

    document_version_id: int
    family_title: str
    jurisdiction_code: str
    old_status: str
    new_status: str
    source: str  # "pdf_tracker", "iapp", or "both"
    detail: str = ""


@dataclass
class StatusCheckResult:
    """Summary of a full status-check run."""

    checked: int = 0
    changed: int = 0
    errors: int = 0
    changes: list[StatusChange] = field(default_factory=list)
    pdf_records: int = 0
    iapp_records: int = 0
    pdf_matched: int = 0
    iapp_matched: int = 0


def check_all_statuses(db: Session, *, dry_run: bool = False) -> StatusCheckResult:
    """Cross-reference all tracked bills against PDF tracker and IAPP.

    Args:
        db: Database session.
        dry_run: If True, detect changes but don't write to the DB.

    Returns:
        StatusCheckResult with all detected changes.
    """
    result = StatusCheckResult()

    # Step 1: Build indexes from both sources
    pdf_index = _build_pdf_index(db)
    iapp_result = _scrape_iapp_index()
    iapp_index = iapp_result.index

    result.pdf_records = len(pdf_index)
    result.iapp_records = len(iapp_index)

    if not iapp_result.success:
        logger.warning(
            "status_check_iapp_unavailable",
            error=iapp_result.error,
            note="Proceeding with PDF tracker data only",
        )

    logger.info(
        "status_check_sources_loaded",
        pdf_tracker=len(pdf_index),
        iapp=len(iapp_index),
        iapp_available=iapp_result.success,
    )

    # Step 2: Load all document versions with their family and source info
    versions = db.scalars(
        select(DocumentVersion)
        .join(DocumentFamily)
        .join(Source)
        .order_by(Source.jurisdiction_code, DocumentFamily.short_cite)
    ).all()

    for version in versions:
        result.checked += 1
        family = version.family
        source = family.source if family else None

        if not source or not source.jurisdiction_code:
            continue

        jurisdiction = source.jurisdiction_code
        law_name = family.short_cite or family.canonical_title or ""
        current_status = (
            version.temporal_status.value
            if hasattr(version.temporal_status, "value")
            else str(version.temporal_status)
        )

        # Track which bills are found in each source index
        norm_name = _normalize_name(law_name)
        key = (jurisdiction, norm_name)
        if key in pdf_index:
            result.pdf_matched += 1
        if key in iapp_index:
            result.iapp_matched += 1

        # Step 3: Look up this bill in both sources
        new_status = _resolve_status(
            jurisdiction, law_name, current_status, pdf_index, iapp_index
        )

        # Persist IAPP metadata on the family regardless of status change.
        # This ensures bill_number, raw status, and ai_topic are available
        # for extraction even if the status hasn't changed.
        if not dry_run:
            _store_iapp_metadata(family, jurisdiction, law_name, iapp_index)

        if new_status and new_status.new_status != current_status:
            result.changed += 1
            result.changes.append(new_status)
            new_status.document_version_id = version.id
            new_status.family_title = family.canonical_title or law_name
            new_status.jurisdiction_code = jurisdiction

            if not dry_run:
                _apply_status_change(db, version, new_status)

    if not dry_run and result.changed > 0:
        db.commit()

    logger.info(
        "status_check_complete",
        checked=result.checked,
        changed=result.changed,
        dry_run=dry_run,
    )
    return result


def _build_pdf_index(db: Session) -> dict[tuple[str, str], dict]:
    """Build a lookup index from PDF tracker data already in the database.

    Uses DocumentFamily metadata (key_requirements, enforcement, ai_scope)
    that was seeded by parse_tracker_pdf() → seed_from_tracker().

    Returns dict keyed by (state_code, normalized_law_name) → record.
    """
    index = {}

    families = db.scalars(
        select(DocumentFamily)
        .join(Source)
        .where(Source.connector_id.in_(["pdf_tracker", "orrick_tracker"]))
    ).all()

    for family in families:
        source = family.source
        if not source:
            continue
        code = source.jurisdiction_code
        name = _normalize_name(family.short_cite or "")
        if code and name:
            meta = family.metadata_ or {}
            # Determine status from the latest version
            latest_version = None
            for v in family.versions:
                if latest_version is None or (v.effective_date and (
                    latest_version.effective_date is None or
                    v.effective_date > latest_version.effective_date
                )):
                    latest_version = v

            effective_date = latest_version.effective_date if latest_version else None
            normalized_status = "active" if effective_date else "enacted"

            index[(code, name)] = {
                "state_code": code,
                "law_name": family.short_cite,
                "effective_date": str(effective_date) if effective_date else "",
                "key_requirements": meta.get("key_requirements", ""),
                "enforcement": meta.get("enforcement", ""),
                "normalized_status": normalized_status,
            }

    return index


class IAPPScrapeResult:
    """Wraps IAPP scrape output to distinguish 'failed' from 'empty'."""

    __slots__ = ("index", "success", "error")

    def __init__(self, index: dict, *, success: bool = True, error: str = ""):
        self.index = index
        self.success = success
        self.error = error


def _scrape_iapp_index() -> IAPPScrapeResult:
    """Build a lookup index from IAPP tracker data.

    Tries the local IAPP PDF first (fast, no network), then falls back
    to web scraping if the PDF is not available.

    Returns an IAPPScrapeResult so callers can distinguish:
      - success=True,  index={...}  → parsed/scraped OK, got data
      - success=True,  index={}     → parsed/scraped OK, zero bills matched
      - success=False, index={}     → both methods failed
    """
    records = None

    # Strategy 1: Parse local IAPP PDF (preferred — no network dependency)
    try:
        from src.ingestion.iapp_pdf_tracker import IAPP_PDF_PATH, parse_iapp_pdf
        if IAPP_PDF_PATH.exists():
            records = parse_iapp_pdf()
            logger.info("iapp_loaded_from_pdf", records=len(records))
    except Exception as e:
        logger.warning("iapp_pdf_parse_failed", error=str(e)[:200])

    # Strategy 2: Fall back to web scraping
    if records is None:
        try:
            from src.ingestion.iapp_scraper import scrape_tracker
            records = scrape_tracker()
        except Exception as e:
            logger.warning("iapp_scrape_failed", error=str(e))
            return IAPPScrapeResult({}, success=False, error=str(e))

    index = {}
    for r in records:
        code = r.get("state_code", "")
        if not code:
            continue

        # Index by bill number
        bill_num = _normalize_name(r.get("bill_number", ""))
        if bill_num:
            index[(code, bill_num)] = r

        # Also index by bill title for cross-source matching
        title = _normalize_name(r.get("bill_title", ""))
        if title:
            index[(code, title)] = r

    return IAPPScrapeResult(index, success=True)


def _normalize_name(name: str) -> str:
    """Normalize a law/bill name for fuzzy matching."""
    import re
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name


def _resolve_status(
    jurisdiction: str,
    law_name: str,
    current_status: str,
    pdf_index: dict,
    iapp_index: dict,
) -> StatusChange | None:
    """Determine if a bill's status has changed based on external sources.

    Checks IAPP first (richer lifecycle data), then PDF tracker (enacted confirmation).
    Only returns a change if the transition is valid.
    """
    norm_name = _normalize_name(law_name)
    key = (jurisdiction, norm_name)

    iapp_record = iapp_index.get(key)
    pdf_record = pdf_index.get(key)

    # Determine new status — IAPP has priority for pre-enactment lifecycle
    new_status = None
    source_label = ""

    if iapp_record:
        iapp_status = iapp_record.get("normalized_status", "")
        if iapp_status and iapp_status != current_status:
            new_status = iapp_status
            source_label = "iapp"

    if pdf_record:
        pdf_status = pdf_record.get("normalized_status", "")
        if pdf_status and pdf_status != current_status:
            if new_status and new_status == pdf_status:
                source_label = "both"  # Both agree
            elif not new_status:
                new_status = pdf_status
                source_label = "pdf_tracker"
            # If they disagree, prefer IAPP (more lifecycle detail)

    if not new_status:
        return None

    # Validate transition
    valid_next = VALID_TRANSITIONS.get(current_status, set())
    if new_status not in valid_next:
        logger.debug(
            "invalid_status_transition",
            jurisdiction=jurisdiction,
            law=law_name,
            current=current_status,
            proposed=new_status,
        )
        return None

    detail = ""
    if iapp_record:
        detail = iapp_record.get("last_action", "")
    if pdf_record and not detail:
        detail = pdf_record.get("effective_date", "")

    return StatusChange(
        document_version_id=0,  # filled in by caller
        family_title="",
        jurisdiction_code=jurisdiction,
        old_status=current_status,
        new_status=new_status,
        source=source_label,
        detail=detail,
    )


def _store_iapp_metadata(
    family: DocumentFamily,
    jurisdiction: str,
    law_name: str,
    iapp_index: dict,
) -> None:
    """Store IAPP-sourced metadata on a DocumentFamily.

    Looks up the bill in the IAPP index and stores bill_number,
    raw status, ai_topic, and last_action in family.metadata_ so
    they're available for extraction agents.
    """
    norm = _normalize_name(law_name)
    key = (jurisdiction, norm)
    iapp_record = iapp_index.get(key)
    if not iapp_record:
        return

    meta = dict(family.metadata_ or {})
    updated = False

    for iapp_key, meta_key in [
        ("bill_number", "iapp_bill_number"),
        ("status", "iapp_status"),
        ("ai_topic", "iapp_ai_topic"),
        ("last_action", "iapp_last_action"),
        ("bill_url", "iapp_bill_url"),
    ]:
        val = iapp_record.get(iapp_key, "")
        if val and meta.get(meta_key) != val:
            meta[meta_key] = val
            updated = True

    if updated:
        family.metadata_ = meta
        if iapp_record.get("bill_url") and not family.iapp_reference_url:
            family.iapp_reference_url = iapp_record["bill_url"]
        logger.debug(
            "iapp_metadata_stored",
            jurisdiction=jurisdiction,
            law=law_name,
            iapp_bill=iapp_record.get("bill_number", ""),
            iapp_status=iapp_record.get("status", ""),
        )


def _apply_status_change(
    db: Session,
    version: DocumentVersion,
    change: StatusChange,
) -> None:
    """Write a status change to the database.

    Deduplicates LegalEvents by checking for an existing event with the same
    (version_id, event_type, event_date) before creating a new one. This
    prevents duplicate events when the status checker runs multiple times
    on the same day with unchanged source data.
    """
    old = change.old_status
    new = change.new_status

    logger.info(
        "applying_status_change",
        version_id=version.id,
        jurisdiction=change.jurisdiction_code,
        family=change.family_title,
        old_status=old,
        new_status=new,
        source=change.source,
    )

    version.temporal_status = TemporalStatus(new)

    event_type = STATUS_TO_EVENT.get(new, LegalEventType.status_check)
    today = date.today()

    # Dedup: check for existing event with same (version, type, date)
    existing_event = db.scalars(
        select(LegalEvent).where(
            LegalEvent.document_version_id == version.id,
            LegalEvent.event_type == event_type,
            LegalEvent.event_date == today,
        )
    ).first()

    if existing_event:
        logger.debug(
            "legal_event_deduplicated",
            version_id=version.id,
            event_type=event_type.value,
            event_date=str(today),
        )
        return

    db.add(LegalEvent(
        document_version_id=version.id,
        event_type=event_type,
        event_date=today,
        description=(
            f"Status changed: {old} → {new} "
            f"(detected via {change.source})"
        ),
        authority=change.source,
        metadata_={
            "old_status": old,
            "new_status": new,
            "check_source": change.source,
            "detail": change.detail,
        },
    ))
    db.flush()
