"""Internal review routes — /internal/ prefix.

HTMX-rendered review interface for human-in-the-loop quality assurance.
Surfaces extractions for review, allows approve/reject/revise actions,
and tracks all review decisions in an immutable audit log.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.edit_service import (
    EditServiceError,
    apply_edit,
    extraction_canonical_key,
    extraction_identity_string,
    propose_edit,
)
from src.db.engine import get_db
from src.db.models import (
    Extraction,
    ExtractionType,
    ReviewAction,
    ReviewQueueItem,
    ReviewStatus,
)
from src.schemas.api import (
    ExtractionResponse,
    PaginatedResponse,
    ReviewDecision,
    ReviewQueueResponse,
)

router = APIRouter()


@router.get("/review/queue")
def list_review_queue(
    status: str = "pending",
    extraction_type: str | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PaginatedResponse:
    """List items in the review queue with filtering."""
    query = (
        select(ReviewQueueItem)
        .join(Extraction)
        .where(ReviewQueueItem.status == status)
    )
    if extraction_type:
        query = query.where(Extraction.extraction_type == extraction_type)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = (
        db.scalars(
            query.order_by(ReviewQueueItem.priority.desc(), ReviewQueueItem.created_at)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        .all()
    )

    return PaginatedResponse(
        items=[_queue_item_to_response(item) for item in items],
        total=total or 0,
        page=page,
        per_page=per_page,
        pages=max(1, ((total or 0) + per_page - 1) // per_page),
    )


@router.get("/review/queue/{queue_id}")
def get_review_item(queue_id: int, db: Session = Depends(get_db)) -> ReviewQueueResponse:
    """Get a single review queue item with full extraction details."""
    item = db.get(ReviewQueueItem, queue_id)
    if not item:
        raise HTTPException(status_code=404, detail="Review item not found")
    return _queue_item_to_response(item)


@router.post("/review/queue/{queue_id}/action")
def submit_review_action(
    queue_id: int,
    decision: ReviewDecision,
    db: Session = Depends(get_db),
) -> dict:
    """Submit a review decision (approve/reject/needs_revision)."""
    item = db.get(ReviewQueueItem, queue_id)
    if not item:
        raise HTTPException(status_code=404, detail="Review item not found")

    # Validate action
    try:
        action_status = ReviewStatus(decision.action)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action: {decision.action}. "
            f"Must be one of: approved, rejected, needs_revision",
        )

    # Create audit log entry
    review_action = ReviewAction(
        queue_item_id=queue_id,
        action=action_status,
        reviewer=decision.reviewer,
        comment=decision.comment,
        corrections=decision.corrections,
    )
    db.add(review_action)

    # Update queue item and extraction status
    item.status = action_status
    item.extraction.review_status = action_status

    # Apply corrections if provided — routed through edit_service (LC-1e) so
    # the model's original payload stays write-once (G-1 fix) instead of
    # being merged over in place; each corrected key becomes its own
    # ExtractionFieldEdit row (validated, revertible, audited) rather than an
    # untracked dict merge. Kept the requirement narrow (top-level fields
    # only) to match what this dict-of-corrections shape has always
    # supported — a dotted-path correction should go through the Law Card
    # editor (POST /api/laws/{key}/extractions/{id}/edits), which accepts one
    # field per call and can express nesting.
    correction_errors: list[str] = []
    if decision.corrections and action_status == ReviewStatus.approved:
        extraction = item.extraction
        canonical_key = extraction_canonical_key(extraction)
        if canonical_key is None:
            raise HTTPException(
                status_code=400,
                detail="This law has no canonical_key assigned yet — corrections "
                "can't be filed until it does.",
            )
        for field_path, new_value in decision.corrections.items():
            try:
                edit = propose_edit(
                    db, extraction,
                    canonical_key=canonical_key,
                    extraction_identity=extraction_identity_string(extraction),
                    field_path=field_path,
                    new_value=new_value,
                    reason=decision.comment or "Correction submitted with review approval",
                    editor=decision.reviewer,
                )
            except EditServiceError as e:
                correction_errors.append(str(e))
                continue
            result = apply_edit(db, edit.id, editor=decision.reviewer)
            if not result.success:
                correction_errors.append(result.error or f"Failed to apply correction to {field_path!r}")

    db.commit()
    response: dict = {"status": "ok", "action": decision.action, "queue_id": queue_id}
    if correction_errors:
        response["correction_errors"] = correction_errors
    return response


@router.get("/extractions")
def list_extractions(
    extraction_type: str | None = None,
    review_status: str | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PaginatedResponse:
    """List all extractions with filtering."""
    query = select(Extraction)
    if extraction_type:
        query = query.where(Extraction.extraction_type == extraction_type)
    if review_status:
        query = query.where(Extraction.review_status == review_status)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = (
        db.scalars(
            query.order_by(Extraction.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        .all()
    )

    return PaginatedResponse(
        items=[_extraction_to_response(e) for e in items],
        total=total or 0,
        page=page,
        per_page=per_page,
        pages=max(1, ((total or 0) + per_page - 1) // per_page),
    )


@router.get("/extractions/{extraction_id}")
def get_extraction(extraction_id: int, db: Session = Depends(get_db)) -> ExtractionResponse:
    """Get a single extraction with full details."""
    extraction = db.get(Extraction, extraction_id)
    if not extraction:
        raise HTTPException(status_code=404, detail="Extraction not found")
    return _extraction_to_response(extraction)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extraction_to_response(e: Extraction) -> ExtractionResponse:
    nsr = e.source_record
    dv = nsr.document_version if nsr else None
    df = dv.family if dv else None
    s = df.source if df else None
    return ExtractionResponse(
        id=e.id,
        extraction_type=e.extraction_type.value if isinstance(e.extraction_type, ExtractionType) else e.extraction_type,
        payload=e.current_payload,
        evidence_spans=e.evidence_spans,
        confidence_score=e.confidence_score,
        confidence_tier=e.confidence_tier.value if hasattr(e.confidence_tier, "value") else e.confidence_tier,
        review_status=e.review_status.value if hasattr(e.review_status, "value") else e.review_status,
        source_text=nsr.text_content if nsr else None,
        section_path=nsr.section_path if nsr else None,
        document_title=df.canonical_title if df else None,
        jurisdiction_code=s.jurisdiction_code if s else None,
        jurisdiction_name=s.jurisdiction_name if s else None,
        effective_date=dv.effective_date if dv else None,
        temporal_status=dv.temporal_status.value if dv and hasattr(dv.temporal_status, "value") else None,
        created_at=e.created_at,
    )


def _queue_item_to_response(item: ReviewQueueItem) -> ReviewQueueResponse:
    return ReviewQueueResponse(
        queue_id=item.id,
        extraction=_extraction_to_response(item.extraction),
        priority=item.priority,
        assigned_to=item.assigned_to,
        status=item.status.value if hasattr(item.status, "value") else item.status,
        created_at=item.created_at,
    )


# ---------------------------------------------------------------------------
# Verification trigger (POST only — runs LLMs)
# ---------------------------------------------------------------------------


@router.post("/verification/run")
def trigger_verification_run(
    document_version_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Trigger a verification pass (cross-validation + gap detection).

    This endpoint runs LLM calls and may take several minutes.
    Results are persisted to verification_run_summaries and readable
    via GET /v1/verification.

    Use document_version_id to scope to a single document; omit to
    run across all extracted documents.
    """
    from dataclasses import asdict

    from src.ingestion.extractor import run_verification_pass

    results = run_verification_pass(db, document_version_id)
    return {
        "triggered": True,
        "documents_processed": len(results),
        "documents": [asdict(r) for r in results],
        "summary": {
            "total_documents": len(results),
            "total_cv_flagged": sum(r.cross_validation_flagged for r in results),
            "total_gaps": sum(r.gaps_found for r in results),
            "total_high_confidence_gaps": sum(r.high_confidence_gaps for r in results),
            "total_citations_checked": sum(r.citations_checked for r in results),
            "total_citations_unverified": sum(r.citations_unverified for r in results),
            "total_tokens": sum(r.total_tokens for r in results),
        },
    }
