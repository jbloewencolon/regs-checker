"""Dashboard routes — HTML UI for the extraction pipeline.

Serves a simple HTMX-powered dashboard that lets non-technical users
run each pipeline step, track progress, and review extractions.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.db.engine import get_db
from src.db.models import (
    ConfidenceTier,
    DocumentVersion,
    Extraction,
    ExtractionType,
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
    ReviewQueueItem,
    ReviewStatus,
)

router = APIRouter()

EXPORT_DIR = Path("export")


# ---------------------------------------------------------------------------
# Template rendering helper
# ---------------------------------------------------------------------------

def _render(request: Request, template: str, context: dict = None) -> HTMLResponse:
    """Render a Jinja2 template. Uses the templates instance from app state."""
    ctx = context or {}
    ctx["request"] = request
    templates = request.app.state.templates
    return templates.TemplateResponse(template, ctx)


# ---------------------------------------------------------------------------
# Dashboard pages
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    """Main pipeline dashboard page."""
    stats = _get_pipeline_stats(db)
    export_files = _get_export_files()

    return _render(request, "dashboard.html", {
        "stats": stats,
        "export_files": export_files,
    })


@router.get("/review", response_class=HTMLResponse)
def review_page(
    request: Request,
    status: str = "pending",
    page: int = Query(default=1, ge=1),
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

    # Get items for current status
    query = (
        select(ReviewQueueItem)
        .join(Extraction)
        .where(ReviewQueueItem.status == status)
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

        items.append({
            "queue_id": qi.id,
            "extraction_id": e.id if e else None,
            "extraction_type": e.extraction_type.value if e and hasattr(e.extraction_type, 'value') else str(e.extraction_type),
            "payload": e.payload if e else {},
            "confidence_score": e.confidence_score if e else 0,
            "confidence_tier": e.confidence_tier.value if e and hasattr(e.confidence_tier, 'value') else 'D',
            "review_status": qi.status.value if hasattr(qi.status, 'value') else qi.status,
            "jurisdiction_code": src.jurisdiction_code if src else None,
            "short_cite": df.short_cite if df else None,
        })

    total = counts.get(status, 0)
    total_pages = max(1, (total + per_page - 1) // per_page)

    return _render(request, "review.html", {
        "items": items,
        "counts": counts,
        "filter_status": status,
        "current_page": page,
        "total_pages": total_pages,
    })


# ---------------------------------------------------------------------------
# API endpoints (called by HTMX buttons)
# ---------------------------------------------------------------------------


@router.get("/api/stats")
def get_stats(db: Session = Depends(get_db)) -> HTMLResponse:
    """Return stats HTML fragment for the header bar."""
    stats = _get_pipeline_stats(db)
    html = f"""
    <span class="stat">
      <span class="stat-value">{stats['total_passages']}</span> passages
    </span>
    <span class="stat">
      <span class="stat-value">{stats['total_extractions']}</span> extractions
    </span>
    <span class="stat">
      <span class="stat-value">{stats['pending_review']}</span> to review
    </span>
    """
    return HTMLResponse(html)


@router.post("/api/run/orrick-discovery")
def run_orrick_discovery(db: Session = Depends(get_db)) -> HTMLResponse:
    """Run Orrick tracker scrape."""
    try:
        from src.ingestion.orrick_scraper import scrape_tracker, seed_from_tracker
        records = scrape_tracker()
        jobs = seed_from_tracker(db, records)
        db.commit()
        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Found {len(records)} laws on tracker, seeded {len(jobs)} new ones for ingestion.'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {e}</div>'
        )


@router.post("/api/run/status-check")
def run_status_check(
    dry_run: bool = False,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Check bill statuses against Orrick and IAPP trackers."""
    try:
        from src.ingestion.status_checker import check_all_statuses
        result = check_all_statuses(db, dry_run=dry_run)

        if result.changed == 0:
            return HTMLResponse(
                f'<div class="result-panel info">'
                f'Checked {result.checked} bills against '
                f'Orrick ({result.orrick_records} records) and '
                f'IAPP ({result.iapp_records} records). '
                f'No status changes detected.'
                f'</div>'
            )

        mode = "Would change" if dry_run else "Updated"
        changes_html = "".join(
            f'<li><strong>{c.jurisdiction_code}</strong> — {c.family_title}: '
            f'<span style="text-decoration: line-through;">{c.old_status}</span> → '
            f'<strong>{c.new_status}</strong> '
            f'<em>(via {c.source})</em></li>'
            for c in result.changes[:20]
        )
        extra = f" (+{result.changed - 20} more)" if result.changed > 20 else ""

        panel_class = "warning" if dry_run else "success"
        return HTMLResponse(
            f'<div class="result-panel {panel_class}">'
            f'{mode} {result.changed} bill statuses '
            f'(checked {result.checked} bills). '
            f'{result.errors} errors.{extra}'
            f'<ul style="margin: 8px 0 0 16px; font-size: 13px;">{changes_html}</ul>'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {e}</div>'
        )


