"""Internal review routes — /internal/ prefix.

HTMX-rendered review interface for human-in-the-loop quality assurance.
Surfaces extractions for review, allows approve/reject/revise actions,
and tracks all review decisions in an immutable audit log.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.engine import get_db
from src.db.models import (
    Extraction,
    ExtractionType,
    NormalizedSourceRecord,
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

    # Apply corrections if provided
    if decision.corrections and action_status == ReviewStatus.approved:
        item.extraction.payload = {**item.extraction.payload, **decision.corrections}

    db.commit()
    return {"status": "ok", "action": decision.action, "queue_id": queue_id}


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
        payload=e.payload,
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
