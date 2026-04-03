"""Review queue routes — approve / reject extractions from the dashboard."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.api.routes._dashboard_helpers import _render
from src.db.engine import get_db
from src.db.models import (
    Extraction,
    ReviewAction,
    ReviewQueueItem,
    ReviewStatus,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Review page
# ---------------------------------------------------------------------------


@router.get("/review", response_class=HTMLResponse)
def review_page(
    request: Request,
    status: str = "pending",
    page: int = Query(default=1, ge=1),
    truncated_only: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Review queue page with filtering and pagination."""
    per_page = 25

    # Get counts per status
    counts = {}
    for s in ["pending", "approved", "rejected"]:
        counts[s] = db.scalar(
            select(func.count()).select_from(
                select(ReviewQueueItem).where(ReviewQueueItem.status == s).subquery()
            )
        ) or 0

    # Count truncated items (pending only)
    truncated_count = db.scalar(
        select(func.count()).select_from(
            select(ReviewQueueItem)
            .join(Extraction)
            .where(
                ReviewQueueItem.status == "pending",
                Extraction.metadata_["truncated"].as_boolean() == True,  # noqa: E712
            )
            .subquery()
        )
    ) or 0

    # Get items for current status
    query = (
        select(ReviewQueueItem)
        .join(Extraction)
        .where(ReviewQueueItem.status == status)
    )

    if truncated_only:
        query = query.where(
            Extraction.metadata_["truncated"].as_boolean() == True,  # noqa: E712
        )

    query = (
        query
        .order_by(ReviewQueueItem.priority.desc(), ReviewQueueItem.created_at)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    queue_items = db.scalars(query).all()

    # Build display items
    items = []
    for qi in queue_items:
        e = qi.extraction
        nsr = e.source_record if e else None
        dv = nsr.document_version if nsr else None
        df = dv.family if dv else None
        src = df.source if df else None

        # Try to get confidence breakdown from metadata
        breakdown = None
        if e and e.metadata_:
            breakdown = e.metadata_.get("confidence_breakdown")

        truncated = bool(e and e.metadata_ and e.metadata_.get("truncated"))
        model_reasoning = None
        plain_summary = None
        if e and e.metadata_:
            model_reasoning = e.metadata_.get("model_reasoning")
            plain_summary = e.metadata_.get("plain_summary")

        items.append({
            "queue_id": qi.id,
            "extraction_id": e.id if e else None,
            "extraction_type": e.extraction_type.value if e and hasattr(e.extraction_type, 'value') else str(e.extraction_type),
            "payload": e.payload if e else {},
            "confidence_score": e.confidence_score if e else 0,
            "confidence_tier": e.confidence_tier.value if e and hasattr(e.confidence_tier, 'value') else 'D',
            "confidence_breakdown": breakdown,
            "model_id": e.model_id if e else None,
            "evidence_spans": e.evidence_spans if e else [],
            "model_reasoning": model_reasoning,
            "plain_summary": plain_summary,
            "review_status": qi.status.value if hasattr(qi.status, 'value') else qi.status,
            "jurisdiction_code": src.jurisdiction_code if src else None,
            "short_cite": df.short_cite if df else None,
            "source_text": nsr.text_content if nsr else None,
            "truncated": truncated,
        })

    total = counts.get(status, 0)
    total_pages = max(1, (total + per_page - 1) // per_page)

    return _render(request, "review.html", {
        "items": items,
        "counts": counts,
        "filter_status": status,
        "current_page": page,
        "total_pages": total_pages,
        "truncated_only": truncated_only,
        "truncated_count": truncated_count,
    })


# ---------------------------------------------------------------------------
# Review actions (HTMX endpoints)
# ---------------------------------------------------------------------------


@router.post("/api/review/{queue_id}/approve")
def approve_item(queue_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    """Quick-approve a review queue item."""
    return _review_action(db, queue_id, "approved")


@router.post("/api/review/{queue_id}/reject")
def reject_item(queue_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    """Quick-reject a review queue item."""
    return _review_action(db, queue_id, "rejected")


def _review_action(db: Session, queue_id: int, action: str) -> HTMLResponse:
    """Apply a review action and return updated table row."""
    item = db.get(ReviewQueueItem, queue_id)
    if not item:
        return HTMLResponse('<tr><td colspan="6">Item not found</td></tr>')

    status = ReviewStatus(action)
    db.add(ReviewAction(
        queue_item_id=queue_id,
        action=status,
        reviewer="dashboard",
    ))
    item.status = status
    item.extraction.review_status = status
    db.commit()

    color = "var(--success)" if action == "approved" else "var(--danger)"
    return HTMLResponse(
        f'<tr style="opacity: 0.5;">'
        f'<td colspan="5" style="color: {color}; font-style: italic;">'
        f'Item #{queue_id} {action}'
        f'</td>'
        f'<td></td></tr>'
    )


# ---------------------------------------------------------------------------
# Edit extraction payload (HTMX endpoint)
# ---------------------------------------------------------------------------


@router.post("/api/review/{queue_id}/edit")
def edit_extraction(
    queue_id: int,
    payload_json: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Update extraction payload fields from the review UI."""
    item = db.get(ReviewQueueItem, queue_id)
    if not item:
        return HTMLResponse(
            '<div class="result-panel danger">Item not found</div>',
            status_code=404,
        )

    try:
        updates = json.loads(payload_json)
    except json.JSONDecodeError:
        return HTMLResponse(
            '<div class="result-panel danger">Invalid JSON payload</div>',
            status_code=400,
        )

    extraction = item.extraction
    if not extraction:
        return HTMLResponse(
            '<div class="result-panel danger">No extraction linked</div>',
            status_code=404,
        )

    # Merge updates into existing payload
    current = dict(extraction.payload) if extraction.payload else {}
    for key, value in updates.items():
        if key in current or value:  # only add new keys if they have values
            current[key] = value

    extraction.payload = current

    # Record the edit as a review action
    db.add(ReviewAction(
        queue_item_id=queue_id,
        action=ReviewStatus.pending,  # stays pending after edit
        reviewer="dashboard",
        comment=f"Edited fields: {', '.join(updates.keys())}",
    ))
    db.commit()

    return HTMLResponse(
        '<div class="result-panel success" style="padding:6px 12px;font-size:12px;">'
        f'Saved changes to {len(updates)} field(s).</div>'
    )