@router.post("/api/run/fetch")
def run_fetch(
    limit: int | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Fetch and parse pending documents."""
    try:
        from src.ingestion.pipeline import run_pending_ingestion
        summary = run_pending_ingestion(db, limit=limit)
        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Completed: {summary["completed"]} documents, '
            f'{summary["total_passages"]} passages extracted. '
            f'{summary["failed"]} failed.'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {e}</div>'
        )


@router.post("/api/run/export-passages")
def run_export_passages(
    limit: int | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Export unprocessed passages for Claude Code."""
    try:
        from src.scripts.manual_extraction import export_passages
        summary = export_passages(db, limit=limit)
        if summary["total_passages"] == 0:
            return HTMLResponse(
                '<div class="result-panel info">No unprocessed passages to export.</div>'
            )
        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Exported {summary["total_passages"]} passages into '
            f'{summary["batches"]} batch files. '
            f'Check the <code>export/</code> folder.'
            f'</div>'
        )
    except Exception as e:
        return HTMLResponse(
            f'<div class="result-panel error">Error: {e}</div>'
        )


@router.post("/api/run/import-extractions")
def run_import_extractions(db: Session = Depends(get_db)) -> HTMLResponse:
    """Import Claude Code extraction results."""
    try:
        from src.scripts.manual_extraction import import_extractions
        summary = import_extractions(db)
        if summary["files_processed"] == 0:
            return HTMLResponse(
                '<div class="result-panel warning">'
                'No result files found. Save Claude\'s JSON responses as '
                '<code>export/batch_*_results.json</code> first.'
                '</div>'
            )
        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Imported {summary["extractions_created"]} extractions '
            f'from {summary["files_processed"]} files. '
            f'{summary["duplicates_skipped"]} duplicates skipped, '
            f'{summary["errors"]} errors.'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {e}</div>'
        )


