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
    orrick_bill_id: str = ""  # "Law Link" column from Orrick
    iapp_bill_number: str = ""  # "Statute/bill" column from IAPP
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


# Bill abbreviation synonyms used by legislatures and trackers
_BILL_ABBREVS: list[tuple[str, str]] = [
    ("senate bill", "sb"),
    ("house bill", "hb"),
    ("assembly bill", "ab"),
    ("senate joint resolution", "sjr"),
    ("house joint resolution", "hjr"),
    ("assembly joint resolution", "ajr"),
    ("senate concurrent resolution", "scr"),
    ("house concurrent resolution", "hcr"),
    ("senate resolution", "sr"),
    ("house resolution", "hr"),
    ("assembly resolution", "ar"),
    ("house file", "hf"),
    ("senate file", "sf"),
    ("legislative bill", "lb"),
    ("general assembly", "ga"),
]


def _normalize_bill_id(name: str) -> str:
    """Normalize a bill identifier, expanding abbreviations for matching.

    Converts both "SB 100" and "Senate Bill 100" to the same canonical
    form "sb 100" so they match during cross-referencing.
    """
    n = _normalize(name)
    # Expand long forms to short abbreviations for canonical comparison
    for long, short in _BILL_ABBREVS:
        if n.startswith(long + " "):
            n = short + " " + n[len(long) + 1:]
            break
    return n


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

    # Build IAPP indexes: multiple lookup strategies
    # 1. By normalized bill number (exact)
    # 2. By canonical bill ID (abbreviation-expanded)
    # 3. By normalized title
    iapp_by_bill: dict[tuple[str, str], dict] = {}  # (code, norm_bill_num)
    iapp_by_bill_canon: dict[tuple[str, str], dict] = {}  # (code, canonical_bill_id)
    iapp_by_title: dict[tuple[str, str], dict] = {}  # (code, norm_title)
    iapp_all: list[tuple[str, dict]] = []  # (code, record) for fallback

    for r in iapp_records:
        code = r.get("state_code", "")
        if not code:
            continue
        iapp_all.append((code, r))
        bill_num = _normalize(r.get("bill_number", ""))
        if bill_num:
            iapp_by_bill[(code, bill_num)] = r
            canon = _normalize_bill_id(r.get("bill_number", ""))
            if canon != bill_num:
                iapp_by_bill_canon[(code, canon)] = r
            else:
                iapp_by_bill_canon[(code, bill_num)] = r
        title = _normalize(r.get("bill_title", ""))
        if title:
            iapp_by_title[(code, title)] = r

    matched_iapp_indices: set[int] = set()

    for orrick in orrick_records:
        code = orrick.get("state_code", "")
        if not code:
            continue

        # Try matching with multiple strategies (most specific first)
        match_key = None
        iapp_record = None

        # Strategy 1: Exact normalized bill_id match
        for orrick_field in ["bill_id", "law_name"]:
            norm = _normalize(orrick.get(orrick_field, ""))
            if norm and (code, norm) in iapp_by_bill:
                match_key = norm
                iapp_record = iapp_by_bill[(code, norm)]
                break

        # Strategy 2: Canonical bill ID match (abbreviation-expanded)
        if not iapp_record:
            for orrick_field in ["bill_id", "law_name"]:
                canon = _normalize_bill_id(orrick.get(orrick_field, ""))
                if canon and (code, canon) in iapp_by_bill_canon:
                    match_key = canon
                    iapp_record = iapp_by_bill_canon[(code, canon)]
                    break

        # Strategy 3: Exact title match
        if not iapp_record:
            norm_name = _normalize(orrick.get("law_name", ""))
            if norm_name and (code, norm_name) in iapp_by_title:
                match_key = norm_name
                iapp_record = iapp_by_title[(code, norm_name)]

        # Strategy 4: Token-overlap on title (for bills with slightly different names)
        if not iapp_record:
            orrick_name = _normalize(orrick.get("law_name", ""))
            if orrick_name and len(orrick_name) > 5:
                orrick_tokens = set(orrick_name.split())
                best_score = 0.0
                best_match = None
                for idx, (icode, ir) in enumerate(iapp_all):
                    if icode != code or idx in matched_iapp_indices:
                        continue
                    iapp_title = _normalize(ir.get("bill_title", ""))
                    if not iapp_title:
                        continue
                    iapp_tokens = set(iapp_title.split())
                    overlap = orrick_tokens & iapp_tokens
                    # Remove common stopwords from overlap scoring
                    overlap -= {"the", "of", "and", "act", "a", "an", "in", "on", "for", "to"}
                    if not overlap:
                        continue
                    score = len(overlap) / max(len(orrick_tokens), len(iapp_tokens))
                    if score > best_score and score >= 0.5:
                        best_score = score
                        best_match = (idx, ir, iapp_title)
                if best_match:
                    idx, iapp_record, _ = best_match
                    match_key = _normalize(orrick.get("law_name", ""))
                    matched_iapp_indices.add(idx)

        if iapp_record:
            # Track matched record for iapp-only counting
            for idx, (icode, ir) in enumerate(iapp_all):
                if ir is iapp_record:
                    matched_iapp_indices.add(idx)
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

        # Legislative status (Orrick is typically enacted/active only; IAPP has full lifecycle)
        orrick_status = orrick.get("normalized_status", "")
        iapp_status = iapp_record.get("normalized_status", "")
        if orrick_status and iapp_status and orrick_status != iapp_status:
            # Use raw IAPP status for display if available
            iapp_raw = iapp_record.get("status", iapp_status)
            fields.append(FieldDiscrepancy("status", orrick_status, iapp_raw))

        if fields:
            result.discrepancies.append(BillDiscrepancy(
                state_code=code,
                match_key=match_key or "",
                orrick_title=orrick_title,
                iapp_title=iapp_title,
                orrick_url=orrick_url,
                iapp_url=iapp_url,
                orrick_bill_id=orrick.get("bill_id", ""),
                iapp_bill_number=iapp_record.get("bill_number", ""),
                fields=fields,
            ))

    # Count IAPP-only records
    for idx in range(len(iapp_all)):
        if idx not in matched_iapp_indices:
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

            # Store IAPP cross-reference data on the family
            meta = dict(family.metadata_ or {})
            updated = False
            if disc.iapp_bill_number and not meta.get("iapp_bill_number"):
                meta["iapp_bill_number"] = disc.iapp_bill_number
                updated = True
            if disc.iapp_url and not family.iapp_reference_url:
                family.iapp_reference_url = disc.iapp_url
                updated = True
            if disc.orrick_bill_id and not meta.get("bill_id"):
                meta["bill_id"] = disc.orrick_bill_id
                updated = True
            if updated:
                family.metadata_ = meta
                db.flush()

            # Find the associated IngestionJob
            job = db.scalars(
                select(IngestionJob)
                .join(DocumentVersion)
                .where(DocumentVersion.family_id == family.id)
                .limit(1)
            ).first()
            if job:
                disc.job_id = job.id
