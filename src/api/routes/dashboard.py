"""Dashboard routes — HTML UI for the extraction pipeline.

Serves an HTMX-powered dashboard with:
  - Real-time progress tracking with % completion and ETA
  - Pipeline step controls (run each step or run-all)
  - Analytics: confidence breakdown, model comparison, jurisdiction view
  - Review queue with confidence component visualization

Split into sub-modules:
  - _dashboard_helpers: shared constants, lock, render, stats helpers
  - review_routes: review queue page + approve/reject
  - tracker_routes: tracker CRUD + import/export
"""

from __future__ import annotations

import threading
from html import escape as html_escape
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.db.engine import get_db
from src.db.models import (
    ApplicabilityCondition,
    ConfidenceTier,
    DocumentFamily,
    DocumentVersion,
    Extraction,
    ExtractionJob,
    ExtractionType,
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
    ObligationDependency,
    RawArtifact,
    ReviewAction,
    ReviewQueueItem,
    ReviewStatus,
    Source,
)

from src.api.routes._dashboard_helpers import (
    EXPORT_DIR,
    _acquire_pipeline_lock,
    _get_export_files,
    _get_pipeline_stats,
    _pipeline_lock,
    _render,
)
from src.api.routes.review_routes import router as review_router
from src.api.routes.tracker_routes import (
    _tracker_csv_to_records,
    router as tracker_router,
)

router = APIRouter()
router.include_router(review_router)
router.include_router(tracker_router)


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


