"""Dashboard routes — HTML UI for the extraction pipeline.

Serves an HTMX-powered dashboard with:
  - Real-time progress tracking with % completion and ETA
  - Pipeline step controls (run each step or run-all)
  - Analytics: confidence breakdown, model comparison, jurisdiction view
  - Review queue with confidence component visualization
"""

from __future__ import annotations

from html import escape as html_escape
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.db.engine import get_db
from src.db.models import (
    ConfidenceTier,
    DocumentFamily,
    DocumentVersion,
    Extraction,
    ExtractionType,
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
    RawArtifact,
    ReviewQueueItem,
    ReviewStatus,
    Source,
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
    from src.api.progress import compute_pipeline_progress
    from src.core.config import settings

    stats = _get_pipeline_stats(db)
    export_files = _get_export_files()
    progress = compute_pipeline_progress(db)

    return _render(request, "dashboard.html", {
        "stats": stats,
        "export_files": export_files,
        "progress": progress.to_dict(),
        "config": settings,
    })


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, db: Session = Depends(get_db)):
    """Analytics and evaluation page."""
    from src.api.progress import (
        get_confidence_distribution,
        get_extraction_by_type,
        get_jurisdiction_summary,
        get_model_comparison,
    )

    return _render(request, "analytics.html", {
        "confidence": get_confidence_distribution(db),
        "by_type": get_extraction_by_type(db),
        "models": get_model_comparison(db),
        "jurisdictions": get_jurisdiction_summary(db),
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

        # Try to get confidence breakdown from metadata
        breakdown = None
        if e and e.metadata_:
            breakdown = e.metadata_.get("confidence_breakdown")

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
            "review_status": qi.status.value if hasattr(qi.status, 'value') else qi.status,
            "jurisdiction_code": src.jurisdiction_code if src else None,
            "short_cite": df.short_cite if df else None,
            "source_text": nsr.passage_text if nsr else None,
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
    from src.api.progress import compute_pipeline_progress

    stats = _get_pipeline_stats(db)
    progress = compute_pipeline_progress(db)

    eta_html = ""
    if progress.estimated_remaining_seconds:
        hrs = progress.estimated_remaining_seconds // 3600
        mins = (progress.estimated_remaining_seconds % 3600) // 60
        if hrs > 0:
            eta_html = f'<span class="stat"><span class="stat-label">ETA</span> <span class="stat-value">{hrs}h {mins}m</span></span>'
        else:
            eta_html = f'<span class="stat"><span class="stat-label">ETA</span> <span class="stat-value">{mins}m</span></span>'

    html = f"""
    <span class="stat">
      <span class="stat-label">Progress</span>
      <span class="stat-value">{progress.overall_percent}%</span>
    </span>
    <span class="stat">
      <span class="stat-value">{stats['total_passages']}</span>
      <span class="stat-label">passages</span>
    </span>
    <span class="stat">
      <span class="stat-value">{stats['total_extractions']}</span>
      <span class="stat-label">extractions</span>
    </span>
    <span class="stat">
      <span class="stat-value">{stats['pending_review']}</span>
      <span class="stat-label">to review</span>
    </span>
    {eta_html}
    """
    return HTMLResponse(html)


@router.get("/api/progress")
def get_progress(db: Session = Depends(get_db)) -> HTMLResponse:
    """Return progress ring + ETA HTML fragment, polled every 5s during runs."""
    from src.api.progress import compute_pipeline_progress

    progress = compute_pipeline_progress(db)
    p = progress.to_dict()

    # Build step bars
    step_bars = ""
    for s in p["steps"]:
        bar_color = "var(--success)" if s["is_complete"] else "var(--primary)"
        failed_tag = f' <span style="color:var(--danger);font-size:11px;">({s["failed"]} failed)</span>' if s.get("failed", 0) > 0 else ""

        if s.get("display_mode") == "found":
            # One-shot step (Discovery): show "N found" instead of "N/N"
            if s["total"] > 0:
                count_label = f'{s["total"]} found'
                pct = "100.0"
                fill_pct = 100
            else:
                count_label = "Not run"
                pct = "—"
                fill_pct = 0
            step_bars += f"""
            <div class="progress-step-row">
              <span class="progress-step-label">{s['name']}</span>
              <div class="progress-step-bar">
                <div class="progress-step-fill" style="width: {fill_pct}%; background: {bar_color};"></div>
              </div>
              <span class="progress-step-pct">{pct}{'%' if s['total'] > 0 else ''}</span>
              <span class="progress-step-count">{count_label}</span>
            </div>
            """
        else:
            # Show breakdown: completed + failed + pending = total
            pending = s.get("pending", 0)
            failed = s.get("failed", 0)
            in_prog = s.get("in_progress", 0)
            detail_parts = []
            if failed > 0:
                detail_parts.append(f'<span style="color:var(--danger);">{failed} failed</span>')
            if pending > 0:
                detail_parts.append(f'<span style="color:var(--text-muted);">{pending} pending</span>')
            if in_prog > 0:
                detail_parts.append(f'<span style="color:var(--warning);">{in_prog} in progress</span>')
            detail_tag = f' <span style="font-size:11px;">({", ".join(detail_parts)})</span>' if detail_parts else ""

            step_bars += f"""
            <div class="progress-step-row">
              <span class="progress-step-label">{s['name']}</span>
              <div class="progress-step-bar">
                <div class="progress-step-fill" style="width: {s['percent']}%; background: {bar_color};"></div>
              </div>
              <span class="progress-step-pct">{s['percent']}%</span>
              <span class="progress-step-count">{s['completed']}/{s['total']}{detail_tag}</span>
            </div>
            """

    # ETA
    eta_text = "Calculating..."
    if progress.estimated_remaining_seconds is not None:
        if progress.estimated_remaining_seconds == 0:
            eta_text = "Complete"
        else:
            hrs = progress.estimated_remaining_seconds // 3600
            mins = (progress.estimated_remaining_seconds % 3600) // 60
            eta_text = f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m remaining"

    rate_text = ""
    if progress.items_per_minute:
        rate_text = f'<div class="progress-rate">{progress.items_per_minute} items/min</div>'

    # SVG ring
    radius = 54
    circumference = 2 * 3.14159 * radius
    offset = circumference * (1 - p["overall_percent"] / 100)

    html = f"""
    <div class="progress-overview">
      <div class="progress-ring-container">
        <svg class="progress-ring" width="140" height="140" viewBox="0 0 140 140">
          <circle class="progress-ring-bg" cx="70" cy="70" r="{radius}"
                  fill="none" stroke="var(--border)" stroke-width="10"/>
          <circle class="progress-ring-fill" cx="70" cy="70" r="{radius}"
                  fill="none" stroke="var(--primary)" stroke-width="10"
                  stroke-dasharray="{circumference}"
                  stroke-dashoffset="{offset}"
                  stroke-linecap="round"
                  transform="rotate(-90 70 70)"/>
        </svg>
        <div class="progress-ring-text">
          <span class="progress-ring-pct">{p['overall_percent']}%</span>
          <span class="progress-ring-label">complete</span>
        </div>
      </div>
      <div class="progress-details">
        <div class="progress-eta">{eta_text}</div>
        {rate_text}
        <div class="progress-steps-breakdown">
          {step_bars}
        </div>
      </div>
    </div>
    """
    return HTMLResponse(html)


@router.get("/api/analytics/confidence")
def get_confidence_chart(db: Session = Depends(get_db)) -> HTMLResponse:
    """Return confidence distribution as an HTML chart."""
    from src.api.progress import get_confidence_distribution

    data = get_confidence_distribution(db)
    tiers = data["tier_distribution"]
    total = data["total_extractions"] or 1

    colors = {"A": "var(--success)", "B": "var(--info)", "C": "var(--warning)", "D": "var(--danger)"}
    bars = ""
    for tier in ["A", "B", "C", "D"]:
        count = tiers.get(tier, 0)
        pct = round(count / total * 100, 1) if total > 0 else 0
        bars += f"""
        <div class="chart-bar-group">
          <div class="chart-bar-label">Tier {tier}</div>
          <div class="chart-bar-track">
            <div class="chart-bar-fill" style="width: {pct}%; background: {colors[tier]};"></div>
          </div>
          <div class="chart-bar-value">{count} ({pct}%)</div>
        </div>
        """

    return HTMLResponse(f'<div class="chart-bars">{bars}</div>')


@router.get("/api/documents")
def list_documents(db: Session = Depends(get_db)) -> HTMLResponse:
    """List fetched documents with inline metadata editing."""
    # Query with IngestionJob to get job_id for edit forms
    rows = (
        db.execute(
            select(
                IngestionJob.id.label("job_id"),
                DocumentFamily.canonical_title,
                DocumentFamily.short_cite,
                DocumentFamily.subject_area,
                Source.jurisdiction_code,
                DocumentVersion.temporal_status,
                IngestionJob.fetch_url,
                IngestionJob.status.label("job_status"),
                RawArtifact.content_type,
                RawArtifact.size_bytes,
                func.count(NormalizedSourceRecord.id).label("passages"),
            )
            .select_from(IngestionJob)
            .join(DocumentVersion, IngestionJob.document_version_id == DocumentVersion.id)
            .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
            .join(Source, DocumentFamily.source_id == Source.id)
            .outerjoin(RawArtifact, DocumentVersion.id == RawArtifact.document_version_id)
            .outerjoin(NormalizedSourceRecord, DocumentVersion.id == NormalizedSourceRecord.document_version_id)
            .where(IngestionJob.status == IngestionStatus.completed)
            .group_by(
                IngestionJob.id,
                DocumentFamily.canonical_title,
                DocumentFamily.short_cite,
                DocumentFamily.subject_area,
                Source.jurisdiction_code,
                DocumentVersion.temporal_status,
                IngestionJob.fetch_url,
                IngestionJob.status,
                RawArtifact.content_type,
                RawArtifact.size_bytes,
            )
            .order_by(Source.jurisdiction_code, DocumentFamily.short_cite)
            .limit(200)
        )
        .all()
    )

    if not rows:
        return HTMLResponse(
            '<div class="result-panel info" style="margin-top:10px;">'
            'No completed documents yet. Run "Fetch All Pending" first.'
            '</div>'
        )

    table_rows = ""
    for r in rows:
        jid = r.job_id
        jur = html_escape(str(r.jurisdiction_code or ""))
        cite = html_escape(str(r.short_cite or ""))
        title = html_escape(str(r.canonical_title or "")[:80])
        subject = html_escape(str(r.subject_area or ""))
        url = html_escape(str(r.fetch_url or "")[:60])
        ctype = html_escape(str(r.content_type or "—"))
        size_kb = f"{r.size_bytes / 1024:.0f} KB" if r.size_bytes else "—"
        passages = r.passages or 0
        status_val = r.temporal_status.value if hasattr(r.temporal_status, "value") else str(r.temporal_status or "—")

        # Display row with edit toggle
        table_rows += f"""
        <tr id="doc-row-{jid}">
          <td><strong>{jur}</strong></td>
          <td>{cite}</td>
          <td title="{title}" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{title}</td>
          <td>{html_escape(status_val)}</td>
          <td style="text-align:right;">{size_kb}</td>
          <td style="text-align:right;">{passages}</td>
          <td>
            <button class="btn btn-sm"
                    onclick="toggleDocEdit({jid})">Edit</button>
          </td>
        </tr>
        <tr id="doc-edit-{jid}" style="display:none;background:var(--bg-secondary);">
          <td colspan="7" style="padding:8px;">
            <form hx-post="/dashboard/api/edit-document/{jid}"
                  hx-target="#doc-edit-result-{jid}"
                  hx-swap="innerHTML"
                  style="display:flex;flex-wrap:wrap;gap:6px;align-items:end;font-size:12px;">
              <label style="display:flex;flex-direction:column;gap:2px;">
                Jurisdiction
                <input type="text" name="jurisdiction" value="{jur}"
                       style="width:50px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Short Cite
                <input type="text" name="short_cite" value="{cite}"
                       style="width:180px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Title
                <input type="text" name="title" value="{title}"
                       style="width:250px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Subject Area
                <input type="text" name="subject_area" value="{subject}"
                       style="width:120px;font-size:12px;padding:2px 4px;">
              </label>
              <button type="submit" class="btn btn-sm btn-primary" hx-disabled-elt="this">
                <span class="btn-label">Save</span>
                <span class="htmx-indicator"><span class="spinner"></span></span>
              </button>
            </form>
            <div id="doc-edit-result-{jid}" style="margin-top:4px;"></div>
          </td>
        </tr>
        """

    return HTMLResponse(
        f'<div style="margin-top:10px;">'
        f'<div class="table-wrap">'
        f'<table class="review-table">'
        f'<thead><tr>'
        f'<th>Jur.</th><th>Cite</th><th>Title</th><th>Status</th>'
        f'<th style="text-align:right;">Size</th>'
        f'<th style="text-align:right;">Passages</th>'
        f'<th>Actions</th>'
        f'</tr></thead>'
        f'<tbody>{table_rows}</tbody>'
        f'</table></div>'
        f'<div style="font-size:12px;color:var(--text-muted);margin-top:6px;">'
        f'Showing {len(rows)} completed documents. Click Edit to modify metadata.'
        f'</div></div>'
        f'<script>'
        f'function toggleDocEdit(id) {{'
        f'  var el = document.getElementById("doc-edit-" + id);'
        f'  el.style.display = el.style.display === "none" ? "" : "none";'
        f'}}'
        f'</script>'
    )


@router.post("/api/run/pdf-discovery")
def run_pdf_discovery(db: Session = Depends(get_db)) -> HTMLResponse:
    """Parse Orrick PDF tracker and seed new legislation."""
    try:
        from src.ingestion.pdf_tracker import parse_tracker_pdf, seed_from_tracker
        records = parse_tracker_pdf()
        jobs, stats = seed_from_tracker(db, records)
        db.commit()

        # Build informative breakdown
        parts = [f'Parsed <strong>{stats["total_parsed"]}</strong> laws from PDF.']
        if stats["new_jobs"] > 0:
            parts.append(f'Seeded <strong>{stats["new_jobs"]}</strong> new for ingestion.')
        if stats["existing"] > 0:
            parts.append(f'{stats["existing"]} already in database.')

        notes = ""
        if stats["seeded_no_url"]:
            no_url_items = "".join(
                f'<li>{html_escape(s)}</li>' for s in stats["seeded_no_url"][:10]
            )
            extra = f" (+{len(stats['seeded_no_url']) - 10} more)" if len(stats["seeded_no_url"]) > 10 else ""
            notes += (
                f'<div style="margin-top:6px;"><span style="color:var(--warning);">'
                f'{len(stats["seeded_no_url"])} records have no URL in PDF '
                f'(need manual upload):</span>{extra}'
                f'<ul style="margin:4px 0 0 16px;font-size:12px;">{no_url_items}</ul></div>'
            )
        if stats["skipped_no_state"]:
            notes += (
                f'<div style="margin-top:4px;font-size:12px;color:var(--warning);">'
                f'{len(stats["skipped_no_state"])} records had unrecognized state names.</div>'
            )

        panel_class = "success" if not stats["seeded_no_url"] and not stats["skipped_no_state"] else "warning"
        return HTMLResponse(
            f'<div class="result-panel {panel_class}">'
            f'{" ".join(parts)}'
            f'{notes}'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
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

        iapp_note = ""
        if result.iapp_records == 0:
            iapp_note = (
                '<br><span style="font-size:12px;color:var(--warning);">'
                'IAPP: 0 records — place the IAPP PDF at '
                '<code>static/IAPP_Legislation_tracker.pdf</code> to enable.'
                '</span>'
            )

        if result.changed == 0:
            return HTMLResponse(
                f'<div class="result-panel info">'
                f'Checked <strong>{result.checked}</strong> bills against '
                f'PDF tracker (<strong>{result.pdf_records}</strong> matched) and '
                f'IAPP (<strong>{result.iapp_records}</strong> matched). '
                f'No status changes detected.'
                f'{iapp_note}'
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
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
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

        total = summary["completed"] + summary["failed"] + summary["skipped"]
        panel_class = "success" if summary["failed"] == 0 and summary["skipped"] == 0 else "warning"

        failed_note = ""
        if summary["failed"] > 0 or summary["skipped"] > 0:
            failed_note = (
                f'<div style="margin-top:6px;font-size:13px;">'
                f'<span style="color:var(--danger);">{summary["failed"]} failed</span>'
            )
            if summary["skipped"] > 0:
                failed_note += f', <span style="color:var(--warning);">{summary["skipped"]} need review</span>'
            failed_note += (
                f' — switch to the <strong>Failed Documents</strong> tab to upload or edit.'
                f'</div>'
            )

        return HTMLResponse(
            f'<div class="result-panel {panel_class}">'
            f'<strong>{summary["completed"]}/{total}</strong> documents fetched, '
            f'<strong>{summary["total_passages"]}</strong> passages extracted.'
            f'{failed_note}'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
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
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
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
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/extract")
def run_api_extract(
    limit: int | None = None,
    provider: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Run extraction via local LLM or Anthropic API.

    Args:
        limit: Max passages to extract (None = all).
        provider: Force "local" or "anthropic". Defaults to REGS_EXTRACTION_PROVIDER.
    """
    import os

    try:
        # Temporarily override the extraction provider if explicitly requested
        old_provider = os.environ.get("REGS_EXTRACTION_PROVIDER")
        if provider:
            os.environ["REGS_EXTRACTION_PROVIDER"] = provider
            # Clear cached provider so the new setting takes effect
            from src.core.llm_provider import _provider_cache
            _provider_cache.pop("extraction", None)
            _provider_cache.pop("local_extraction", None)

        try:
            from src.ingestion.extractor import run_extraction
            summary = run_extraction(db, limit=limit)
        finally:
            # Restore original provider setting
            if provider:
                if old_provider is not None:
                    os.environ["REGS_EXTRACTION_PROVIDER"] = old_provider
                else:
                    os.environ.pop("REGS_EXTRACTION_PROVIDER", None)
                from src.core.llm_provider import _provider_cache
                _provider_cache.pop("extraction", None)
                _provider_cache.pop("local_extraction", None)

        tokens = summary.get("token_usage", {})
        label = f"via {provider}" if provider else ""
        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Extracted {summary["total_extractions"]} items from '
            f'{summary["records_processed"]} passages {label}. '
            f'Tokens: {tokens.get("total_tokens", 0):,}'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/sync")
def run_sync(
    dry_run: bool = False,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Sync extractions to Policy Navigator (supports dry-run preview)."""
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
        summary = sync_extractions(
            source_url=source_url, target_url=target_url, dry_run=dry_run,
        )

        if dry_run:
            matched = summary.get("source_pending", 0) - summary.get("skipped_no_bridge", 0)
            skipped = summary.get("skipped_no_bridge", 0)
            return HTMLResponse(
                f'<div class="result-panel warning">'
                f'<strong>Dry Run Preview</strong><br>'
                f'Matched (would sync): <strong>{matched}</strong><br>'
                f'Skipped (no bridge):  <strong>{skipped}</strong><br>'
                f'Bridge entries: {summary.get("bridge_entries", 0)}<br>'
                f'Cursor: id &gt; {summary.get("cursor_start", 0)} '
                f'({summary.get("source_pending", 0)} pending)'
                f'</div>'
            )

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Synced {summary["synced"]} rows to Policy Navigator. '
            f'{summary.get("skipped_excluded", 0)} excluded, '
            f'{summary.get("skipped_no_bridge", 0)} no bridge mapping.'
            f'</div>'
        )
    except Exception as e:
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/sync-preflight")
def run_sync_preflight() -> HTMLResponse:
    """Pre-sync verification: bridge validity + schema match.

    Confirms:
      1. law_document_bridge is populated and covers local family_ids.
      2. synced_extractions table exists with the expected columns.
    """
    import os
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    source_url = os.environ.get("REGS_SUPABASE_URL")
    target_url = os.environ.get("REGS_POLICY_NAVIGATOR_URL")

    if not source_url or not target_url:
        return HTMLResponse(
            '<div class="result-panel warning">'
            'Preflight skipped: REGS_SUPABASE_URL and/or '
            'REGS_POLICY_NAVIGATOR_URL not configured.'
            '</div>'
        )

    try:
        source_engine = create_engine(source_url)
        target_engine = create_engine(target_url)
        source_session = sessionmaker(bind=source_engine)()
        target_session = sessionmaker(bind=target_engine)()

        checks: list[dict] = []

        try:
            # ---- Check 1: Bridge validity ----
            from src.core.bridge_monitor import detect_unbridged_families
            report = detect_unbridged_families(source_session, target_session)

            if report.total_families == 0:
                checks.append({
                    "name": "Bridge Validity",
                    "ok": True,
                    "detail": "No document families with extractions yet.",
                })
            elif report.has_gaps:
                gap_list = ", ".join(
                    f"{html_escape(str(f.jurisdiction_code))}/{html_escape(str(f.short_cite or f.family_id))}"
                    for f in report.unbridged[:5]
                )
                extra = f" (+{report.unbridged_families - 5} more)" if report.unbridged_families > 5 else ""
                checks.append({
                    "name": "Bridge Validity",
                    "ok": False,
                    "detail": (
                        f"{report.unbridged_families}/{report.total_families} families "
                        f"missing bridge rows: {gap_list}{extra}"
                    ),
                })
            else:
                checks.append({
                    "name": "Bridge Validity",
                    "ok": True,
                    "detail": (
                        f"All {report.bridged_families} families have bridge mappings."
                    ),
                })

            # ---- Check 2: Schema match ----
            expected_cols = {
                "system_a_extraction_id", "law_id", "extraction_type",
                "payload", "evidence_spans", "confidence_score",
                "confidence_tier", "review_status", "model_id",
                "section_path", "passage_text", "source_created_at",
                "synced_at",
            }
            col_rows = target_session.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'synced_extractions'"
            )).fetchall()
            actual_cols = {row[0] for row in col_rows}

            if not actual_cols:
                checks.append({
                    "name": "Schema Match",
                    "ok": False,
                    "detail": "synced_extractions table does not exist in target DB.",
                })
            else:
                missing = expected_cols - actual_cols
                if missing:
                    checks.append({
                        "name": "Schema Match",
                        "ok": False,
                        "detail": f"Missing columns in synced_extractions: {', '.join(sorted(missing))}",
                    })
                else:
                    checks.append({
                        "name": "Schema Match",
                        "ok": True,
                        "detail": (
                            f"synced_extractions has all {len(expected_cols)} required columns "
                            f"(payload, evidence_spans ready)."
                        ),
                    })

        finally:
            source_session.close()
            target_session.close()

        # ---- Render results ----
        all_ok = all(c["ok"] for c in checks)
        panel_class = "success" if all_ok else "warning"
        icon_ok = '<span style="color:var(--success);">&#10003;</span>'
        icon_fail = '<span style="color:var(--danger);">&#10007;</span>'

        rows_html = ""
        for c in checks:
            icon = icon_ok if c["ok"] else icon_fail
            rows_html += (
                f'<div style="margin:4px 0;">'
                f'{icon} <strong>{html_escape(c["name"])}</strong>: {html_escape(c["detail"])}'
                f'</div>'
            )

        status_msg = "All pre-flight checks passed. Safe to sync." if all_ok else "Some checks failed. Review before syncing."
        return HTMLResponse(
            f'<div class="result-panel {panel_class}">'
            f'<strong>Sync Pre-flight</strong> — {status_msg}'
            f'{rows_html}'
            f'</div>'
        )

    except Exception as e:
        return HTMLResponse(
            f'<div class="result-panel error">Preflight error: {html_escape(str(e))}</div>'
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
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/evaluate")
def run_evaluate(db: Session = Depends(get_db)) -> HTMLResponse:
    """Run evaluation harness against gold standard fixtures."""
    try:
        from src.evaluation.harness import run_evaluation
        result = run_evaluation(db)
        html = f"""
        <div class="result-panel success">
          <strong>Evaluation complete</strong><br>
          Macro F1: <strong>{result.macro_f1:.3f}</strong> |
          Tested: {result.passages_tested} passages |
          Agents: {', '.join(f'{a.agent_name}: F1={a.f1:.3f}' for a in result.agent_scores)}
        </div>
        """
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/compare-models")
def run_compare_models(db: Session = Depends(get_db)) -> HTMLResponse:
    """Run model comparison (Haiku vs new models)."""
    try:
        from src.evaluation.compare_models import compare_models
        result = compare_models(db)

        rows = ""
        for model in result:
            tiers = model.get("tiers", {})
            rows += f"""
            <tr>
              <td><strong>{model['model_id']}</strong></td>
              <td>{model['count']}</td>
              <td>{model['avg_confidence']:.1%}</td>
              <td>{model.get('json_valid_pct', 'N/A')}</td>
              <td>{tiers.get('A', 0)}</td>
              <td>{tiers.get('B', 0)}</td>
              <td>{tiers.get('C', 0)}</td>
              <td>{tiers.get('D', 0)}</td>
            </tr>
            """

        html = f"""
        <div class="result-panel info">
          <table class="review-table" style="margin-top: 8px;">
            <thead>
              <tr>
                <th>Model</th><th>Count</th><th>Avg Conf</th><th>JSON Valid</th>
                <th>A</th><th>B</th><th>C</th><th>D</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        """
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
        )


@router.post("/api/edit-document/{job_id}")
async def edit_document_metadata(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Edit metadata for a document family associated with an ingestion job.

    Accepts form fields: jurisdiction, title, short_cite, subject_area, fetch_url.
    Updates Source.jurisdiction_code, DocumentFamily fields, and IngestionJob.fetch_url.
    """
    form = await request.form()
    job = db.get(IngestionJob, job_id)
    if not job:
        return HTMLResponse(
            f'<div class="result-panel error">Job #{job_id} not found.</div>'
        )

    dv = job.document_version
    family = dv.family if dv else None
    source = family.source if family else None

    if not family or not source:
        return HTMLResponse(
            f'<div class="result-panel error">No document family for job #{job_id}.</div>'
        )

    changes = []

    # Jurisdiction
    new_jur = form.get("jurisdiction", "").strip()
    if new_jur and new_jur != source.jurisdiction_code:
        # Check if a Source already exists for the new jurisdiction
        existing_source = db.scalars(
            select(Source).where(
                Source.jurisdiction_code == new_jur,
                Source.connector_id == source.connector_id,
            )
        ).first()
        if existing_source:
            family.source_id = existing_source.id
            changes.append(f"jurisdiction: {source.jurisdiction_code} → {new_jur}")
        else:
            source.jurisdiction_code = new_jur
            changes.append(f"jurisdiction → {new_jur}")

    # Title
    new_title = form.get("title", "").strip()
    if new_title and new_title != family.canonical_title:
        changes.append(f"title updated")
        family.canonical_title = new_title

    # Short cite
    new_cite = form.get("short_cite", "").strip()
    if new_cite and new_cite != family.short_cite:
        changes.append(f"short_cite: {family.short_cite} → {new_cite}")
        family.short_cite = new_cite

    # Subject area
    new_subject = form.get("subject_area", "").strip()
    if new_subject and new_subject != family.subject_area:
        changes.append(f"subject_area updated")
        family.subject_area = new_subject

    # Fetch URL
    new_url = form.get("fetch_url", "").strip()
    if new_url and new_url != job.fetch_url:
        changes.append(f"fetch_url updated")
        job.fetch_url = new_url
        # If the job was failed, reset to pending so it can be retried
        if job.status in (IngestionStatus.failed, IngestionStatus.requires_manual_review):
            job.status = IngestionStatus.pending
            job.error_message = None
            changes.append("status reset to pending for retry")

    if not changes:
        return HTMLResponse(
            '<div class="result-panel info" style="font-size:13px;">No changes made.</div>'
        )

    try:
        db.commit()
        label = f"{family.source.jurisdiction_code} - {family.short_cite}"
        return HTMLResponse(
            f'<div class="result-panel success" style="font-size:13px;">'
            f'Updated <strong>{html_escape(label)}</strong>: '
            f'{html_escape(", ".join(changes))}'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Save failed: {html_escape(str(e)[:300])}</div>'
        )


@router.post("/api/upload-document")
async def upload_document(
    file: UploadFile,
    job_id: int = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Manually upload a document for a failed/manual-review ingestion job.

    Accepts a PDF or HTML file, stores it as a raw artifact in S3,
    then runs parse+chunk to create passages — same as the automated pipeline
    but skipping the fetch step.
    """
    import hashlib
    from datetime import datetime

    from src.ingestion.connector import _upload_to_s3
    from src.ingestion.parser import parse_and_normalize
    from src.ingestion.pipeline import compute_parse_quality

    job = db.get(IngestionJob, job_id)
    if not job:
        return HTMLResponse(
            f'<div class="result-panel error">Job #{job_id} not found.</div>'
        )

    if job.status not in (
        IngestionStatus.pending,
        IngestionStatus.failed,
        IngestionStatus.requires_manual_review,
    ):
        return HTMLResponse(
            f'<div class="result-panel warning">'
            f'Job #{job_id} cannot receive uploads (status: {job.status.value}). '
            f'Only pending, failed, or manual-review jobs can receive uploads.'
            f'</div>'
        )

    try:
        content_bytes = await file.read()
        if not content_bytes:
            return HTMLResponse(
                '<div class="result-panel error">Empty file uploaded.</div>'
            )

        # Detect content type
        filename = file.filename or ""
        if filename.lower().endswith(".pdf"):
            content_type = "application/pdf"
        elif filename.lower().endswith((".html", ".htm")):
            content_type = "text/html"
        else:
            content_type = file.content_type or "application/octet-stream"

        # Content-addressable storage
        sha256 = hashlib.sha256(content_bytes).hexdigest()

        # Check for duplicate
        existing = db.query(RawArtifact).filter_by(sha256_hash=sha256).first()
        if existing:
            artifact = existing
        else:
            # Upload to S3
            dv = job.document_version
            source = dv.family.source if dv and dv.family else None
            jurisdiction = source.jurisdiction_code if source else "unknown"
            s3_key = f"raw/{jurisdiction}/{sha256}"
            _upload_to_s3(s3_key, content_bytes, content_type)

            artifact = RawArtifact(
                document_version_id=job.document_version_id,
                sha256_hash=sha256,
                s3_key=s3_key,
                content_type=content_type,
                size_bytes=len(content_bytes),
                is_primary=True,
            )
            db.add(artifact)
            db.flush()

        # Update job status to fetched
        job.status = IngestionStatus.fetched
        job.fetch_completed_at = datetime.utcnow()
        job.error_message = None
        db.commit()

        # Parse and chunk
        job.status = IngestionStatus.parsing
        job.parse_started_at = datetime.utcnow()
        db.commit()

        records = parse_and_normalize(db, job, artifact)

        job.status = IngestionStatus.completed
        job.parse_completed_at = datetime.utcnow()
        job.parse_quality_score = compute_parse_quality(records)
        db.commit()

        dv = job.document_version
        label = "unknown"
        if dv and dv.family:
            label = f"{dv.family.source.jurisdiction_code} - {dv.family.short_cite}"

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Uploaded and parsed <strong>{html_escape(label)}</strong>: '
            f'{len(records)} passages extracted from {len(content_bytes):,} bytes '
            f'({html_escape(content_type)}).'
            f'</div>'
        )

    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">'
            f'Upload failed: {html_escape(str(e)[:500])}'
            f'</div>'
        )


@router.post("/api/run/cross-reference")
def run_cross_reference(db: Session = Depends(get_db)) -> HTMLResponse:
    """Cross-reference Orrick and IAPP tracker records to find discrepancies."""
    try:
        from src.ingestion.cross_reference import (
            cross_reference_trackers,
            link_discrepancies_to_jobs,
        )
        from src.ingestion.pdf_tracker import parse_tracker_pdf

        orrick_records = parse_tracker_pdf()

        # Load IAPP records
        iapp_records = []
        try:
            from src.ingestion.iapp_pdf_tracker import IAPP_PDF_PATH, parse_iapp_pdf
            if IAPP_PDF_PATH.exists():
                iapp_records = parse_iapp_pdf()
        except Exception:
            pass
        if not iapp_records:
            try:
                from src.ingestion.iapp_scraper import scrape_tracker
                iapp_records = scrape_tracker()
            except Exception:
                pass

        if not iapp_records:
            return HTMLResponse(
                '<div class="result-panel warning">'
                'IAPP data unavailable. Place the IAPP PDF at '
                '<code>static/IAPP_Legislation_tracker.pdf</code> to enable cross-referencing.'
                '</div>'
            )

        result = cross_reference_trackers(orrick_records, iapp_records)
        link_discrepancies_to_jobs(db, result.discrepancies)

        if not result.discrepancies:
            return HTMLResponse(
                f'<div class="result-panel success">'
                f'Cross-referenced {result.matched} bills between Orrick ({result.orrick_total}) '
                f'and IAPP ({result.iapp_total}). No discrepancies found.'
                f'<br><span style="font-size:12px;color:var(--text-muted);">'
                f'{result.orrick_only} Orrick-only, {result.iapp_only} IAPP-only.</span>'
                f'</div>'
            )

        # Render discrepancy table
        rows_html = ""
        for disc in result.discrepancies:
            field_rows = ""
            for f in disc.fields:
                jid = disc.job_id or 0
                field_rows += (
                    f'<div style="margin:4px 0;font-size:12px;">'
                    f'<strong>{html_escape(f.field_name)}</strong>: '
                    f'<span style="color:var(--info);">Orrick:</span> '
                    f'<code style="word-break:break-all;">{html_escape(f.orrick_value[:100])}</code> '
                )
                if jid and f.field_name in ("url", "title"):
                    form_field = "fetch_url" if f.field_name == "url" else "title"
                    field_rows += (
                        f'<button class="btn btn-sm" style="font-size:11px;padding:1px 6px;" '
                        f'hx-post="/dashboard/api/resolve-discrepancy/{jid}" '
                        f'hx-vals=\'{{"field": "{form_field}", "value": "{html_escape(f.orrick_value)}"}}\' '
                        f'hx-target="#disc-result-{jid}" hx-swap="innerHTML" '
                        f'hx-disabled-elt="this">Use Orrick</button> '
                    )

                field_rows += (
                    f'<br><span style="color:var(--warning);">IAPP:</span> '
                    f'<code style="word-break:break-all;">{html_escape(f.iapp_value[:100])}</code> '
                )
                if jid and f.field_name in ("url", "title"):
                    form_field = "fetch_url" if f.field_name == "url" else "title"
                    field_rows += (
                        f'<button class="btn btn-sm" style="font-size:11px;padding:1px 6px;" '
                        f'hx-post="/dashboard/api/resolve-discrepancy/{jid}" '
                        f'hx-vals=\'{{"field": "{form_field}", "value": "{html_escape(f.iapp_value)}"}}\' '
                        f'hx-target="#disc-result-{jid}" hx-swap="innerHTML" '
                        f'hx-disabled-elt="this">Use IAPP</button> '
                    )

                field_rows += '</div>'

            jid = disc.job_id or 0
            rows_html += f"""
            <tr>
              <td><strong>{html_escape(disc.state_code)}</strong></td>
              <td style="font-size:12px;">{html_escape(disc.orrick_title[:40])}</td>
              <td style="font-size:12px;">{len(disc.fields)} field(s)</td>
              <td>{field_rows}<div id="disc-result-{jid}"></div></td>
            </tr>
            """

        return HTMLResponse(
            f'<div class="result-panel warning">'
            f'Cross-referenced <strong>{result.matched}</strong> bills. '
            f'Found <strong>{len(result.discrepancies)}</strong> with discrepancies.'
            f'<br><span style="font-size:12px;color:var(--text-muted);">'
            f'Orrick: {result.orrick_total} | IAPP: {result.iapp_total} | '
            f'Matched: {result.matched} | Orrick-only: {result.orrick_only} | '
            f'IAPP-only: {result.iapp_only}</span>'
            f'</div>'
            f'<div class="table-wrap" style="margin-top:8px;">'
            f'<table class="review-table">'
            f'<thead><tr>'
            f'<th>Jur.</th><th>Bill</th><th>Diffs</th><th>Details</th>'
            f'</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>'
        )
    except Exception as e:
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e)[:500])}</div>'
        )


