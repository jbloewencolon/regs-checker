"""Legislative status checker — cross-references Orrick and IAPP trackers.

Checks the current status of every bill in our database by scraping both
the Orrick AI Law Tracker and the IAPP US State AI Legislation Tracker.
When a bill's status has changed (e.g. pending → enacted, or pending → dead),
updates the DocumentVersion.temporal_status and logs a LegalEvent.

Designed to run on a schedule (weekly) or on-demand from the dashboard.

Strategy:
  1. Scrape Orrick tracker → get enacted/active laws with effective dates
  2. Scrape IAPP tracker → get full lifecycle statuses (introduced → dead/enacted)
  3. For each DocumentVersion in our DB, match against both sources
  4. If either source reports a status change, update and log it
  5. IAPP is the primary status source (more lifecycle granularity)
  6. Orrick confirms enacted/active status and provides effective dates
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
    source: str  # "orrick", "iapp", or "both"
    detail: str = ""


@dataclass
class StatusCheckResult:
    """Summary of a full status-check run."""

    checked: int = 0
    changed: int = 0
    errors: int = 0
    changes: list[StatusChange] = field(default_factory=list)
    orrick_records: int = 0
    iapp_records: int = 0


def check_all_statuses(db: Session, *, dry_run: bool = False) -> StatusCheckResult:
    """Cross-reference all tracked bills against Orrick and IAPP.

    Args:
        db: Database session.
        dry_run: If True, detect changes but don't write to the DB.

    Returns:
        StatusCheckResult with all detected changes.
    """
    result = StatusCheckResult()

    # Step 1: Scrape both trackers
    orrick_index = _scrape_orrick_index()
    iapp_index = _scrape_iapp_index()

    result.orrick_records = len(orrick_index)
    result.iapp_records = len(iapp_index)
    logger.info(
        "status_check_sources_loaded",
        orrick=len(orrick_index),
        iapp=len(iapp_index),
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

        # Step 3: Look up this bill in both sources
        new_status = _resolve_status(
            jurisdiction, law_name, current_status, orrick_index, iapp_index
        )

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


def _scrape_orrick_index() -> dict[tuple[str, str], dict]:
    """Build a lookup index from Orrick tracker data.

    Returns dict keyed by (state_code, normalized_law_name) → record.
    """
    try:
        from src.ingestion.orrick_scraper import scrape_tracker
        records = scrape_tracker()
    except Exception as e:
        logger.warning("orrick_scrape_failed", error=str(e))
        return {}

    index = {}
    for r in records:
        code = r.get("state_code", "")
        name = _normalize_name(r.get("law_name", ""))
        if code and name:
            # Orrick mostly tracks enacted laws
            r["normalized_status"] = "active" if r.get("effective_date") else "enacted"
            index[(code, name)] = r
    return index


def _scrape_iapp_index() -> dict[tuple[str, str], dict]:
    """Build a lookup index from IAPP tracker data.

    Returns dict keyed by (state_code, normalized_bill_identifier) → record.
    Multiple keys per record (bill number + title) for fuzzy matching.
    """
    try:
        from src.ingestion.iapp_scraper import scrape_tracker
        records = scrape_tracker()
    except Exception as e:
        logger.warning("iapp_scrape_failed", error=str(e))
        return {}

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

    return index


def _normalize_name(name: str) -> str:
    """Normalize a law/bill name for fuzzy matching.

    Strips whitespace, lowercases, removes punctuation.
    """
    import re
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name


def _resolve_status(
    jurisdiction: str,
    law_name: str,
    current_status: str,
    orrick_index: dict,
    iapp_index: dict,
) -> StatusChange | None:
    """Determine if a bill's status has changed based on external sources.

    Checks IAPP first (richer lifecycle data), then Orrick (enacted confirmation).
    Only returns a change if the transition is valid.
    """
    norm_name = _normalize_name(law_name)
    key = (jurisdiction, norm_name)

    iapp_record = iapp_index.get(key)
    orrick_record = orrick_index.get(key)

    # Determine new status — IAPP has priority for pre-enactment lifecycle
    new_status = None
    source_label = ""

    if iapp_record:
        iapp_status = iapp_record.get("normalized_status", "")
        if iapp_status and iapp_status != current_status:
            new_status = iapp_status
            source_label = "iapp"

    if orrick_record:
        orrick_status = orrick_record.get("normalized_status", "")
        if orrick_status and orrick_status != current_status:
            if new_status and new_status == orrick_status:
                source_label = "both"  # Both agree
            elif not new_status:
                new_status = orrick_status
                source_label = "orrick"
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
    if orrick_record and not detail:
        detail = orrick_record.get("effective_date", "")

    return StatusChange(
        document_version_id=0,  # filled in by caller
        family_title="",
        jurisdiction_code=jurisdiction,
        old_status=current_status,
        new_status=new_status,
        source=source_label,
        detail=detail,
    )


def _apply_status_change(
    db: Session,
    version: DocumentVersion,
    change: StatusChange,
) -> None:
    """Write a status change to the database.

    Updates DocumentVersion.temporal_status and appends a LegalEvent.
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

    # Update version status
    version.temporal_status = TemporalStatus(new)

    # Log the event
    event_type = STATUS_TO_EVENT.get(new, LegalEventType.status_check)
    db.add(LegalEvent(
        document_version_id=version.id,
        event_type=event_type,
        event_date=date.today(),
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