@router.get("/triage", response_class=HTMLResponse)
def triage_page(
    request: Request,
    filter: str = "all",
    db: Session = Depends(get_db),
):
    """Section triage review page — accordion by state with passage details."""
    from src.db.models import SectionTriageResult, TriageDecision, TriageMethod

    # Get totals — gracefully handle missing/empty table
    totals = {"relevant": 0, "uncertain": 0, "not_relevant": 0, "quality_fail": 0, "total": 0}
    try:
        for row in db.execute(
            text("SELECT decision, method, count(*) as cnt FROM section_triage_results GROUP BY decision, method")
        ).all():
            decision, method, cnt = row
            totals["total"] += cnt
            if method == "quality_fail":
                totals["quality_fail"] += cnt
            elif decision in totals:
                totals[decision] += cnt
    except Exception:
        db.rollback()

    if totals["total"] == 0:
        return _render(request, "triage.html", {
            "states": [],
            "totals": totals,
            "filter": filter,
        })

    # Build query for triage results joined with source records and documents
    query = (
        select(
            SectionTriageResult,
            NormalizedSourceRecord.section_path,
            NormalizedSourceRecord.text_content,
            NormalizedSourceRecord.document_version_id,
            DocumentFamily.canonical_title,
            DocumentFamily.short_cite,
            Source.jurisdiction_code,
            Source.jurisdiction_name,
        )
        .join(NormalizedSourceRecord, SectionTriageResult.source_record_id == NormalizedSourceRecord.id)
        .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
        .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
        .join(Source, DocumentFamily.source_id == Source.id)
    )

    if filter == "relevant":
        query = query.where(SectionTriageResult.decision == "relevant")
    elif filter == "not_relevant":
        query = query.where(SectionTriageResult.decision == "not_relevant")
    elif filter == "uncertain":
        query = query.where(SectionTriageResult.decision == "uncertain")
    elif filter == "quality_fail":
        query = query.where(SectionTriageResult.method == "quality_fail")

    query = query.order_by(
        Source.jurisdiction_code,
        DocumentFamily.canonical_title,
        NormalizedSourceRecord.ordinal,
    )

    rows = db.execute(query).all()

    # Group by state → document → passages
    from collections import OrderedDict
    states_dict: dict[str, dict] = OrderedDict()

    for row in rows:
        triage = row[0]
        section_path = row[1]
        text_content = row[2]
        dv_id = row[3]
        doc_title = row[4] or "Untitled"
        short_cite = row[5]
        jur_code = row[6] or "??"
        jur_name = row[7] or jur_code

        if jur_code not in states_dict:
            states_dict[jur_code] = {
                "jurisdiction_code": jur_code,
                "jurisdiction_name": jur_name,
                "relevant": 0,
                "not_relevant": 0,
                "uncertain": 0,
                "quality_fail": 0,
                "documents": OrderedDict(),
            }

        state = states_dict[jur_code]
        doc_key = f"{dv_id}:{doc_title}"
        if doc_key not in state["documents"]:
            state["documents"][doc_key] = {
                "title": doc_title,
                "short_cite": short_cite,
                "relevant": 0,
                "not_relevant": 0,
                "uncertain": 0,
                "passages": [],
            }

        doc = state["documents"][doc_key]

        # Count
        decision_val = triage.decision.value if hasattr(triage.decision, "value") else str(triage.decision)
        method_val = triage.method.value if hasattr(triage.method, "value") else str(triage.method)

        if method_val == "quality_fail":
            state["quality_fail"] += 1
        elif decision_val in ("relevant", "not_relevant", "uncertain"):
            state[decision_val] += 1
            doc[decision_val] += 1

        # Preview: first 120 chars, full text for expansion
        preview = (text_content or "")[:120].replace("\n", " ")
        if len(text_content or "") > 120:
            preview += "..."

        doc["passages"].append({
            "id": triage.id,
            "decision": decision_val,
            "method": method_val,
            "section_path": section_path,
            "text_preview": html_escape(preview),
            "text_full": html_escape(text_content or ""),
            "matched_keywords": triage.matched_keywords or [],
            "orrick_terms_checked": triage.orrick_terms_checked or [],
            "llm_reasoning": triage.llm_reasoning,
            "pdf_quality_score": triage.pdf_quality_score,
            "quality_flags": triage.quality_flags or [],
            "confidence": triage.confidence or 0.0,
        })

    # Convert OrderedDicts to lists for template
    states = []
    for state in states_dict.values():
        state["documents"] = list(state["documents"].values())
        states.append(state)

    return _render(request, "triage.html", {
        "states": states,
        "totals": totals,
        "filter": filter,
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

    pending_html = ""
    if stats['pending_ingestion'] > 0:
        pending_html = f"""
    <span class="stat">
      <span class="stat-value" style="color: var(--warning);">{stats['pending_ingestion']}</span>
      <span class="stat-label">pending fetch</span>
    </span>"""

    html = f"""
    <span class="stat">
      <span class="stat-label">Progress</span>
      <span class="stat-value">{progress.overall_percent}%</span>
    </span>
    {pending_html}
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


@router.get("/api/triage-stats")
def triage_stats(db: Session = Depends(get_db)) -> HTMLResponse:
    """Return live triage summary stats as an HTML fragment."""
    from src.db.models import SectionTriageResult

    totals = {"relevant": 0, "uncertain": 0, "not_relevant": 0, "quality_fail": 0, "total": 0}
    try:
        for row in db.execute(
            text("SELECT decision, method, count(*) as cnt FROM section_triage_results GROUP BY decision, method")
        ).all():
            decision, method, cnt = row
            totals["total"] += cnt
            if method == "quality_fail":
                totals["quality_fail"] += cnt
            elif decision in totals:
                totals[decision] += cnt
    except Exception:
        db.rollback()

    return HTMLResponse(
        f'<div class="triage-summary"'
        f'     hx-get="/dashboard/api/triage-stats"'
        f'     hx-trigger="every 5s"'
        f'     hx-swap="outerHTML">'
        f'  <div class="triage-stat">'
        f'    <span class="triage-stat-value">{totals["relevant"]}</span>'
        f'    <span class="triage-stat-label">Relevant</span>'
        f'  </div>'
        f'  <div class="triage-stat">'
        f'    <span class="triage-stat-value triage-uncertain">{totals["uncertain"]}</span>'
        f'    <span class="triage-stat-label">Uncertain</span>'
        f'  </div>'
        f'  <div class="triage-stat">'
        f'    <span class="triage-stat-value triage-skipped">{totals["not_relevant"]}</span>'
        f'    <span class="triage-stat-label">Skipped</span>'
        f'  </div>'
        f'  <div class="triage-stat">'
        f'    <span class="triage-stat-value triage-quality-fail">{totals["quality_fail"]}</span>'
        f'    <span class="triage-stat-label">Quality Fail</span>'
        f'  </div>'
        f'  <div class="triage-stat">'
        f'    <span class="triage-stat-value" style="color: var(--text-muted);">{totals["total"]}</span>'
        f'    <span class="triage-stat-label">Total Passages</span>'
        f'  </div>'
        f'</div>'
    )


@router.get("/api/pipeline-tracker")
def pipeline_tracker(db: Session = Depends(get_db)) -> HTMLResponse:
    """Return fetch/parse/triage stats as an HTML fragment."""
    from src.db.models import SectionTriageResult

    # --- Fetch stats ---
    total_jobs = db.scalar(
        select(func.count()).select_from(IngestionJob)
    ) or 0

    fetched = db.scalar(
        select(func.count()).where(
            IngestionJob.fetch_completed_at.isnot(None)
        )
    ) or 0

    fetch_failed = db.scalar(
        select(func.count()).where(
            IngestionJob.status == IngestionStatus.failed,
            IngestionJob.fetch_completed_at.is_(None),
        )
    ) or 0

    fetch_in_progress = db.scalar(
        select(func.count()).where(
            IngestionJob.status == IngestionStatus.fetching,
        )
    ) or 0

    # --- Parse stats ---
    parsed = db.scalar(
        select(func.count()).where(
            IngestionJob.parse_completed_at.isnot(None)
        )
    ) or 0

    parse_failed = db.scalar(
        select(func.count()).where(
            IngestionJob.status == IngestionStatus.failed,
            IngestionJob.fetch_completed_at.isnot(None),
            IngestionJob.parse_completed_at.is_(None),
        )
    ) or 0

    parse_in_progress = db.scalar(
        select(func.count()).where(
            IngestionJob.status == IngestionStatus.parsing,
        )
    ) or 0

    # --- Avg durations ---
    avg_fetch_sec = db.scalar(
        select(
            func.avg(
                func.extract("epoch", IngestionJob.fetch_completed_at)
                - func.extract("epoch", IngestionJob.fetch_started_at)
            )
        ).where(
            IngestionJob.fetch_started_at.isnot(None),
            IngestionJob.fetch_completed_at.isnot(None),
        )
    )

    avg_parse_sec = db.scalar(
        select(
            func.avg(
                func.extract("epoch", IngestionJob.parse_completed_at)
                - func.extract("epoch", IngestionJob.parse_started_at)
            )
        ).where(
            IngestionJob.parse_started_at.isnot(None),
            IngestionJob.parse_completed_at.isnot(None),
        )
    )

    # --- Triage stats ---
    triage_total = 0
    triage_relevant = 0
    triage_skipped = 0
    triage_uncertain = 0
    avg_triage_sec = None
    try:
        triage_total = db.scalar(
            select(func.count()).select_from(SectionTriageResult)
        ) or 0
        if triage_total > 0:
            triage_relevant = db.scalar(
                select(func.count()).where(SectionTriageResult.decision == "relevant")
            ) or 0
            triage_skipped = db.scalar(
                select(func.count()).where(SectionTriageResult.decision == "not_relevant")
            ) or 0
            triage_uncertain = db.scalar(
                select(func.count()).where(SectionTriageResult.decision == "uncertain")
            ) or 0

            total_passages = db.scalar(
                select(func.count()).select_from(NormalizedSourceRecord)
            ) or 0
            if total_passages > 0 and triage_total > 0:
                # Estimate avg triage time from parsed job duration minus parse time
                # divided by passage count (rough proxy)
                pass
    except Exception:
        db.rollback()

    # --- Format helpers ---
    def fmt_duration(seconds):
        if seconds is None:
            return "—"
        if seconds < 1:
            return f"{seconds * 1000:.0f}ms"
        if seconds < 60:
            return f"{seconds:.1f}s"
        mins = seconds / 60
        return f"{mins:.1f}m"

    def pct(n, total):
        if total == 0:
            return 0
        return round(n / total * 100, 1)

    fetch_pct = pct(fetched, total_jobs)
    parse_pct = pct(parsed, total_jobs)
    triage_pct = pct(triage_total, db.scalar(select(func.count()).select_from(NormalizedSourceRecord)) or 1)

    # Build status badges
    def status_badge(in_prog, failed):
        parts = []
        if in_prog > 0:
            parts.append(f'<span class="tracker-badge running">{in_prog} running</span>')
        if failed > 0:
            parts.append(f'<span class="tracker-badge failed">{failed} failed</span>')
        return " ".join(parts)

    html = f"""
    <div class="tracker-grid">
      <div class="tracker-card">
        <div class="tracker-header">Fetched</div>
        <div class="tracker-value">{fetched}<span class="tracker-total">/{total_jobs}</span></div>
        <div class="tracker-bar"><div class="tracker-bar-fill" style="width:{fetch_pct}%"></div></div>
        <div class="tracker-footer">
          <span class="tracker-pct">{fetch_pct}%</span>
          {status_badge(fetch_in_progress, fetch_failed)}
        </div>
      </div>

      <div class="tracker-card">
        <div class="tracker-header">Parsed</div>
        <div class="tracker-value">{parsed}<span class="tracker-total">/{total_jobs}</span></div>
        <div class="tracker-bar"><div class="tracker-bar-fill parsed" style="width:{parse_pct}%"></div></div>
        <div class="tracker-footer">
          <span class="tracker-pct">{parse_pct}%</span>
          {status_badge(parse_in_progress, parse_failed)}
        </div>
      </div>

      <div class="tracker-card">
        <div class="tracker-header">Triaged</div>
        <div class="tracker-value">{triage_total}<span class="tracker-total"> passages</span></div>
        <div class="tracker-bar"><div class="tracker-bar-fill triaged" style="width:{triage_pct}%"></div></div>
        <div class="tracker-footer">
          <span class="tracker-detail">{triage_relevant} relevant</span>
          <span class="tracker-detail uncertain">{triage_uncertain} uncertain</span>
          <span class="tracker-detail skipped">{triage_skipped} skipped</span>
        </div>
      </div>

      <div class="tracker-card">
        <div class="tracker-header">Avg. Time</div>
        <div class="tracker-timings">
          <div class="tracker-timing-row">
            <span class="tracker-timing-label">Fetch</span>
            <span class="tracker-timing-value">{fmt_duration(avg_fetch_sec)}</span>
          </div>
          <div class="tracker-timing-row">
            <span class="tracker-timing-label">Parse</span>
            <span class="tracker-timing-value">{fmt_duration(avg_parse_sec)}</span>
          </div>
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
                DocumentFamily.metadata_.label("family_metadata"),
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
                DocumentFamily.metadata_,
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
        raw_url = str(r.fetch_url or "")
        url = html_escape(raw_url)
        url_short = html_escape(raw_url[:50])
        ctype = html_escape(str(r.content_type or "—"))
        size_kb = f"{r.size_bytes / 1024:.0f} KB" if r.size_bytes else "—"
        passages = r.passages or 0
        status_val = r.temporal_status.value if hasattr(r.temporal_status, "value") else str(r.temporal_status or "—")

        # Extract bill_id and IAPP fields from family metadata
        meta = r.family_metadata or {}
        bill_id = meta.get("bill_id", "")
        iapp_bill_number = meta.get("iapp_bill_number", "")
        iapp_status = meta.get("iapp_status", "")

        # Bill ID: show Orrick bill_id, IAPP bill_number, or both if they differ
        bill_id_display = html_escape(bill_id or iapp_bill_number or "—")
        bill_id_extra = ""
        if bill_id and iapp_bill_number and bill_id != iapp_bill_number:
            bill_id_extra = (
                f'<br><span style="font-size:10px;color:var(--text-muted);">'
                f'IAPP: {html_escape(iapp_bill_number)}</span>'
            )

        # Bill Status: prefer raw IAPP status, fall back to normalized TemporalStatus
        bill_status_display = html_escape(iapp_status or status_val)

        # URL display: clickable link, truncated
        url_cell = "—"
        if raw_url:
            url_cell = (
                f'<a href="{url}" target="_blank" rel="noopener" '
                f'style="font-size:11px;word-break:break-all;" '
                f'title="{url}">{url_short}{"…" if len(raw_url) > 50 else ""}</a>'
            )

        # Display row with edit toggle
        table_rows += f"""
        <tr id="doc-row-{jid}">
          <td><strong>{jur}</strong></td>
          <td>{cite}</td>
          <td title="{title}" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{title}</td>
          <td>{bill_id_display}{bill_id_extra}</td>
          <td>{bill_status_display}</td>
          <td style="max-width:160px;">{url_cell}</td>
          <td style="text-align:right;">{size_kb}</td>
          <td style="text-align:right;">{passages}</td>
          <td>
            <button class="btn btn-sm"
                    onclick="toggleDocEdit({jid})">Edit</button>
          </td>
        </tr>
        <tr id="doc-edit-{jid}" style="display:none;background:var(--bg-secondary);">
          <td colspan="9" style="padding:8px;">
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
                       style="width:150px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Title
                <input type="text" name="title" value="{title}"
                       style="width:200px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Bill ID (Orrick)
                <input type="text" name="bill_id" value="{html_escape(bill_id)}"
                       style="width:100px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Bill # (IAPP)
                <input type="text" name="iapp_bill_number" value="{html_escape(iapp_bill_number)}"
                       style="width:100px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                IAPP Status
                <input type="text" name="iapp_status" value="{html_escape(iapp_status)}"
                       style="width:140px;font-size:12px;padding:2px 4px;">
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
        f'<th>Jur.</th><th>Cite</th><th>Title</th><th>Bill ID</th><th>Bill Status</th>'
        f'<th>Source URL</th>'
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
def run_csv_discovery(db: Session = Depends(get_db)) -> HTMLResponse:
    """Seed legislation from the ai_law_tracker.csv (primary discovery source)."""
    if not _acquire_pipeline_lock():
        return HTMLResponse(
            '<div class="result-panel info">A pipeline operation is already running. Please wait.</div>'
        )
    try:
        from src.ingestion.pdf_tracker import seed_from_tracker

        records = _tracker_csv_to_records()
        if not records:
            return HTMLResponse(
                '<div class="result-panel warning">'
                'No records in <code>static/ai_law_tracker.csv</code>. '
                'Add rows via the Law Tracker tab first.</div>'
            )

        jobs, stats = seed_from_tracker(db, records)
        db.commit()

        parts = [f'Loaded <strong>{stats["total_parsed"]}</strong> laws from tracker CSV.']
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
                f'{len(stats["seeded_no_url"])} records have no Source URL '
                f'(add URLs in the Law Tracker tab):</span>{extra}'
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
    finally:
        _pipeline_lock.release()


@router.post("/api/run/status-check")
def run_status_check(
    dry_run: bool = False,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Check bill statuses against Orrick and IAPP trackers."""
    if not _acquire_pipeline_lock():
        return HTMLResponse(
            '<div class="result-panel info">A pipeline operation is already running. Please wait.</div>'
        )
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
                f'Checked <strong>{result.checked}</strong> bills. '
                f'Cross-referenced against PDF tracker '
                f'(<strong>{result.pdf_matched}</strong>/{result.checked} matched '
                f'from {result.pdf_records} index records) and '
                f'IAPP (<strong>{result.iapp_matched}</strong>/{result.checked} matched '
                f'from {result.iapp_records} index records). '
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
    finally:
        _pipeline_lock.release()


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

        cancelled_note = ""
        if summary.get("cancelled"):
            cancelled_note = (
                '<div style="margin-top:6px;font-size:13px;color:var(--warning);">'
                'Pipeline was terminated by user. Remaining jobs still pending.'
                '</div>'
            )
            panel_class = "warning"

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
            f'{cancelled_note}'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/fetch/cancel")
def cancel_fetch() -> HTMLResponse:
    """Signal the running fetch pipeline to stop after the current job."""
    from src.ingestion.pipeline import is_cancelled, request_cancel

    if is_cancelled():
        return HTMLResponse(
            '<div class="result-panel info">Cancellation already requested.</div>'
        )
    request_cancel()
    return HTMLResponse(
        '<div class="result-panel warning">Termination requested — pipeline will stop after the current job completes.</div>'
    )


@router.post("/api/run/fetch/reset")
def reset_fetch(db: Session = Depends(get_db)) -> HTMLResponse:
    """Reset non-completed ingestion jobs for re-parsing.

    Jobs that already have a raw artifact (file downloaded) are set to
    'fetched' so the pipeline skips the download and only re-parses.
    Jobs without a raw artifact are set to 'pending' for full re-fetch.
    Completed jobs are never touched.
    """
    from sqlalchemy import update

    try:
        # Find jobs eligible for reset
        resettable_statuses = [
            IngestionStatus.failed,
            IngestionStatus.requires_manual_review,
            IngestionStatus.fetching,
            IngestionStatus.fetched,
            IngestionStatus.parsing,
        ]

        jobs = db.scalars(
            select(IngestionJob).where(
                IngestionJob.status.in_(resettable_statuses)
            )
        ).all()

        if not jobs:
            return HTMLResponse(
                '<div class="result-panel info">No jobs to reset — all are either pending or completed.</div>'
            )

        # Check which jobs already have a raw artifact downloaded
        reset_to_fetched = 0
        reset_to_pending = 0
        skipped_manual_review = 0

        for job in jobs:
            # Manual-review jobs had bad content (Discovery rejected it).
            # Don't re-parse the same garbage — skip them unless user
            # provides a new URL.
            if job.status == IngestionStatus.requires_manual_review:
                skipped_manual_review += 1
                continue

            has_artifact = db.scalar(
                select(func.count()).where(
                    RawArtifact.document_version_id == job.document_version_id
                )
            ) or 0

            if has_artifact > 0:
                # File already downloaded — only re-parse
                job.status = IngestionStatus.fetched
                job.error_message = None
                job.parse_started_at = None
                job.parse_completed_at = None
                job.parse_quality_score = None
                reset_to_fetched += 1
            else:
                # No file yet — full re-fetch
                job.status = IngestionStatus.pending
                job.error_message = None
                job.fetch_started_at = None
                job.fetch_completed_at = None
                job.parse_started_at = None
                job.parse_completed_at = None
                reset_to_pending += 1

        db.commit()

        total = reset_to_fetched + reset_to_pending
        if total == 0 and skipped_manual_review > 0:
            return HTMLResponse(
                f'<div class="result-panel info">'
                f'No jobs to reset. {skipped_manual_review} manual-review jobs '
                f'were skipped (bad content — update their URLs first).'
                f'</div>'
            )
        parts = [f'Reset <strong>{total}</strong> jobs.']
        if reset_to_fetched > 0:
            parts.append(f'{reset_to_fetched} will re-parse only (files already downloaded).')
        if reset_to_pending > 0:
            parts.append(f'{reset_to_pending} will re-fetch and parse.')
        if skipped_manual_review > 0:
            parts.append(
                f'<span style="color:var(--warning);">{skipped_manual_review} manual-review '
                f'jobs skipped (bad content — update URLs first).</span>'
            )

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'{" ".join(parts)}'
            f'</div>',
            headers={"HX-Trigger": "pipelineReset"},
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
        )


_triage_progress: dict | None = None
_triage_lock = threading.Lock()


@router.post("/api/run/triage")
def run_triage_endpoint(db: Session = Depends(get_db)) -> HTMLResponse:
    """Run section triage on all untriaged passages, streaming progress."""
    global _triage_progress

    if _triage_progress is not None and _triage_progress.get("running"):
        return HTMLResponse(
            '<div class="result-panel info">Triage is already running. '
            'Check the Triage tab for live results.</div>'
        )

    try:
        from src.ingestion.extractor import run_triage

        _triage_progress = {"running": True, "relevant": 0, "uncertain": 0, "skipped": 0, "total": 0, "done": 0}

        def _on_progress(msg: str):
            pass  # Logged by run_triage internally

        summary = run_triage(db, on_progress=_on_progress)

        _triage_progress = {
            "running": False,
            **summary,
        }

        if summary["total"] == 0:
            return HTMLResponse(
                '<div class="result-panel info">No untriaged passages found. '
                'All passages have already been triaged, or no documents have been parsed yet.</div>'
            )

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Triaged <strong>{summary["total"]}</strong> passages: '
            f'<span style="color:var(--success);">{summary["relevant"]} relevant</span>, '
            f'<span style="color:var(--warning);">{summary["uncertain"]} uncertain</span>, '
            f'<span style="color:var(--text-muted);">{summary["skipped"]} skipped</span>. '
            f'<a href="/dashboard/triage">View results &rarr;</a>'
            f'</div>'
        )
    except Exception as e:
        _triage_progress = None
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Triage error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/triage/reset")
def reset_triage(db: Session = Depends(get_db)) -> HTMLResponse:
    """Clear uncertain and low-confidence triage results for re-triaging.

    Only resets:
    - 'uncertain' decisions (passthrough / unconfident LLM results)
    - 'quality_fail' decisions
    - Any result with confidence < 0.7

    High-confidence 'relevant' and 'not_relevant' decisions are preserved,
    as are manually overridden results.
    """
    from sqlalchemy import or_, delete
    from src.db.models import SectionTriageResult, TriageDecision
    from src.ingestion.extractor import _ensure_triage_table

    try:
        _ensure_triage_table(db)

        # Count what will be reset
        reset_filter = or_(
            SectionTriageResult.decision == TriageDecision.uncertain,
            SectionTriageResult.confidence < 0.7,
        )

        count = db.scalar(
            select(func.count()).where(reset_filter)
        ) or 0

        if count == 0:
            return HTMLResponse(
                '<div class="result-panel info">No low-confidence or uncertain results to reset. '
                'All triage decisions are confident.</div>'
            )

        db.execute(
            delete(SectionTriageResult).where(reset_filter)
        )
        db.commit()

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Cleared <strong>{count}</strong> uncertain/low-confidence triage results. '
            f'High-confidence and manually reviewed results preserved. '
            f'Hit <strong>Triage Passages</strong> to re-triage with LLM.'
            f'</div>',
            headers={"HX-Trigger": "pipelineReset"},
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Reset error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/extract/reset")
def reset_extractions(db: Session = Depends(get_db)) -> HTMLResponse:
    """Clear all extractions and extraction jobs so passages can be re-extracted.

    Cascading deletes: review_queue → review_actions, extractions,
    extraction_jobs. Triage results are preserved.
    """
    from sqlalchemy import delete

    try:
        # Count before clearing
        ext_count = db.scalar(select(func.count()).select_from(Extraction)) or 0
        job_count = db.scalar(select(func.count()).select_from(ExtractionJob)) or 0

        if ext_count == 0 and job_count == 0:
            return HTMLResponse(
                '<div class="result-panel info">No extractions to clear.</div>'
            )

        # Delete in FK order: review_actions → review_queue → extractions → extraction_jobs
        # Also clear downstream: applicability_conditions, obligation_dependencies
        db.execute(delete(ApplicabilityCondition))
        db.execute(delete(ObligationDependency))
        db.execute(delete(ReviewAction))
        db.execute(delete(ReviewQueueItem))
        db.execute(delete(Extraction))
        db.execute(delete(ExtractionJob))

        # Clear cached bill_context so it can be rebuilt
        db.execute(text(
            "UPDATE document_versions SET metadata = metadata - 'bill_context' "
            "WHERE metadata ? 'bill_context'"
        ))
        db.commit()

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Cleared <strong>{ext_count}</strong> extractions and '
            f'<strong>{job_count}</strong> extraction jobs. '
            f'Also cleared dependency graph, applicability conditions, and review queue. '
            f'Triage results preserved. Hit <strong>Extract</strong> to re-run.'
            f'</div>',
            headers={"HX-Trigger": "pipelineReset"},
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Reset error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/dependency-graph/reset")
def reset_dependency_graph(db: Session = Depends(get_db)) -> HTMLResponse:
    """Clear all obligation dependency edges."""
    from sqlalchemy import delete

    try:
        count = db.scalar(
            select(func.count()).select_from(ObligationDependency)
        ) or 0

        if count == 0:
            return HTMLResponse(
                '<div class="result-panel info">No dependency edges to clear.</div>'
            )

        db.execute(delete(ObligationDependency))
        db.commit()

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Cleared <strong>{count}</strong> dependency edges. '
            f'Hit <strong>Build All Pending</strong> to rebuild.'
            f'</div>',
            headers={"HX-Trigger": "pipelineReset"},
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Reset error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/condition-parse/reset")
def reset_applicability_conditions(db: Session = Depends(get_db)) -> HTMLResponse:
    """Clear all parsed applicability condition trees."""
    from sqlalchemy import delete

    try:
        count = db.scalar(
            select(func.count()).select_from(ApplicabilityCondition)
        ) or 0

        if count == 0:
            return HTMLResponse(
                '<div class="result-panel info">No condition trees to clear.</div>'
            )

        db.execute(delete(ApplicabilityCondition))
        db.commit()

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Cleared <strong>{count}</strong> condition nodes. '
            f'Hit <strong>Parse All Pending</strong> to rebuild.'
            f'</div>',
            headers={"HX-Trigger": "pipelineReset"},
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Reset error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/review/reset")
def reset_review(db: Session = Depends(get_db)) -> HTMLResponse:
    """Reset all review decisions back to pending.

    Clears review actions (audit log) and resets all review queue items
    and extraction review statuses to pending.
    """
    from sqlalchemy import delete, update

    try:
        action_count = db.scalar(
            select(func.count()).select_from(ReviewAction)
        ) or 0
        reviewed_count = db.scalar(
            select(func.count()).select_from(ReviewQueueItem).where(
                ReviewQueueItem.status != ReviewStatus.pending
            )
        ) or 0

        if action_count == 0 and reviewed_count == 0:
            return HTMLResponse(
                '<div class="result-panel info">No review decisions to reset. All items are already pending.</div>'
            )

        # Clear review actions audit log
        db.execute(delete(ReviewAction))

        # Reset review queue statuses to pending
        db.execute(
            update(ReviewQueueItem).values(status=ReviewStatus.pending)
        )

        # Reset extraction review statuses to pending
        db.execute(
            update(Extraction).where(
                Extraction.review_status != ReviewStatus.pending
            ).values(review_status=ReviewStatus.pending)
        )

        db.commit()

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Reset <strong>{reviewed_count}</strong> review decisions to pending. '
            f'Cleared <strong>{action_count}</strong> review actions. '
            f'All extractions are now awaiting review.'
            f'</div>',
            headers={"HX-Trigger": "pipelineReset"},
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Reset error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/sync/reset")
def reset_sync(db: Session = Depends(get_db)) -> HTMLResponse:
    """Clear synced_at timestamps so all approved extractions can be re-synced."""
    try:
        count = db.scalar(
            text("SELECT count(*) FROM extractions WHERE metadata->>'synced_at' IS NOT NULL")
        ) or 0

        if count == 0:
            return HTMLResponse(
                '<div class="result-panel info">No synced extractions to reset.</div>'
            )

        db.execute(text(
            "UPDATE extractions SET metadata = metadata - 'synced_at' "
            "WHERE metadata ? 'synced_at'"
        ))
        db.commit()

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Cleared sync status on <strong>{count}</strong> extractions. '
            f'They will be included in the next sync run.'
            f'</div>',
            headers={"HX-Trigger": "pipelineReset"},
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Reset error: {html_escape(str(e))}</div>'
        )