@router.post("/api/resolve-discrepancy/{job_id}")
async def resolve_discrepancy(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Apply a chosen value from Orrick or IAPP to resolve a discrepancy.

    Accepts JSON body: {"field": "fetch_url"|"title"|"short_cite", "value": "..."}
    """
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())

    field_name = data.get("field", "")
    value = data.get("value", "")

    if not field_name or not value:
        return HTMLResponse(
            '<span style="color:var(--danger);font-size:12px;">Missing field or value.</span>'
        )

    job = db.get(IngestionJob, job_id)
    if not job:
        return HTMLResponse(
            f'<span style="color:var(--danger);font-size:12px;">Job #{job_id} not found.</span>'
        )

    dv = job.document_version
    family = dv.family if dv else None

    if not family:
        return HTMLResponse(
            '<span style="color:var(--danger);font-size:12px;">No document family found.</span>'
        )

    if field_name == "fetch_url":
        old = job.fetch_url or ""
        job.fetch_url = value
        # Reset failed jobs so they can retry with the new URL
        if job.status in (IngestionStatus.failed, IngestionStatus.requires_manual_review):
            job.status = IngestionStatus.pending
            job.error_message = None
        db.commit()
        return HTMLResponse(
            f'<span style="color:var(--success);font-size:12px;">'
            f'URL updated. Job reset to pending.</span>'
        )
    elif field_name == "title":
        family.canonical_title = f"{family.source.jurisdiction_code} - {value}"
        family.short_cite = value
        db.commit()
        return HTMLResponse(
            f'<span style="color:var(--success);font-size:12px;">'
            f'Title updated to: {html_escape(value[:60])}</span>'
        )
    else:
        return HTMLResponse(
            f'<span style="color:var(--warning);font-size:12px;">'
            f'Unknown field: {html_escape(field_name)}</span>'
        )


@router.get("/api/failed-documents")
def list_failed_documents(db: Session = Depends(get_db)) -> HTMLResponse:
    """List all failed and manual-review ingestion jobs with upload + edit forms."""
    jobs = db.scalars(
        select(IngestionJob)
        .where(IngestionJob.status.in_([
            IngestionStatus.failed,
            IngestionStatus.requires_manual_review,
        ]))
        .order_by(IngestionJob.updated_at.desc())
    ).all()

    if not jobs:
        return HTMLResponse(
            '<div class="result-panel success" style="margin-top:10px;">'
            'No failed documents. All jobs completed successfully.'
            '</div>'
        )

    rows_html = ""
    for job in jobs:
        dv = job.document_version
        label = "unknown"
        jurisdiction = ""
        short_cite = ""
        title = ""
        subject = ""
        if dv and dv.family:
            source = dv.family.source
            jurisdiction = source.jurisdiction_code if source else ""
            short_cite = dv.family.short_cite or ""
            title = dv.family.canonical_title or ""
            subject = dv.family.subject_area or ""
            label = short_cite or title or "unknown"

        status_class = "danger" if job.status == IngestionStatus.failed else "warning"
        status_label = "Failed" if job.status == IngestionStatus.failed else "Needs Review"

        error_short = html_escape(str(job.error_message or "")[:150])
        url_display = html_escape(str(job.fetch_url or ""))
        jid = job.id

        rows_html += f"""
        <tr id="failed-row-{jid}">
          <td><strong>{html_escape(jurisdiction)}</strong></td>
          <td>{html_escape(label)}</td>
          <td><span style="color:var(--{status_class});">{status_label}</span></td>
          <td style="font-size:11px;max-width:250px;">
            <code style="word-break:break-all;">{url_display[:80]}</code>
            <br><span style="color:var(--{status_class});">{error_short}</span>
          </td>
          <td style="white-space:nowrap;">
            <form hx-post="/dashboard/api/upload-document"
                  hx-target="#failed-result-{jid}"
                  hx-swap="innerHTML"
                  hx-encoding="multipart/form-data"
                  style="display:inline-flex;gap:4px;align-items:center;">
              <input type="hidden" name="job_id" value="{jid}">
              <input type="file" name="file" accept=".pdf,.html,.htm,.txt"
                     style="font-size:11px;max-width:150px;" required>
              <button type="submit" class="btn btn-sm btn-primary" hx-disabled-elt="this">
                <span class="btn-label">Upload</span>
                <span class="htmx-indicator"><span class="spinner"></span></span>
              </button>
            </form>
            <button class="btn btn-sm" onclick="toggleFailedEdit({jid})"
                    style="margin-left:4px;">Edit</button>
          </td>
        </tr>
        <tr id="failed-edit-{jid}" style="display:none;background:var(--bg-secondary);">
          <td colspan="5" style="padding:8px;">
            <form hx-post="/dashboard/api/edit-document/{jid}"
                  hx-target="#failed-result-{jid}"
                  hx-swap="innerHTML"
                  style="display:flex;flex-wrap:wrap;gap:6px;align-items:end;font-size:12px;">
              <label style="display:flex;flex-direction:column;gap:2px;">
                Jurisdiction
                <input type="text" name="jurisdiction" value="{html_escape(jurisdiction)}"
                       style="width:50px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Short Cite
                <input type="text" name="short_cite" value="{html_escape(short_cite)}"
                       style="width:180px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Title
                <input type="text" name="title" value="{html_escape(title)}"
                       style="width:200px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Fetch URL
                <input type="text" name="fetch_url" value="{url_display}"
                       style="width:250px;font-size:12px;padding:2px 4px;">
              </label>
              <button type="submit" class="btn btn-sm btn-primary" hx-disabled-elt="this">
                <span class="btn-label">Save</span>
                <span class="htmx-indicator"><span class="spinner"></span></span>
              </button>
            </form>
          </td>
        </tr>
        <tr id="failed-result-row-{jid}" style="display:none;">
          <td colspan="5"><div id="failed-result-{jid}"></div></td>
        </tr>
        """

    return HTMLResponse(
        f'<div style="margin-top:10px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">'
        f'<span style="font-size:13px;color:var(--text-muted);">'
        f'{len(jobs)} documents need attention</span>'
        f'<button class="btn btn-sm"'
        f' hx-get="/dashboard/api/export-failed-txt"'
        f' hx-swap="none"'
        f' hx-disabled-elt="this"'
        f' onclick="window.open(\'/dashboard/api/export-failed-txt\', \'_blank\')">'
        f'<span class="btn-label">Print Failed List</span>'
        f'</button>'
        f'</div>'
        f'<div class="table-wrap">'
        f'<table class="review-table">'
        f'<thead><tr>'
        f'<th>Jur.</th><th>Document</th><th>Status</th>'
        f'<th>Error / URL</th><th>Actions</th>'
        f'</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table></div>'
        f'<div style="font-size:12px;color:var(--text-muted);margin-top:6px;">'
        f'Upload the PDF/HTML manually, or click Edit to fix metadata/URL and retry.'
        f'</div></div>'
        f'<script>'
        f'function toggleFailedEdit(id) {{'
        f'  var el = document.getElementById("failed-edit-" + id);'
        f'  var res = document.getElementById("failed-result-row-" + id);'
        f'  el.style.display = el.style.display === "none" ? "" : "none";'
        f'  res.style.display = el.style.display;'
        f'}}'
        f'</script>'
    )


@router.get("/api/export-failed-txt")
def export_failed_txt(db: Session = Depends(get_db)):
    """Export all failed documents as a plain .txt file for searching/printing."""
    from fastapi.responses import PlainTextResponse

    jobs = db.scalars(
        select(IngestionJob)
        .where(IngestionJob.status.in_([
            IngestionStatus.failed,
            IngestionStatus.requires_manual_review,
        ]))
        .order_by(IngestionJob.updated_at.desc())
    ).all()

    lines = [
        "FAILED DOCUMENT DOWNLOADS",
        "=" * 60,
        f"Generated: {__import__('datetime').datetime.utcnow().isoformat()}Z",
        f"Total: {len(jobs)} documents",
        "",
    ]

    for i, job in enumerate(jobs, 1):
        dv = job.document_version
        jurisdiction = ""
        label = "unknown"
        if dv and dv.family:
            source = dv.family.source
            jurisdiction = source.jurisdiction_code if source else ""
            label = dv.family.short_cite or dv.family.canonical_title or "unknown"

        status_label = "FAILED" if job.status == IngestionStatus.failed else "NEEDS REVIEW"

        lines.append(f"{i}. [{jurisdiction}] {label}")
        lines.append(f"   Status: {status_label}")
        lines.append(f"   URL: {job.fetch_url or 'N/A'}")
        error_msg = (job.error_message or "").split("\n")[0][:200]
        lines.append(f"   Error: {error_msg}")
        if hasattr(job, "ai_suggested_url") and job.ai_suggested_url:
            lines.append(f"   Suggested URL: {job.ai_suggested_url}")
        lines.append("")

    lines.append("=" * 60)
    lines.append("Search tips:")
    lines.append("  - For 403 errors: try downloading the PDF in a browser")
    lines.append("  - For SSL errors: the site may need a different URL or mirror")
    lines.append("  - For 404 errors: search for the bill by name on the state legislature site")

    content = "\n".join(lines)
    return PlainTextResponse(
        content,
        headers={
            "Content-Disposition": "attachment; filename=failed_documents.txt",
        },
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
        return HTMLResponse('<tr><td colspan="7">Item not found</td></tr>')

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
        f'<td colspan="6" style="color: {color}; font-style: italic;">'
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
