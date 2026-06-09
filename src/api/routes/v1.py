"""Product API routes — /v1/ prefix.

JSON endpoints with caching and rate limiting for external consumers.
Serves obligations, compliance matrix, dependency trees, and change feed.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.engine import get_db
from src.db.views import DEPENDENCY_TREE_QUERY
from src.schemas.api import (
    ChangeFeedItem,
    ChangeFeedResponse,
    ComplianceMatrixCell,
    ComplianceMatrixResponse,
    DependencyNode,
    DependencyTreeResponse,
    ExtractionResponse,
    ObligationQuery,
    PaginatedResponse,
)

router = APIRouter()


@router.get("/obligations")
def list_obligations(
    jurisdiction: str | None = None,
    subject: str | None = None,
    modality: str | None = None,
    active_only: bool = True,
    min_confidence: str = "B",
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PaginatedResponse:
    """Query obligations with filtering by jurisdiction, subject, modality, and confidence."""
    confidence_tiers = _tiers_at_or_above(min_confidence)

    base_view = "current_active_obligations" if active_only else "served_obligations"
    where_clauses = ["extraction_type = 'obligation'"]
    params: dict = {}

    if jurisdiction:
        where_clauses.append("jurisdiction_code = :jurisdiction")
        params["jurisdiction"] = jurisdiction
    if subject:
        where_clauses.append("payload->>'subject_normalized' ILIKE :subject")
        params["subject"] = f"%{subject}%"
    if modality:
        where_clauses.append("payload->>'modality' = :modality")
        params["modality"] = modality
    if confidence_tiers:
        where_clauses.append("confidence_tier = ANY(:tiers)")
        params["tiers"] = list(confidence_tiers)

    where_sql = " AND ".join(where_clauses)

    count_sql = f"SELECT COUNT(*) FROM {base_view} WHERE {where_sql}"
    total = db.scalar(text(count_sql).bindparams(**params)) or 0

    query_sql = (
        f"SELECT * FROM {base_view} WHERE {where_sql} "
        f"ORDER BY confidence_score DESC "
        f"LIMIT :limit OFFSET :offset"
    )
    params["limit"] = per_page
    params["offset"] = (page - 1) * per_page

    rows = db.execute(text(query_sql).bindparams(**params)).mappings().all()

    items = [
        ExtractionResponse(
            id=row["extraction_id"],
            extraction_type=row["extraction_type"],
            payload=row["payload"],
            evidence_spans=row.get("evidence_spans", []),
            confidence_score=row["confidence_score"],
            confidence_tier=row["confidence_tier"],
            review_status="approved",
            source_text=row.get("source_text"),
            section_path=row.get("section_path"),
            document_title=row.get("document_title"),
            jurisdiction_code=row.get("jurisdiction_code"),
            jurisdiction_name=row.get("jurisdiction_name"),
            effective_date=row.get("effective_date"),
            temporal_status=row.get("temporal_status"),
        )
        for row in rows
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, (total + per_page - 1) // per_page),
    )


@router.get("/obligations/{extraction_id}")
def get_obligation(extraction_id: int, db: Session = Depends(get_db)) -> ExtractionResponse:
    """Get a single obligation by extraction ID."""
    row = (
        db.execute(
            text(
                "SELECT * FROM served_obligations "
                "WHERE extraction_id = :id AND extraction_type = 'obligation'"
            ).bindparams(id=extraction_id)
        )
        .mappings()
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Obligation not found")

    return ExtractionResponse(
        id=row["extraction_id"],
        extraction_type=row["extraction_type"],
        payload=row["payload"],
        evidence_spans=row.get("evidence_spans", []),
        confidence_score=row["confidence_score"],
        confidence_tier=row["confidence_tier"],
        review_status="approved",
        source_text=row.get("source_text"),
        section_path=row.get("section_path"),
        document_title=row.get("document_title"),
        jurisdiction_code=row.get("jurisdiction_code"),
        jurisdiction_name=row.get("jurisdiction_name"),
        effective_date=row.get("effective_date"),
        temporal_status=row.get("temporal_status"),
    )


@router.get("/obligations/{extraction_id}/dependencies")
def get_obligation_dependencies(
    extraction_id: int,
    max_depth: int = Query(default=5, ge=1, le=10),
    db: Session = Depends(get_db),
) -> DependencyTreeResponse:
    """Get the full dependency tree for an obligation (Rec #4 — recursive CTEs)."""
    rows = (
        db.execute(
            text(DEPENDENCY_TREE_QUERY).bindparams(
                extraction_id=extraction_id, max_depth=max_depth
            )
        )
        .mappings()
        .all()
    )

    dependencies = [
        DependencyNode(
            extraction_id=row["child_extraction_id"],
            extraction_type=row["extraction_type"],
            payload=row["payload"],
            confidence_tier=row["confidence_tier"],
            depth=row["depth"],
            dependency_type=row["dependency_type"],
        )
        for row in rows
    ]

    return DependencyTreeResponse(
        root_extraction_id=extraction_id,
        dependencies=dependencies,
        max_depth=max_depth,
    )


@router.get("/matrix")
def get_compliance_matrix(
    jurisdiction: str | None = None,
    db: Session = Depends(get_db),
) -> ComplianceMatrixResponse:
    """Get the compliance matrix — obligation counts by jurisdiction and subject."""
    where = ""
    params: dict = {}
    if jurisdiction:
        where = "WHERE jurisdiction_code = :jurisdiction"
        params["jurisdiction"] = jurisdiction

    rows = (
        db.execute(text(f"SELECT * FROM served_matrix_cells {where}").bindparams(**params))
        .mappings()
        .all()
    )

    cells = [ComplianceMatrixCell(**dict(row)) for row in rows]
    jurisdictions = sorted({c.jurisdiction_code for c in cells})

    return ComplianceMatrixResponse(
        cells=cells,
        jurisdictions=jurisdictions,
    )


@router.get("/changes")
def get_change_feed(
    since: date | None = None,
    jurisdiction: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ChangeFeedResponse:
    """Get the change intelligence feed — recent legal events."""
    where_clauses = []
    params: dict = {}

    if since:
        where_clauses.append("le.event_date >= :since")
        params["since"] = since
    if jurisdiction:
        where_clauses.append("s.jurisdiction_code = :jurisdiction")
        params["jurisdiction"] = jurisdiction

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    query = f"""
        SELECT
            le.event_type,
            le.event_date,
            df.canonical_title AS document_title,
            s.jurisdiction_code,
            le.description
        FROM legal_events le
        JOIN document_versions dv ON dv.id = le.document_version_id
        JOIN document_families df ON df.id = dv.family_id
        JOIN sources s ON s.id = df.source_id
        {where_sql}
        ORDER BY le.event_date DESC
        LIMIT :limit
    """
    params["limit"] = limit

    rows = db.execute(text(query).bindparams(**params)).mappings().all()

    items = [
        ChangeFeedItem(
            event_type=row["event_type"],
            event_date=row["event_date"],
            document_title=row["document_title"],
            jurisdiction_code=row["jurisdiction_code"],
            description=row.get("description"),
        )
        for row in rows
    ]

    return ChangeFeedResponse(items=items, total=len(items), since=since)


# ---------------------------------------------------------------------------
# Completeness Manifest
# ---------------------------------------------------------------------------


@router.get("/completeness")
def get_completeness(
    document_version_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Return extraction completeness manifest as JSON.

    Reports per-document extraction coverage: total passages, processed,
    skipped, coverage percentage, and gaps where passages have no extractions.
    Use this to certify that all passages in a law have been processed.
    """
    from dataclasses import asdict

    from src.ingestion.extractor import compute_completeness_manifest

    reports = compute_completeness_manifest(db, document_version_id)
    return {
        "documents": [asdict(r) for r in reports],
        "summary": {
            "total_documents": len(reports),
            "complete_documents": sum(1 for r in reports if r.is_complete),
            "total_passages": sum(r.total_passages for r in reports),
            "total_processed": sum(r.passages_processed for r in reports),
            "total_gaps": sum(len(r.gaps) for r in reports),
        },
    }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@router.get("/verification")
def get_verification_results(
    document_version_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Return persisted verification summaries — read-only, no LLM calls.

    Queries the verification_run_summaries table for the latest run per
    document version.  To trigger a new verification pass, use the
    POST /internal/verification/run endpoint.
    """
    from sqlalchemy import select as sa_select

    from src.db.models import VerificationRunSummary

    query = sa_select(VerificationRunSummary).order_by(
        VerificationRunSummary.document_version_id,
        VerificationRunSummary.run_at.desc(),
    )
    if document_version_id is not None:
        query = query.where(
            VerificationRunSummary.document_version_id == document_version_id
        )

    rows = db.scalars(query).all()

    # Return latest row per document version
    seen: set[int] = set()
    summaries = []
    for row in rows:
        if row.document_version_id not in seen:
            seen.add(row.document_version_id)
            summaries.append(row)

    def _row_to_dict(r: VerificationRunSummary) -> dict:
        return {
            "id": r.id,
            "document_version_id": r.document_version_id,
            "run_at": r.run_at.isoformat() if r.run_at else None,
            "cv_passages_checked": r.cv_passages_checked,
            "cv_passages_failed": r.cv_passages_failed,
            "cv_extractions_valid": r.cv_extractions_valid,
            "cv_extractions_flagged": r.cv_extractions_flagged,
            "cv_avg_accuracy": r.cv_avg_accuracy,
            "gd_passages_checked": r.gd_passages_checked,
            "gd_passages_failed": r.gd_passages_failed,
            "gd_gaps_found": r.gd_gaps_found,
            "gd_high_confidence": r.gd_high_confidence,
            "citations_checked": r.citations_checked,
            "citations_verified": r.citations_verified,
            "citations_unverified": r.citations_unverified,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
        }

    return {
        "documents": [_row_to_dict(r) for r in summaries],
        "summary": {
            "total_documents": len(summaries),
            "total_cv_flagged": sum(r.cv_extractions_flagged for r in summaries),
            "total_gaps": sum(r.gd_gaps_found for r in summaries),
            "total_high_confidence_gaps": sum(r.gd_high_confidence for r in summaries),
            "total_citations_checked": sum(r.citations_checked for r in summaries),
            "total_citations_unverified": sum(r.citations_unverified for r in summaries),
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIER_ORDER = ["A", "B", "C", "D"]


def _tiers_at_or_above(min_tier: str) -> list[str]:
    """Return all confidence tiers at or above the given minimum."""
    min_tier = min_tier.upper()
    if min_tier not in _TIER_ORDER:
        return _TIER_ORDER
    idx = _TIER_ORDER.index(min_tier)
    return _TIER_ORDER[: idx + 1]