@router.post("/api/run/extract")
def run_api_extract(
    limit: int = 10,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Run API-based extraction (paid)."""
    try:
        from src.ingestion.extractor import run_extraction
        summary = run_extraction(db, limit=limit)
        tokens = summary.get("token_usage", {})
        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Extracted {summary["total_extractions"]} items from '
            f'{summary["records_processed"]} passages. '
            f'Tokens: {tokens.get("total_tokens", 0):,}'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {e}</div>'
        )


@router.post("/api/run/sync")
def run_sync(db: Session = Depends(get_db)) -> HTMLResponse:
    """Sync extractions to Policy Navigator."""
    import os
    source_url = os.environ.get("REGS_SUPABASE_URL")
    target_url = os.environ.get("REGS_POLICY_NAVIGATOR_URL")

    if not source_url or not target_url:
        return HTMLResponse(
            '<div class="result-panel warning">'
            'Sync skipped: REGS_SUPABASE_URL and/or REGS_POLICY_NAVIGATOR_URL not configured.'
            '</div>'
        )

    try:
        from src.scripts.sync_extractions import sync_extractions
        summary = sync_extractions(source_url=source_url, target_url=target_url, dry_run=False)
        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Synced {summary["synced"]} rows to Policy Navigator. '
            f'{summary.get("skipped_excluded", 0)} excluded, '
            f'{summary.get("skipped_no_bridge", 0)} no bridge mapping.'
            f'</div>'
        )
    except Exception as e:
        return HTMLResponse(
            f'<div class="result-panel error">Error: {e}</div>'
        )


@router.post("/api/run/bridge-check")
def run_bridge_check() -> HTMLResponse:
    """Check for bridge gaps."""
    import os
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    source_url = os.environ.get("REGS_SUPABASE_URL")
    target_url = os.environ.get("REGS_POLICY_NAVIGATOR_URL")

    if not source_url or not target_url:
        return HTMLResponse(
            '<div class="result-panel warning">Database URLs not configured.</div>'
        )

    try:
        from src.core.bridge_monitor import detect_unbridged_families, format_bridge_gap_notification
        source_engine = create_engine(source_url)
        target_engine = create_engine(target_url)
        source_session = sessionmaker(bind=source_engine)()
        target_session = sessionmaker(bind=target_engine)()

        try:
            report = detect_unbridged_families(source_session, target_session)
            if report.has_gaps:
                return HTMLResponse(
                    f'<div class="result-panel warning">'
                    f'{report.unbridged_families} document families have no bridge row. '
                    f'These cannot be synced until bridge mappings are created.'
                    f'</div>'
                )
            return HTMLResponse(
                f'<div class="result-panel success">'
                f'All {report.bridged_families} families have bridge mappings.'
                f'</div>'
            )
        finally:
            source_session.close()
            target_session.close()
    except Exception as e:
        return HTMLResponse(
            f'<div class="result-panel error">Error: {e}</div>'
        )


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
    from src.db.models import ReviewAction

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
# Helpers
# ---------------------------------------------------------------------------


def _get_pipeline_stats(db: Session) -> dict:
    """Gather pipeline statistics for the dashboard."""
    pending_ingestion = db.scalar(
        select(func.count()).where(IngestionJob.status == IngestionStatus.pending)
    ) or 0

    total_passages = db.scalar(
        select(func.count()).select_from(NormalizedSourceRecord)
    ) or 0

    total_extractions = db.scalar(
        select(func.count()).select_from(Extraction)
    ) or 0

    # Unprocessed passages (no extractions yet)
    extracted_ids = select(Extraction.source_record_id).distinct()
    unprocessed_passages = db.scalar(
        select(func.count()).where(
            NormalizedSourceRecord.id.notin_(extracted_ids)
        )
    ) or 0

    pending_review = db.scalar(
        select(func.count()).where(ReviewQueueItem.status == ReviewStatus.pending)
    ) or 0

    approved_extractions = db.scalar(
        select(func.count()).where(Extraction.review_status == ReviewStatus.approved)
    ) or 0

    # Review counts by tier
    review_by_tier = {}
    for tier in ["A", "B", "C", "D"]:
        count = db.scalar(
            select(func.count())
            .select_from(ReviewQueueItem)
            .join(Extraction)
            .where(
                ReviewQueueItem.status == ReviewStatus.pending,
                Extraction.confidence_tier == tier,
            )
        ) or 0
        review_by_tier[tier] = count

    # Status summary — count document versions by temporal status
    status_summary = {}
    status_rows = db.execute(
        select(
            DocumentVersion.temporal_status,
            func.count(),
        ).group_by(DocumentVersion.temporal_status)
    ).all()
    for row in status_rows:
        status_val = row[0].value if hasattr(row[0], "value") else str(row[0])
        status_summary[status_val] = row[1]

    # Pending result files
    pending_results = len(list(EXPORT_DIR.glob("batch_*_results.json"))) if EXPORT_DIR.exists() else 0

    return {
        "pending_ingestion": pending_ingestion,
        "total_passages": total_passages,
        "unprocessed_passages": unprocessed_passages,
        "total_extractions": total_extractions,
        "approved_extractions": approved_extractions,
        "pending_review": pending_review,
        "review_by_tier": review_by_tier,
        "pending_results": pending_results,
        "status_summary": status_summary,
    }


def _get_export_files() -> list[dict]:
    """List export batch files and their result status."""
    if not EXPORT_DIR.exists():
        return []

    files = []
    for txt_file in sorted(EXPORT_DIR.glob("batch_*.txt")):
        result_file = txt_file.with_name(txt_file.stem + "_results.json")
        done_file = result_file.with_suffix(".json.done")
        files.append({
            "name": txt_file.name,
            "path": str(txt_file),
            "has_result": result_file.exists() or done_file.exists(),
        })
    return files
