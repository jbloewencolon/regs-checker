"""Cross-reference Orrick PDF tracker and IAPP tracker records.

Compares overlapping fields between the two sources to detect
discrepancies in titles, URLs, effective dates, and AI scope/topic.

When both sources cover the same bill, users can choose which source's
value to use as the primary for each field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


@dataclass
class FieldDiscrepancy:
    """A single field where Orrick and IAPP disagree."""

    field_name: str
    orrick_value: str
    iapp_value: str


@dataclass
class BillDiscrepancy:
    """All discrepancies for one bill found in both sources."""

    state_code: str
    match_key: str  # normalized name used to match
    orrick_title: str
    iapp_title: str
    orrick_url: str
    iapp_url: str
    fields: list[FieldDiscrepancy] = field(default_factory=list)

    # DB references (filled in when we can link to an IngestionJob)
    job_id: int | None = None
    family_id: int | None = None


@dataclass
class CrossReferenceResult:
    """Summary of cross-referencing two tracker sources."""

    orrick_total: int = 0
    iapp_total: int = 0
    matched: int = 0
    orrick_only: int = 0
    iapp_only: int = 0
    discrepancies: list[BillDiscrepancy] = field(default_factory=list)
    iapp_available: bool = False


def _normalize(name: str) -> str:
    """Normalize a name for fuzzy matching."""
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name


def _dates_match(d1: str, d2: str) -> bool:
    """Compare two date strings loosely (ignoring formatting differences)."""
    if not d1 and not d2:
        return True
    if not d1 or not d2:
        return False
    # Strip and extract digits for comparison
    digits1 = re.sub(r"[^0-9]", "", d1)
    digits2 = re.sub(r"[^0-9]", "", d2)
    if digits1 == digits2:
        return True
    # Try comparing just the core parts
    return d1.strip().lower() == d2.strip().lower()


def cross_reference_trackers(
    orrick_records: list[dict],
    iapp_records: list[dict],
) -> CrossReferenceResult:
    """Compare Orrick and IAPP records field-by-field.

    Args:
        orrick_records: Output from parse_tracker_pdf()
        iapp_records: Output from parse_iapp_pdf() or scrape_tracker()

    Returns:
        CrossReferenceResult with matched bills and field-level discrepancies.
    """
    result = CrossReferenceResult(
        orrick_total=len(orrick_records),
        iapp_total=len(iapp_records),
        iapp_available=len(iapp_records) > 0,
    )

    # Build IAPP index: (state_code, normalized_key) → record
    # Index by both bill_number and bill_title
    iapp_index: dict[tuple[str, str], dict] = {}
    for r in iapp_records:
        code = r.get("state_code", "")
        if not code:
            continue
        bill_num = _normalize(r.get("bill_number", ""))
        if bill_num:
            iapp_index[(code, bill_num)] = r
        title = _normalize(r.get("bill_title", ""))
        if title and (code, title) not in iapp_index:
            iapp_index[(code, title)] = r

    matched_iapp_keys: set[tuple[str, str]] = set()

    for orrick in orrick_records:
        code = orrick.get("state_code", "")
        if not code:
            continue

        # Try matching by bill_id first, then law_name
        match_key = None
        iapp_record = None
        for orrick_field in ["bill_id", "law_name"]:
            norm = _normalize(orrick.get(orrick_field, ""))
            if norm and (code, norm) in iapp_index:
                match_key = norm
                iapp_record = iapp_index[(code, norm)]
                matched_iapp_keys.add((code, norm))
                break

        if not iapp_record:
            result.orrick_only += 1
            continue

        result.matched += 1

        # Compare fields
        fields = []

        # Title / name
        orrick_title = orrick.get("law_name", "")
        iapp_title = iapp_record.get("bill_title", "")
        if orrick_title and iapp_title and _normalize(orrick_title) != _normalize(iapp_title):
            fields.append(FieldDiscrepancy("title", orrick_title, iapp_title))

        # URL
        orrick_url = orrick.get("law_url", "")
        iapp_url = iapp_record.get("bill_url", "")
        if orrick_url and iapp_url and orrick_url != iapp_url:
            fields.append(FieldDiscrepancy("url", orrick_url, iapp_url))

        # Effective date
        orrick_date = orrick.get("effective_date", "")
        iapp_date = iapp_record.get("effective_date", "")
        if not _dates_match(orrick_date, iapp_date):
            fields.append(FieldDiscrepancy("effective_date", orrick_date, iapp_date))

        # AI scope / topic
        orrick_scope = orrick.get("ai_scope", "")
        iapp_topic = iapp_record.get("ai_topic", "")
        if orrick_scope and iapp_topic and _normalize(orrick_scope) != _normalize(iapp_topic):
            fields.append(FieldDiscrepancy("ai_topic", orrick_scope, iapp_topic))

        if fields:
            result.discrepancies.append(BillDiscrepancy(
                state_code=code,
                match_key=match_key or "",
                orrick_title=orrick_title,
                iapp_title=iapp_title,
                orrick_url=orrick_url,
                iapp_url=iapp_url,
                fields=fields,
            ))

    # Count IAPP-only records
    for key in iapp_index:
        if key not in matched_iapp_keys:
            result.iapp_only += 1

    logger.info(
        "cross_reference_complete",
        orrick=result.orrick_total,
        iapp=result.iapp_total,
        matched=result.matched,
        discrepancies=len(result.discrepancies),
        orrick_only=result.orrick_only,
        iapp_only=result.iapp_only,
    )

    return result


def link_discrepancies_to_jobs(
    db,
    discrepancies: list[BillDiscrepancy],
) -> None:
    """Link discrepancies to existing IngestionJobs/DocumentFamilies in the DB.

    Populates job_id and family_id on each BillDiscrepancy so the UI
    can offer "apply this value" actions.
    """
    from sqlalchemy import select

    from src.db.models import DocumentFamily, DocumentVersion, IngestionJob, Source

    for disc in discrepancies:
        # Find the DocumentFamily by jurisdiction + short_cite
        family = db.scalars(
            select(DocumentFamily)
            .join(Source)
            .where(
                Source.jurisdiction_code == disc.state_code,
                DocumentFamily.short_cite.ilike(f"%{disc.match_key}%"),
            )
            .limit(1)
        ).first()

        if not family:
            # Try matching by canonical_title
            family = db.scalars(
                select(DocumentFamily)
                .join(Source)
                .where(
                    Source.jurisdiction_code == disc.state_code,
                    DocumentFamily.canonical_title.ilike(f"%{disc.orrick_title}%"),
                )
                .limit(1)
            ).first()

        if family:
            disc.family_id = family.id
            # Find the associated IngestionJob
            job = db.scalars(
                select(IngestionJob)
                .join(DocumentVersion)
                .where(DocumentVersion.family_id == family.id)
                .limit(1)
            ).first()
            if job:
                disc.job_id = job.id
