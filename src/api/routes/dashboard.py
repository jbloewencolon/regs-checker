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
    from src.api.progress import compute_pipeline_progress

    stats = _get_pipeline_stats(db)
    export_files = _get_export_files()
    progress = compute_pipeline_progress(db)

    return _render(request, "dashboard.html", {
        "stats": stats,
        "export_files": export_files,
        "progress": progress.to_dict(),
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
        step_bars += f"""
        <div class="progress-step-row">
          <span class="progress-step-label">{s['name']}</span>
          <div class="progress-step-bar">
            <div class="progress-step-fill" style="width: {s['percent']}%; background: {bar_color};"></div>
          </div>
          <span class="progress-step-pct">{s['percent']}%</span>
          <span class="progress-step-count">{s['completed']}/{s['total']}</span>
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


@router.post("/api/run/pdf-discovery")
def run_pdf_discovery(db: Session = Depends(get_db)) -> HTMLResponse:
    """Parse Orrick PDF tracker and seed new legislation."""
    try:
        from src.ingestion.pdf_tracker import parse_tracker_pdf, seed_from_tracker
        records = parse_tracker_pdf()
        jobs = seed_from_tracker(db, records)
        db.commit()
        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Parsed {len(records)} laws from PDF, seeded {len(jobs)} new ones for ingestion.'
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

        if result.changed == 0:
            return HTMLResponse(
                f'<div class="result-panel info">'
                f'Checked {result.checked} bills against '
                f'PDF tracker ({result.pdf_records} records) and '
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

        # Build failure detail HTML
        failure_html = ""
        for f in summary.get("failed_jobs", []):
            failure_html += (
                f'<li><strong>{html_escape(str(f["label"]))}</strong> — '
                f'<code style="word-break:break-all;">{html_escape(str(f["url"]))}</code><br>'
                f'<span style="color:var(--danger);">{html_escape(str(f["error"]))}</span></li>'
            )

        manual_html = ""
        for m in summary.get("manual_review_jobs", []):
            suggested = m.get("ai_suggested_url")
            suggested_line = (
                f'<br>AI-suggested: <code style="word-break:break-all;">{html_escape(str(suggested))}</code>'
                if suggested else ""
            )
            manual_html += (
                f'<li><strong>{html_escape(str(m["label"]))}</strong> — '
                f'<code style="word-break:break-all;">{html_escape(str(m["url"]))}</code>'
                f'{suggested_line}<br>'
                f'<span style="color:var(--warning);">{html_escape(str(m["error"]))}</span></li>'
            )

        details = ""
        if failure_html:
            details += (
                f'<div style="margin-top:8px;"><strong>Failed downloads '
                f'(need manual doc insertion):</strong>'
                f'<ul style="margin:4px 0 0 16px;font-size:13px;">{failure_html}</ul></div>'
            )
        if manual_html:
            details += (
                f'<div style="margin-top:8px;"><strong>Needs manual review:</strong>'
                f'<ul style="margin:4px 0 0 16px;font-size:13px;">{manual_html}</ul></div>'
            )

        panel_class = "success" if summary["failed"] == 0 else "warning"
        return HTMLResponse(
            f'<div class="result-panel {panel_class}">'
            f'Completed: {summary["completed"]} documents, '
            f'{summary["total_passages"]} passages extracted. '
            f'{summary["failed"]} failed, {summary["skipped"]} need review.'
            f'{details}'
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