@router.post("/api/triage/{triage_id}/override")
def override_triage(
    triage_id: int,
    decision: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Manually override a triage decision."""
    from src.db.models import SectionTriageResult, TriageDecision, TriageMethod

    valid = {"relevant", "not_relevant"}
    if decision not in valid:
        return HTMLResponse(
            '<span class="triage-decision triage-decision-uncertain">invalid</span>'
        )

    triage = db.get(SectionTriageResult, triage_id)
    if not triage:
        return HTMLResponse('<span style="color:var(--danger);">not found</span>')

    old_decision = triage.decision.value if hasattr(triage.decision, "value") else str(triage.decision)
    triage.decision = TriageDecision(decision)
    triage.confidence = 1.0
    triage.llm_reasoning = f"Manual override: {old_decision} → {decision}"

    # Add manual_review enum value to Postgres if needed, then set it
    try:
        triage.method = TriageMethod.manual_review
    except Exception:
        try:
            db.execute(text(
                "ALTER TYPE triagemethod ADD VALUE IF NOT EXISTS 'manual_review'"
            ))
            db.commit()
            triage.method = TriageMethod.manual_review
        except Exception:
            pass  # Leave original method if enum update fails

    db.commit()

    label = "relevant" if decision == "relevant" else "not relevant"
    css_class = f"triage-decision-{decision}"
    return HTMLResponse(
        f'<span class="triage-decision {css_class}">{label}</span>'
    )


@router.post("/api/retry-job/{job_id}")
def retry_job(job_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    """Reset a single failed/manual-review job back to pending for re-fetching."""
    job = db.get(IngestionJob, job_id)
    if not job:
        return HTMLResponse(
            f'<span style="color:var(--danger);font-size:12px;">Job #{job_id} not found.</span>'
        )
    if job.status not in (
        IngestionStatus.failed,
        IngestionStatus.requires_manual_review,
        IngestionStatus.completed,
    ):
        return HTMLResponse(
            f'<span style="color:var(--warning);font-size:12px;">'
            f'Job #{job_id} is {job.status.value} — cannot re-fetch.</span>'
        )
    job.status = IngestionStatus.pending
    job.error_message = None
    job.fetch_started_at = None
    job.fetch_completed_at = None
    job.parse_started_at = None
    job.parse_completed_at = None
    db.commit()
    return HTMLResponse(
        f'<span style="color:var(--success);font-size:12px;">'
        f'Job #{job_id} reset to pending. Run Fetch to re-process.</span>'
    )


@router.get("/api/extraction-monitor")
def get_extraction_monitor() -> HTMLResponse:
    """Return live extraction health dashboard fragment (polled every 2s during runs).

    Shows:
      - Health gauges (failure rate, consecutive errors, confidence distribution)
      - Per-agent performance bars
      - Live event feed with color-coded severity
      - Token burn rate
    """
    from src.core.extraction_monitor import get_monitor

    monitor = get_monitor()
    snap = monitor.snapshot(recent_count=30)
    d = snap.to_dict()

    if not d["is_running"] and d["passages_processed"] == 0:
        return HTMLResponse(
            '<div class="monitor-idle" style="color:var(--text-muted);font-size:13px;">'
            "No extraction running. Start an extraction to see live monitoring.</div>"
        )

    # --- Health gauges ---
    elapsed_min = d["elapsed_seconds"] / 60 if d["elapsed_seconds"] > 0 else 0
    pct_done = (
        round(d["passages_processed"] / d["passages_total"] * 100, 1)
        if d["passages_total"] > 0
        else 0
    )

    # Failure rate color
    fr = d["failure_rate"]
    fr_color = "var(--success)" if fr < 0.05 else "var(--warning)" if fr < 0.2 else "var(--danger)"
    fr_label = f"{fr:.1%}"

    # Consecutive errors color
    ce = d["consecutive_errors"]
    ce_color = "var(--success)" if ce == 0 else "var(--warning)" if ce < 3 else "var(--danger)"

    status_label = "RUNNING" if d["is_running"] else "STOPPED"
    status_color = "var(--success)" if d["is_running"] else "var(--text-muted)"

    gauges_html = f"""
    <div class="monitor-gauges" style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px;">
      <div class="monitor-gauge" style="text-align:center;min-width:80px;">
        <div style="font-size:22px;font-weight:700;color:{status_color};">{status_label}</div>
        <div style="font-size:11px;color:var(--text-muted);">Status</div>
      </div>
      <div class="monitor-gauge" style="text-align:center;min-width:80px;">
        <div style="font-size:22px;font-weight:700;">{d['passages_processed']}/{d['passages_total']}</div>
        <div style="font-size:11px;color:var(--text-muted);">Passages ({pct_done}%)</div>
      </div>
      <div class="monitor-gauge" style="text-align:center;min-width:80px;">
        <div style="font-size:22px;font-weight:700;">{d['extractions_created']}</div>
        <div style="font-size:11px;color:var(--text-muted);">Extractions</div>
      </div>
      <div class="monitor-gauge" style="text-align:center;min-width:80px;">
        <div style="font-size:22px;font-weight:700;color:{fr_color};">{fr_label}</div>
        <div style="font-size:11px;color:var(--text-muted);">Failure Rate</div>
      </div>
      <div class="monitor-gauge" style="text-align:center;min-width:80px;">
        <div style="font-size:22px;font-weight:700;color:{ce_color};">{ce}</div>
        <div style="font-size:11px;color:var(--text-muted);">Consecutive Errors</div>
      </div>
      <div class="monitor-gauge" style="text-align:center;min-width:80px;">
        <div style="font-size:22px;font-weight:700;">{d['total_tokens']:,}</div>
        <div style="font-size:11px;color:var(--text-muted);">Tokens ({d['tokens_per_minute']:,.0f}/min)</div>
      </div>
    </div>
    """

    # --- Confidence distribution bar ---
    tiers = d["confidence_tiers"]
    total_ext = sum(tiers.values()) or 1
    tier_colors = {"A": "var(--success)", "B": "#3b82f6", "C": "var(--warning)", "D": "var(--danger)"}
    tier_segments = ""
    for tier in ["A", "B", "C", "D"]:
        pct = tiers[tier] / total_ext * 100
        if pct > 0:
            tier_segments += (
                f'<div style="width:{pct}%;background:{tier_colors[tier]};height:100%;'
                f'display:inline-block;" title="Tier {tier}: {tiers[tier]}"></div>'
            )
    conf_html = f"""
    <div style="margin-bottom:12px;">
      <div style="font-size:12px;font-weight:600;margin-bottom:4px;">Confidence Distribution</div>
      <div style="height:16px;background:var(--bg-secondary);border-radius:4px;overflow:hidden;display:flex;">
        {tier_segments}
      </div>
      <div style="display:flex;gap:12px;font-size:11px;margin-top:3px;color:var(--text-muted);">
        {''.join(f'<span><span style="color:{tier_colors[t]};">&#9632;</span> {t}: {tiers[t]}</span>' for t in ["A","B","C","D"])}
      </div>
    </div>
    """

    # --- Per-agent stats ---
    agent_html = ""
    if d["agent_stats"]:
        agent_rows = ""
        for name, stats in sorted(d["agent_stats"].items()):
            afr = stats["failure_rate"]
            afr_color = "var(--success)" if afr < 0.05 else "var(--warning)" if afr < 0.2 else "var(--danger)"
            agent_rows += f"""
            <tr>
              <td><code>{html_escape(name)}</code></td>
              <td>{stats['calls']}</td>
              <td style="color:var(--success);">{stats['successes']}</td>
              <td>{stats['abstentions']}</td>
              <td style="color:{'var(--danger)' if stats['errors'] > 0 else 'var(--text-muted)'};">{stats['errors']}</td>
              <td style="color:{afr_color};">{afr:.0%}</td>
              <td>{stats['tokens']:,}</td>
            </tr>
            """
        agent_html = f"""
        <div style="margin-bottom:12px;">
          <div style="font-size:12px;font-weight:600;margin-bottom:4px;">Agent Performance</div>
          <table class="data-table" style="font-size:12px;">
            <thead><tr>
              <th>Agent</th><th>Calls</th><th>OK</th><th>Abstain</th>
              <th>Errors</th><th>Fail%</th><th>Tokens</th>
            </tr></thead>
            <tbody>{agent_rows}</tbody>
          </table>
        </div>
        """

    # --- Issue summary badges ---
    issue_badges = ""
    if d["criticals"] > 0:
        issue_badges += f'<span class="badge badge-danger">{d["criticals"]} Critical</span> '
    if d["errors_count"] > 0:
        issue_badges += f'<span class="badge badge-warning">{d["errors_count"]} Errors</span> '
    if d["warnings"] > 0:
        issue_badges += f'<span class="badge badge-info">{d["warnings"]} Warnings</span> '
    if not issue_badges:
        issue_badges = '<span style="color:var(--success);font-size:12px;">No issues detected</span>'

    issue_html = f"""
    <div style="margin-bottom:8px;">
      <div style="font-size:12px;font-weight:600;margin-bottom:4px;">Issues</div>
      {issue_badges}
    </div>
    """

    # --- Live event feed ---
    event_colors = {
        "critical": "var(--danger)",
        "error": "#dc3545",
        "warning": "#e67e22",
        "success": "var(--success)",
        "info": "var(--text-muted)",
    }
    event_icons = {
        "critical": "&#9888;",
        "error": "&#10007;",
        "warning": "&#9888;",
        "success": "&#10003;",
        "info": "&#8226;",
    }

    feed_items = ""
    for evt in d["recent_events"][:20]:
        color = event_colors.get(evt["severity"], "var(--text-muted)")
        icon = event_icons.get(evt["severity"], "&#8226;")
        age = evt["age_seconds"]
        age_label = f"{age:.0f}s ago" if age < 60 else f"{age / 60:.0f}m ago"
        feed_items += (
            f'<div style="padding:3px 0;font-size:12px;border-bottom:1px solid var(--border);'
            f'display:flex;gap:6px;align-items:baseline;">'
            f'<span style="color:{color};flex-shrink:0;width:14px;">{icon}</span>'
            f'<span style="flex:1;">{html_escape(evt["message"])}</span>'
            f'<span style="color:var(--text-muted);font-size:10px;flex-shrink:0;">{age_label}</span>'
            f'</div>'
        )

    feed_html = f"""
    <div>
      <div style="font-size:12px;font-weight:600;margin-bottom:4px;">Live Feed</div>
      <div style="max-height:300px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;padding:4px 8px;">
        {feed_items if feed_items else '<div style="color:var(--text-muted);font-size:12px;padding:8px;">Waiting for events...</div>'}
      </div>
    </div>
    """

    # --- Current document ---
    doc_html = ""
    if d["current_document"]:
        doc_html = (
            f'<div style="font-size:12px;margin-bottom:8px;color:var(--text-muted);">'
            f'Processing: <strong>{html_escape(d["current_document"])}</strong></div>'
        )

    return HTMLResponse(
        f"{gauges_html}{doc_html}{conf_html}{agent_html}{issue_html}{feed_html}"
    )


@router.post("/api/run/extract/cancel")
def cancel_extract() -> HTMLResponse:
    """Signal the running extraction pipeline to stop after the current passage."""
    from src.ingestion.extractor import is_cancelled, request_cancel

    if is_cancelled():
        return HTMLResponse(
            '<div class="result-panel info">Cancellation already requested.</div>'
        )
    request_cancel()
    return HTMLResponse(
        '<div class="result-panel warning">Termination requested — extraction will stop after the current passage completes.</div>'
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
        panel_class = "success"
        cancelled_note = ""
        if summary.get("cancelled"):
            cancelled_note = (
                '<div style="margin-top:6px;font-size:13px;color:var(--warning);">'
                'Extraction was terminated by user. Remaining passages still unprocessed.'
                '</div>'
            )
            panel_class = "warning"
        return HTMLResponse(
            f'<div class="result-panel {panel_class}">'
            f'Extracted {summary["total_extractions"]} items from '
            f'{summary["records_processed"]} passages {label}. '
            f'Tokens: {tokens.get("total_tokens", 0):,}'
            f'{cancelled_note}'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/dependency-graph")
def run_dependency_graph(
    document_version_id: int | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Build dependency graphs linking extractions within documents.

    Uses GPT (gpt-oss-20b, 131k context) to identify relationships between
    obligations, definitions, thresholds, exceptions, enforcement mechanisms,
    rights, and compliance mechanisms.

    Args:
        document_version_id: Process a single document (None = all pending).
    """
    try:
        from src.ingestion.extractor import run_dependency_graph as _run_dep_graph
        summary = _run_dep_graph(db, document_version_id=document_version_id)

        docs = summary["documents_processed"]
        edges = summary["total_edges"]
        cancelled_note = ""
        panel_class = "success"

        if summary.get("cancelled"):
            cancelled_note = (
                '<div style="margin-top:6px;font-size:13px;color:var(--warning);">'
                'Dependency graph building was terminated by user.'
                '</div>'
            )
            panel_class = "warning"

        if docs == 0:
            return HTMLResponse(
                '<div class="result-panel info">'
                'No documents pending dependency graph construction. '
                'All documents with extractions already have dependency edges.'
                '</div>'
            )

        return HTMLResponse(
            f'<div class="result-panel {panel_class}">'
            f'Built dependency graphs for {docs} document(s): '
            f'{edges} relationship edges created.'
            f'{cancelled_note}'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/condition-parse")
def run_condition_parse(
    document_version_id: int | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Parse condition fields into structured boolean expression trees.

    Rule-based parser — no LLM call needed. Converts free-text conditions
    from obligation, threshold, exception, and rights extractions into
    AND/OR/NOT/LEAF expression trees in the applicability_conditions table.

    Args:
        document_version_id: Process a single document (None = all pending).
    """
    try:
        from src.ingestion.extractor import run_condition_parsing
        summary = run_condition_parsing(db, document_version_id=document_version_id)

        processed = summary["extractions_processed"]
        nodes = summary["nodes_created"]
        with_conds = summary["extractions_with_conditions"]
        errors = summary.get("errors", 0)

        if processed == 0:
            return HTMLResponse(
                '<div class="result-panel info">'
                'No extractions pending condition parsing. '
                'All condition fields have already been parsed.'
                '</div>'
            )

        error_note = ""
        if errors:
            error_note = f' ({errors} errors)'

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Parsed conditions from {with_conds}/{processed} extractions: '
            f'{nodes} tree nodes created.{error_note}'
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

    # Bill ID, IAPP bill number, and IAPP status (stored in family metadata)
    meta = dict(family.metadata_ or {})
    meta_changed = False
    new_bill_id = form.get("bill_id", "").strip()
    if new_bill_id and new_bill_id != meta.get("bill_id", ""):
        meta["bill_id"] = new_bill_id
        meta_changed = True
        changes.append(f"bill_id → {new_bill_id}")
    new_iapp_bill = form.get("iapp_bill_number", "").strip()
    if new_iapp_bill and new_iapp_bill != meta.get("iapp_bill_number", ""):
        meta["iapp_bill_number"] = new_iapp_bill
        meta_changed = True
        changes.append(f"IAPP bill number → {new_iapp_bill}")
    new_iapp_status = form.get("iapp_status", "").strip()
    if new_iapp_status and new_iapp_status != meta.get("iapp_status", ""):
        meta["iapp_status"] = new_iapp_status
        meta_changed = True
        changes.append(f"IAPP status → {new_iapp_status}")
    if meta_changed:
        family.metadata_ = meta

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
        orrick_records = _tracker_csv_to_records()

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
                from src.ingestion.legacy.iapp_scraper import scrape_tracker
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
    """List all failed, manual-review, and missing-URL ingestion jobs with upload + edit forms."""
    from sqlalchemy import or_, and_

    jobs = db.scalars(
        select(IngestionJob)
        .where(
            or_(
                IngestionJob.status.in_([
                    IngestionStatus.failed,
                    IngestionStatus.requires_manual_review,
                ]),
                # Pending jobs with no URL — fetcher will skip these
                and_(
                    IngestionJob.status == IngestionStatus.pending,
                    or_(
                        IngestionJob.fetch_url.is_(None),
                        IngestionJob.fetch_url == "",
                    ),
                ),
            )
        )
        .order_by(IngestionJob.updated_at.desc())
    ).all()

    if not jobs:
        return HTMLResponse(
            '<div class="result-panel success" style="margin-top:10px;">'
            'No failed or missing documents. All jobs completed successfully.'
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
        bill_id = ""
        iapp_bill_number = ""
        iapp_status = ""
        leg_status = ""
        if dv and dv.family:
            source = dv.family.source
            jurisdiction = source.jurisdiction_code if source else ""
            short_cite = dv.family.short_cite or ""
            title = dv.family.canonical_title or ""
            subject = dv.family.subject_area or ""
            label = short_cite or title or "unknown"
            meta = dv.family.metadata_ or {}
            bill_id = meta.get("bill_id", "")
            iapp_bill_number = meta.get("iapp_bill_number", "")
            iapp_status = meta.get("iapp_status", "")
        if dv and dv.temporal_status:
            leg_status = dv.temporal_status.value if hasattr(dv.temporal_status, "value") else str(dv.temporal_status)

        # Ingestion status badge (small, secondary — not the main Status column)
        is_missing_url = (
            job.status == IngestionStatus.pending
            and not job.fetch_url
        )
        if job.status == IngestionStatus.failed:
            ing_class = "danger"
            ing_label = "Failed"
        elif is_missing_url:
            ing_class = "warning"
            ing_label = "No URL"
        else:
            ing_class = "warning"
            ing_label = "Needs Review"

        # Bill Status: prefer raw IAPP status, fall back to normalized TemporalStatus
        bill_status_display = iapp_status or leg_status or "—"

        # Bill ID: show Orrick bill_id, IAPP bill_number, or both if they differ
        bill_id_display = bill_id or iapp_bill_number or "—"
        bill_id_extra = ""
        if bill_id and iapp_bill_number and bill_id != iapp_bill_number:
            bill_id_extra = (
                f'<br><span style="font-size:10px;color:var(--text-muted);">'
                f'IAPP: {html_escape(iapp_bill_number)}</span>'
            )

        error_short = html_escape(str(job.error_message or "")[:150])
        url_display = html_escape(str(job.fetch_url or ""))
        jid = job.id

        rows_html += f"""
        <tr id="failed-row-{jid}">
          <td><strong>{html_escape(jurisdiction)}</strong></td>
          <td>{html_escape(label)}
              <div style="font-size:10px;color:var(--text-muted);max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                   title="{html_escape(title)}">{html_escape(title)}</div></td>
          <td>{html_escape(bill_id_display)}{bill_id_extra}</td>
          <td>{html_escape(bill_status_display)}</td>
          <td style="font-size:11px;max-width:250px;">
            <span style="color:var(--{ing_class});font-weight:600;font-size:11px;">{ing_label}</span>
            <br><code style="word-break:break-all;">{url_display[:80]}</code>
            <br><span style="color:var(--{ing_class});font-size:11px;">{error_short}</span>
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
            {"" if is_missing_url else f'''<button class="btn btn-sm"
                    hx-post="/dashboard/api/retry-job/{jid}"
                    hx-target="#failed-result-{jid}"
                    hx-swap="innerHTML"
                    hx-disabled-elt="this"
                    style="margin-left:4px;">
              <span class="btn-label">Re-fetch</span>
              <span class="htmx-indicator"><span class="spinner"></span></span>
            </button>'''}
          </td>
        </tr>
        <tr id="failed-edit-{jid}" style="display:none;background:var(--bg-secondary);">
          <td colspan="6" style="padding:8px;">
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
                       style="width:150px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Bill Title
                <input type="text" name="title" value="{html_escape(title)}"
                       style="width:180px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Bill ID (Orrick)
                <input type="text" name="bill_id" value="{html_escape(bill_id)}"
                       style="width:100px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Bill # (IAPP)
                <input type="text" name="iapp_bill_number" value="{html_escape(iapp_bill_number)}"
                       style="width:100px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                IAPP Status
                <input type="text" name="iapp_status" value="{html_escape(iapp_status)}"
                       style="width:140px;font-size:12px;padding:2px 4px;">
              </label>
              <label style="display:flex;flex-direction:column;gap:2px;">
                Fetch URL
                <input type="text" name="fetch_url" value="{url_display}"
                       style="width:220px;font-size:12px;padding:2px 4px;">
              </label>
              <button type="submit" class="btn btn-sm btn-primary" hx-disabled-elt="this">
                <span class="btn-label">Save</span>
                <span class="htmx-indicator"><span class="spinner"></span></span>
              </button>
            </form>
          </td>
        </tr>
        <tr id="failed-result-row-{jid}" style="display:none;">
          <td colspan="6"><div id="failed-result-{jid}"></div></td>
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
        f'<th>Jur.</th><th>Document</th><th>Bill ID</th><th>Bill Status</th>'
        f'<th>Error / URL</th><th>Actions</th>'
        f'</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table></div>'
        f'<div style="font-size:12px;color:var(--text-muted);margin-top:6px;">'
        f'Upload the PDF/HTML manually, click Edit to add/fix the URL and re-fetch, '
        f'or update metadata for laws with no source URL.'
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
    """Export all failed/missing-URL documents as a plain .txt file for searching/printing."""
    from fastapi.responses import PlainTextResponse
    from sqlalchemy import or_, and_

    jobs = db.scalars(
        select(IngestionJob)
        .where(
            or_(
                IngestionJob.status.in_([
                    IngestionStatus.failed,
                    IngestionStatus.requires_manual_review,
                ]),
                and_(
                    IngestionJob.status == IngestionStatus.pending,
                    or_(
                        IngestionJob.fetch_url.is_(None),
                        IngestionJob.fetch_url == "",
                    ),
                ),
            )
        )
        .order_by(IngestionJob.updated_at.desc())
    ).all()

    lines = [
        "FAILED / MISSING DOCUMENT DOWNLOADS",
        "=" * 60,
        f"Generated: {__import__('datetime').datetime.utcnow().isoformat()}Z",
        f"Total: {len(jobs)} documents",
        "",
    ]

    for i, job in enumerate(jobs, 1):
        dv = job.document_version
        jurisdiction = ""
        label = "unknown"
        bill_title = ""
        bill_id = ""
        iapp_bill_number = ""
        iapp_status = ""
        leg_status = ""
        if dv and dv.family:
            source = dv.family.source
            jurisdiction = source.jurisdiction_code if source else ""
            label = dv.family.short_cite or dv.family.canonical_title or "unknown"
            bill_title = dv.family.canonical_title or ""
            meta = dv.family.metadata_ or {}
            bill_id = meta.get("bill_id", "")
            iapp_bill_number = meta.get("iapp_bill_number", "")
            iapp_status = meta.get("iapp_status", "")
        if dv and dv.temporal_status:
            leg_status = dv.temporal_status.value if hasattr(dv.temporal_status, "value") else str(dv.temporal_status)

        if job.status == IngestionStatus.failed:
            status_label = "FAILED"
        elif job.status == IngestionStatus.pending and not job.fetch_url:
            status_label = "NO URL"
        else:
            status_label = "NEEDS REVIEW"

        lines.append(f"{i}. [{jurisdiction}] {label}")
        if bill_title and bill_title != label:
            lines.append(f"   Title: {bill_title}")
        if bill_id:
            lines.append(f"   Bill ID: {bill_id}")
        if iapp_bill_number and iapp_bill_number != bill_id:
            lines.append(f"   IAPP Bill #: {iapp_bill_number}")
        bill_status = iapp_status or leg_status
        if bill_status:
            lines.append(f"   Bill Status: {bill_status}")
        lines.append(f"   Ingestion: {status_label}")
        lines.append(f"   URL: {job.fetch_url or 'N/A'}")
        error_msg = (job.error_message or "").split("\n")[0][:200]
        lines.append(f"   Error: {error_msg}")
        if hasattr(job, "ai_suggested_url") and job.ai_suggested_url:
            lines.append(f"   Suggested URL: {job.ai_suggested_url}")
        lines.append("")

    lines.append("=" * 60)
    lines.append("Search tips:")
    lines.append("  - For NO URL: search the state legislature site for the bill and add the URL or upload the PDF")
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


# ---------------------------------------------------------------------------
# Completeness Manifest — extraction coverage reporting (kept here for now)
# _get_pipeline_stats, _get_export_files → _dashboard_helpers.py
# review_page, approve_item, reject_item → review_routes.py
# tracker_* → tracker_routes.py
# ---------------------------------------------------------------------------


@router.get("/api/completeness")
def get_completeness_manifest(
    document_version_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Return extraction completeness manifest as an HTML table.

    Shows per-document extraction coverage: total passages, processed,
    skipped, coverage %, and flags gaps where passages have no extractions.
    """
    from src.ingestion.extractor import compute_completeness_manifest

    reports = compute_completeness_manifest(db, document_version_id)

    if not reports:
        return HTMLResponse(
            '<div class="result-panel info">No documents with passages found. '
            "Run fetch & extraction first.</div>"
        )

    # Summary stats
    total_docs = len(reports)
    complete_docs = sum(1 for r in reports if r.is_complete)
    total_passages = sum(r.total_passages for r in reports)
    total_processed = sum(r.passages_processed for r in reports)
    total_gaps = sum(len(r.gaps) for r in reports)
    overall_coverage = round(total_processed / total_passages * 100, 1) if total_passages else 0

    summary_html = f"""
    <div class="result-panel {'success' if total_gaps == 0 else 'warning'}"
         style="margin-bottom:12px;">
      <strong>Completeness Summary:</strong>
      {complete_docs}/{total_docs} documents fully covered &middot;
      {total_processed}/{total_passages} passages processed &middot;
      {total_gaps} gap{"s" if total_gaps != 1 else ""} found &middot;
      {overall_coverage}% overall coverage
    </div>
    """

    # Per-document table
    rows_html = ""
    for report in sorted(reports, key=lambda r: r.coverage_percent):
        coverage_class = (
            "success" if report.coverage_percent >= 95
            else "warning" if report.coverage_percent >= 80
            else "danger"
        )
        gap_count = len(report.gaps)
        status_icon = "&#10003;" if report.is_complete else f"&#9888; {gap_count} gaps"

        rows_html += f"""
        <tr>
          <td><strong>{html_escape(report.document_label)}</strong></td>
          <td>{html_escape(report.jurisdiction or '—')}</td>
          <td>{report.total_passages}</td>
          <td>{report.passages_processed}</td>
          <td>{report.passages_skipped_short + report.passages_skipped_boilerplate}</td>
          <td class="text-{coverage_class}">
            <strong>{report.coverage_percent}%</strong>
          </td>
          <td class="text-{coverage_class}">{status_icon}</td>
        </tr>
        """

        # Show gaps if any
        if report.gaps:
            for gap in report.gaps[:5]:  # Show max 5 gaps per doc
                preview = html_escape(gap.get("text_preview", "")[:100])
                rows_html += f"""
                <tr style="background: var(--bg-warning-subtle, #fff3cd);">
                  <td colspan="2" style="padding-left:2em; font-size:0.85em;">
                    &#8627; {html_escape(gap.get('section_path') or 'Unknown section')}
                  </td>
                  <td colspan="3" style="font-size:0.85em;">{preview}...</td>
                  <td colspan="2" style="font-size:0.85em;">
                    Expected: {', '.join(gap.get('expected_agents', []))}
                  </td>
                </tr>
                """
            if len(report.gaps) > 5:
                rows_html += f"""
                <tr style="background: var(--bg-warning-subtle, #fff3cd);">
                  <td colspan="7" style="padding-left:2em; font-size:0.85em; font-style:italic;">
                    ... and {len(report.gaps) - 5} more gaps
                  </td>
                </tr>
                """

    table_html = f"""
    {summary_html}
    <table class="data-table">
      <thead>
        <tr>
          <th>Document</th>
          <th>Jurisdiction</th>
          <th>Total</th>
          <th>Processed</th>
          <th>Skipped</th>
          <th>Coverage</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    """

    return HTMLResponse(table_html)


# ---------------------------------------------------------------------------
# Verification Pipeline — post-extraction accuracy checks
# ---------------------------------------------------------------------------


@router.post("/api/verify", response_class=HTMLResponse)
def run_verification(
    document_version_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Run post-extraction verification agents on completed extractions.

    Three layers: cross-validation, gap detection, and citation verification.
    Returns results as HTML for the dashboard.
    """
    from dataclasses import asdict

    from src.ingestion.extractor import run_verification_pass

    if not _acquire_pipeline_lock():
        return HTMLResponse(
            '<div class="result-panel warning">Another pipeline operation is running.</div>'
        )

    try:
        results = run_verification_pass(db, document_version_id)
    finally:
        _pipeline_lock.release()

    if not results:
        return HTMLResponse(
            '<div class="result-panel info">No documents with extractions to verify.</div>'
        )

    # Summary
    total_cv_flagged = sum(r.cross_validation_flagged for r in results)
    total_gaps = sum(r.gaps_found for r in results)
    total_cit_issues = sum(r.citations_unverified for r in results)
    total_tokens = sum(r.total_tokens for r in results)

    severity = "success" if (total_cv_flagged + total_gaps + total_cit_issues) == 0 else "warning"
    summary_html = f"""
    <div class="result-panel {severity}" style="margin-bottom:12px;">
      <strong>Verification Complete</strong> ({len(results)} documents, {total_tokens:,} tokens)<br>
      Cross-validation: {total_cv_flagged} flagged &middot;
      Gap detection: {total_gaps} gaps &middot;
      Citations: {total_cit_issues} unverified
    </div>
    """

    rows_html = ""
    for r in results:
        doc_severity = (
            "success" if (r.cross_validation_flagged + r.gaps_found + r.citations_unverified) == 0
            else "warning" if r.cross_validation_avg_accuracy >= 0.8
            else "danger"
        )
        rows_html += f"""
        <tr>
          <td><strong>{html_escape(r.document_label)}</strong></td>
          <td class="text-{doc_severity}">{r.cross_validation_avg_accuracy:.1%}</td>
          <td>{r.cross_validation_flagged}</td>
          <td>{r.gaps_found} ({r.high_confidence_gaps} high)</td>
          <td>{r.citations_verified}/{r.citations_checked}</td>
          <td>{r.total_tokens:,}</td>
        </tr>
        """

    table_html = f"""
    {summary_html}
    <table class="data-table">
      <thead>
        <tr>
          <th>Document</th>
          <th>CV Accuracy</th>
          <th>CV Flagged</th>
          <th>Gaps Found</th>
          <th>Citations OK</th>
          <th>Tokens</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    """

    return HTMLResponse(table_html)
