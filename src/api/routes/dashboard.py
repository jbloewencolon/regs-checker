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

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.api.routes._dashboard_helpers import (
    _acquire_pipeline_lock,
    _get_export_files,
    _get_pipeline_stats,
    _pipeline_lock,
    _render,
)
from src.api.routes.review_routes import router as review_router
from src.api.routes.tracker_routes import (
    _tracker_csv_to_records,
)
from src.api.routes.tracker_routes import (
    router as tracker_router,
)
from src.db.engine import get_db
from src.db.models import (
    ApplicabilityCondition,
    ComplianceConcept,
    ConceptReviewStatus,
    DocumentFamily,
    DocumentVersion,
    Extraction,
    ExtractionJob,
    FailedExtractionAttempt,
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
    ObligationDependency,
    PipelineEvent,
    RawArtifact,
    ReviewAction,
    ReviewQueueItem,
    ReviewStatus,
    Source,
)

router = APIRouter()
router.include_router(review_router)
router.include_router(tracker_router)


# ---------------------------------------------------------------------------
# Background job runner — lets long-running pipeline steps return immediately
# so HTMX polling can keep the UI alive.
# ---------------------------------------------------------------------------

_background_jobs: dict[str, dict] = {}  # keyed by step name


def _run_in_background(step: str, target, kwargs: dict | None = None):
    """Launch *target* in a daemon thread with its own DB session.

    The function stored in *target* must accept a ``db`` keyword argument.
    We create a fresh ``SessionLocal()`` so the request-scoped session can
    close immediately.
    """
    from src.db.engine import SessionLocal

    _background_jobs[step] = {"running": True, "result_html": None, "error": None}

    def _wrapper():
        import logging as _logging
        _bg_log = _logging.getLogger(f"background.{step}")
        _bg_log.info("Background job '%s' starting", step)
        db = SessionLocal()
        try:
            result_html = target(db=db, **(kwargs or {}))
            _background_jobs[step]["result_html"] = result_html
            _bg_log.info("Background job '%s' completed successfully", step)
        except Exception as e:
            _bg_log.error("Background job '%s' failed: %s", step, e, exc_info=True)
            db.rollback()
            _background_jobs[step]["error"] = str(e)
        finally:
            db.close()
            _background_jobs[step]["running"] = False

    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()


@router.get("/api/job-status/{step}")
def get_job_status(step: str) -> HTMLResponse:
    """Poll background job status. Returns result HTML when done."""
    job = _background_jobs.get(step)
    if job is None:
        return HTMLResponse('<div class="result-panel info">No job running.</div>')
    if job["running"]:
        return HTMLResponse(
            f'<div class="result-panel info" hx-get="/dashboard/api/job-status/{step}" '
            f'hx-trigger="every 2s" hx-swap="outerHTML">'
            f'<span class="spinner"></span> {step.replace("_", " ").title()} running&hellip; '
            f'Check the pipeline tracker above for live progress.</div>'
        )
    # Done — return final result
    if job["error"]:
        html = f'<div class="result-panel error">Error: {html_escape(job["error"])}</div>'
    else:
        html = job["result_html"] or '<div class="result-panel success">Done.</div>'
    del _background_jobs[step]
    return HTMLResponse(html)


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

    from src.core.model_config import get_config as _get_model_config
    active_provider = _get_model_config().provider

    return _render(request, "dashboard.html", {
        "stats": stats,
        "export_files": export_files,
        "progress": progress.to_dict(),
        "config": settings,
        "active_provider": active_provider,
        "nvidia_model": settings.nvidia_extraction_model,
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
    from src.db.models import SectionTriageResult

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
            "ai_signals": triage.ai_signals,
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
    if progress.estimated_remaining_seconds is not None:
        if progress.estimated_remaining_seconds == 0:
            eta_text = "Complete"
        else:
            hrs = progress.estimated_remaining_seconds // 3600
            mins = (progress.estimated_remaining_seconds % 3600) // 60
            eta_text = f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m remaining"
    elif progress.overall_percent >= 100:
        eta_text = "Complete"
    elif progress.completed_items == 0:
        eta_text = "Not started"
    else:
        eta_text = "Calculating..."

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
    except Exception:
        db.rollback()

    # --- Extraction stats ---
    # Count passages that have at least one extraction (= fully extracted)
    extracted_passages = db.scalar(
        select(func.count(func.distinct(Extraction.source_record_id)))
        .select_from(Extraction)
    ) or 0
    # The denominator is triaged-relevant + uncertain passages
    extractable_passages = triage_relevant + triage_uncertain

    extraction_in_progress = db.scalar(
        select(func.count()).where(ExtractionJob.status == "running")
    ) or 0
    extraction_failed = db.scalar(
        select(func.count()).where(ExtractionJob.status == "failed")
    ) or 0

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

    total_passages = db.scalar(
        select(func.count()).select_from(NormalizedSourceRecord)
    ) or 0

    fetch_pct = pct(fetched, total_jobs)
    parse_pct = pct(parsed, total_jobs)
    triage_pct = pct(triage_total, total_passages) if total_passages else 0
    extract_pct = pct(extracted_passages, extractable_passages) if extractable_passages else 0

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
        <div class="tracker-header">
          <span class="tracker-step-num">1</span> Fetched
        </div>
        <div class="tracker-value">{fetched}<span class="tracker-total">/{total_jobs}</span></div>
        <div class="tracker-bar"><div class="tracker-bar-fill" style="width:{fetch_pct}%"></div></div>
        <div class="tracker-footer">
          <span class="tracker-pct">{fetch_pct}%</span>
          {status_badge(fetch_in_progress, fetch_failed)}
        </div>
      </div>

      <div class="tracker-card">
        <div class="tracker-header">
          <span class="tracker-step-num">2</span> Parsed
        </div>
        <div class="tracker-value">{parsed}<span class="tracker-total">/{total_jobs}</span></div>
        <div class="tracker-bar"><div class="tracker-bar-fill parsed" style="width:{parse_pct}%"></div></div>
        <div class="tracker-footer">
          <span class="tracker-pct">{parse_pct}%</span>
          {status_badge(parse_in_progress, parse_failed)}
        </div>
      </div>

      <div class="tracker-card">
        <div class="tracker-header">
          <span class="tracker-step-num">3</span> Triaged
        </div>
        <div class="tracker-value">{triage_total}<span class="tracker-total">/{total_passages} passages</span></div>
        <div class="tracker-bar"><div class="tracker-bar-fill triaged" style="width:{triage_pct}%"></div></div>
        <div class="tracker-footer">
          <span class="tracker-detail">{triage_relevant} relevant</span>
          <span class="tracker-detail uncertain">{triage_uncertain} uncertain</span>
          <span class="tracker-detail skipped">{triage_skipped} skipped</span>
        </div>
      </div>

      <div class="tracker-card">
        <div class="tracker-header">
          <span class="tracker-step-num">4</span> Extracted
        </div>
        <div class="tracker-value">{extracted_passages}<span class="tracker-total">/{extractable_passages} passages</span></div>
        <div class="tracker-bar"><div class="tracker-bar-fill extracted" style="width:{extract_pct}%"></div></div>
        <div class="tracker-footer">
          <span class="tracker-pct">{extract_pct}%</span>
          {status_badge(extraction_in_progress, extraction_failed)}
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


@router.post("/api/run/seed-local")
def run_seed_local(
    seed_only: bool = False,
    limit: int | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Seed laws from data/fact_laws.csv and ingest local source files."""
    if not _acquire_pipeline_lock():
        return HTMLResponse(
            '<div class="result-panel info">A pipeline operation is already running. Please wait.</div>'
        )
    try:
        from src.ingestion.local_ingest import run_local_ingest

        summary = run_local_ingest(
            db,
            limit=limit,
            seed_only=seed_only,
            on_progress=None,
        )

        parts = []
        created = summary.get("created", 0)
        skipped = summary.get("skipped", 0)
        repaired = summary.get("repaired", 0)
        parts.append(f"Seeded <strong>{created}</strong> new law families ({skipped} already existed).")
        if repaired:
            parts.append(
                f'<span style="color:var(--info);">Repaired <strong>{repaired}</strong> '
                f'families that were missing an ingestion job.</span>'
            )

        if not seed_only:
            completed = summary.get("completed", 0)
            failed = summary.get("failed", 0)
            passages = summary.get("total_passages", 0)
            no_file = summary.get("skipped_no_file", 0)
            parts.append(
                f"Ingested <strong>{completed}</strong> documents "
                f"(<strong>{passages}</strong> passages)."
            )
            if failed > 0:
                parts.append(
                    f'<span style="color:var(--warning);">{failed} failed'
                    f'{f" ({no_file} missing source files)" if no_file else ""}.</span>'
                )

        panel_class = "success" if summary.get("failed", 0) == 0 else "warning"
        return HTMLResponse(
            f'<div class="result-panel {panel_class}">'
            f'{" ".join(parts)}'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
        )
    finally:
        _pipeline_lock.release()


@router.post("/api/run/full-reset-seed-ingest")
def run_full_reset_seed_ingest(db: Session = Depends(get_db)) -> HTMLResponse:
    """Full pipeline reset + re-seed + ingest in one background operation.

    1. Clears all pipeline tables (preserves sources)
    2. Re-seeds document families from data/fact_laws.csv
    3. Ingests all local files from output/law_texts/
    """
    if _background_jobs.get("full_reset", {}).get("running"):
        return HTMLResponse(
            '<div class="result-panel info" hx-get="/dashboard/api/job-status/full_reset" '
            'hx-trigger="every 2s" hx-swap="outerHTML">'
            '<span class="spinner"></span> Reset already running&hellip;</div>'
        )

    def _do_full_reset(db):
        from sqlalchemy import text as _text

        # Count rows we're about to clear (for the result message) before the
        # TRUNCATE, since TRUNCATE does not report affected-row counts.
        deleted_total = 0
        for tbl in ("document_families", "normalized_source_records", "extractions"):
            try:
                deleted_total += db.scalar(_text(f"SELECT count(*) FROM {tbl}")) or 0  # noqa: S608
            except Exception:
                db.rollback()

        # Step 1: Clear ALL document-derived pipeline data in one atomic step.
        #
        # TRUNCATE ... CASCADE follows every foreign key automatically, so child
        # tables (compliance_concepts, bill_level_extractions, verification_*,
        # vocab_review_queue, concept_extraction_links, extraction_attempts, …)
        # are cleared without maintaining a hand-ordered DELETE list. The old
        # per-table loop silently skipped tables it didn't know about; their FK
        # rows then blocked DELETE FROM document_versions/document_families,
        # leaving stale families that made re-seeding a no-op.
        #
        # export_jobs has no FK into the document graph, so name it explicitly.
        # RESTART IDENTITY resets the sequences of every truncated table.
        # sources / api_keys / sync_cursors / content_blobs / extraction_runs are
        # parents (or unrelated) and are intentionally preserved.
        db.execute(_text(
            "TRUNCATE TABLE document_families, export_jobs RESTART IDENTITY CASCADE"
        ))
        db.commit()

        # Step 2: Re-seed from fact_laws.csv
        from src.ingestion.local_ingest import run_local_ingest
        summary = run_local_ingest(db)

        created = summary.get("created", 0)
        completed = summary.get("completed", 0)
        failed = summary.get("failed", 0)
        passages = summary.get("total_passages", 0)
        no_file = summary.get("skipped_no_file", 0)

        panel_class = "success" if failed == 0 else "warning"
        fail_note = (
            f'<div style="margin-top:6px;font-size:13px;color:var(--warning);">'
            f'{failed} failed ({no_file} missing source files).</div>'
        ) if failed else ""
        return (
            f'<div class="result-panel {panel_class}">'
            f'Reset <strong>{deleted_total:,}</strong> rows. '
            f'Seeded <strong>{created}</strong> laws. '
            f'Ingested <strong>{completed}</strong> documents '
            f'(<strong>{passages:,}</strong> passages).{fail_note}'
            f'</div>'
        )

    _run_in_background("full_reset", _do_full_reset)
    return HTMLResponse(
        '<div class="result-panel info" hx-get="/dashboard/api/job-status/full_reset" '
        'hx-trigger="every 2s" hx-swap="outerHTML">'
        '<span class="spinner"></span> Resetting DB, re-seeding, and ingesting&hellip; '
        'Watch the pipeline tracker for progress.</div>'
    )


@router.post("/api/run/pdf-discovery")
def run_csv_discovery(db: Session = Depends(get_db)) -> HTMLResponse:
    """[LEGACY] Seed legislation from the fact_laws.csv (primary discovery source)."""
    if not _acquire_pipeline_lock():
        return HTMLResponse(
            '<div class="result-panel info">A pipeline operation is already running. Please wait.</div>'
        )
    try:
        from src.ingestion._archived.pdf_tracker import seed_from_tracker

        records = _tracker_csv_to_records()
        if not records:
            return HTMLResponse(
                '<div class="result-panel warning">'
                'No records in <code>data/fact_laws.csv</code>. '
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
    """Fetch and parse pending documents (runs in background thread)."""
    if _background_jobs.get("fetch", {}).get("running"):
        return HTMLResponse(
            '<div class="result-panel info" hx-get="/dashboard/api/job-status/fetch" '
            'hx-trigger="every 2s" hx-swap="outerHTML">'
            '<span class="spinner"></span> Fetch already running&hellip;</div>'
        )

    def _do_fetch(db):
        from src.ingestion.pipeline import run_pending_ingestion
        summary = run_pending_ingestion(db, limit=limit)

        if summary["total_pending"] == 0:
            # Check if documents already exist so the message is actionable.
            from src.db.models import NormalizedSourceRecord as _NSR
            passages = db.scalar(select(func.count()).select_from(_NSR)) or 0
            if passages > 0:
                return (
                    '<div class="result-panel info">'
                    f'No pending jobs — all documents already parsed '
                    f'(<strong>{passages:,}</strong> passages in DB). '
                    'Proceed to <strong>Triage Passages</strong>.'
                    '</div>'
                )
            return (
                '<div class="result-panel warning">'
                '<strong>No pending ingestion jobs found.</strong> '
                'Use <strong>Seed &amp; Ingest All</strong> (Step&nbsp;1) to read '
                'documents from <code>output/law_texts/</code> into the database first.'
                '</div>'
            )

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
                ' — switch to the <strong>Failed Documents</strong> tab to upload or edit.'
                '</div>'
            )

        return (
            f'<div class="result-panel {panel_class}">'
            f'<strong>{summary["completed"]}/{total}</strong> documents parsed into '
            f'<strong>{summary["total_passages"]}</strong> passages.'
            f'{failed_note}'
            f'{cancelled_note}'
            f'</div>'
        )

    _run_in_background("fetch", _do_fetch)
    return HTMLResponse(
        '<div class="result-panel info" hx-get="/dashboard/api/job-status/fetch" '
        'hx-trigger="every 2s" hx-swap="outerHTML">'
        '<span class="spinner"></span> Parsing documents&hellip; '
        'Watch the pipeline tracker above for live progress.</div>'
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


@router.post("/api/run/fetch/reset-all")
def reset_fetch_all(db: Session = Depends(get_db)) -> HTMLResponse:
    """Hard reset ALL ingestion jobs (including completed) for re-parsing.

    Jobs that have a raw artifact are set to 'fetched' (re-parse only).
    Jobs without an artifact are set to 'pending' (full re-fetch).
    Also deletes downstream NormalizedSourceRecords so passages are rebuilt.
    """
    from sqlalchemy import delete

    from src.db.models import SectionTriageResult

    try:
        jobs = db.scalars(
            select(IngestionJob).where(
                IngestionJob.status != IngestionStatus.pending,
            )
        ).all()

        if not jobs:
            return HTMLResponse(
                '<div class="result-panel info">No jobs to reset — all are already pending.</div>'
            )

        # Delete downstream passages so parser regenerates them
        completed_version_ids = [
            j.document_version_id for j in jobs
            if j.status == IngestionStatus.completed
        ]
        passages_deleted = 0
        extractions_deleted = 0
        if completed_version_ids:
            # Get passage IDs first — needed for cascading deletes
            passage_ids = db.scalars(
                select(NormalizedSourceRecord.id).where(
                    NormalizedSourceRecord.document_version_id.in_(completed_version_ids)
                )
            ).all()
            if passage_ids:
                # Delete in FK order: deps/conditions → review → extractions → triage → passages
                extraction_ids = db.scalars(
                    select(Extraction.id).where(
                        Extraction.source_record_id.in_(passage_ids)
                    )
                ).all()
                if extraction_ids:
                    db.execute(
                        delete(ApplicabilityCondition).where(
                            ApplicabilityCondition.extraction_id.in_(extraction_ids)
                        )
                    )
                    db.execute(
                        delete(ObligationDependency).where(
                            ObligationDependency.parent_extraction_id.in_(extraction_ids)
                        )
                    )
                    db.execute(
                        delete(ObligationDependency).where(
                            ObligationDependency.child_extraction_id.in_(extraction_ids)
                        )
                    )
                    review_ids = db.scalars(
                        select(ReviewQueueItem.id).where(
                            ReviewQueueItem.extraction_id.in_(extraction_ids)
                        )
                    ).all()
                    if review_ids:
                        db.execute(
                            delete(ReviewAction).where(
                                ReviewAction.queue_item_id.in_(review_ids)
                            )
                        )
                    db.execute(
                        delete(ReviewQueueItem).where(
                            ReviewQueueItem.extraction_id.in_(extraction_ids)
                        )
                    )
                    db.execute(
                        delete(Extraction).where(
                            Extraction.id.in_(extraction_ids)
                        )
                    )
                    extractions_deleted = len(extraction_ids)
                # Delete extraction jobs and failed attempts for these versions
                try:
                    from src.db.models import FailedExtractionAttempt
                    db.execute(
                        delete(FailedExtractionAttempt).where(
                            FailedExtractionAttempt.source_record_id.in_(passage_ids)
                        )
                    )
                except Exception:
                    pass  # Table may not exist yet
                db.execute(
                    delete(ExtractionJob).where(
                        ExtractionJob.document_version_id.in_(completed_version_ids)
                    )
                )
                # Delete triage results
                db.execute(
                    delete(SectionTriageResult).where(
                        SectionTriageResult.source_record_id.in_(passage_ids)
                    )
                )
            passages_deleted = db.scalar(
                select(func.count()).where(
                    NormalizedSourceRecord.document_version_id.in_(completed_version_ids)
                )
            ) or 0
            db.execute(
                delete(NormalizedSourceRecord).where(
                    NormalizedSourceRecord.document_version_id.in_(completed_version_ids)
                )
            )

        reset_to_fetched = 0
        reset_to_pending = 0

        for job in jobs:
            has_artifact = db.scalar(
                select(func.count()).where(
                    RawArtifact.document_version_id == job.document_version_id
                )
            ) or 0

            if has_artifact > 0:
                job.status = IngestionStatus.fetched
                job.error_message = None
                job.parse_started_at = None
                job.parse_completed_at = None
                job.parse_quality_score = None
                reset_to_fetched += 1
            else:
                job.status = IngestionStatus.pending
                job.error_message = None
                job.fetch_started_at = None
                job.fetch_completed_at = None
                job.parse_started_at = None
                job.parse_completed_at = None
                reset_to_pending += 1

        db.commit()

        total = reset_to_fetched + reset_to_pending
        parts = [f'Hard reset <strong>{total}</strong> jobs (including completed).']
        if reset_to_fetched > 0:
            parts.append(f'{reset_to_fetched} will re-parse (files already downloaded).')
        if reset_to_pending > 0:
            parts.append(f'{reset_to_pending} will re-fetch and parse.')
        if extractions_deleted > 0:
            parts.append(f'{extractions_deleted} extractions cleared.')
        if passages_deleted > 0:
            parts.append(f'{passages_deleted} passages cleared for rebuilding.')

        return HTMLResponse(
            f'<div class="result-panel success">{" ".join(parts)}</div>',
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
    """Run section triage on all untriaged passages (runs in background thread)."""
    global _triage_progress

    if _triage_progress is not None and _triage_progress.get("running"):
        return HTMLResponse(
            '<div class="result-panel info" hx-get="/dashboard/api/job-status/triage" '
            'hx-trigger="every 2s" hx-swap="outerHTML">'
            '<span class="spinner"></span> Triage is already running&hellip;</div>'
        )

    if _background_jobs.get("triage", {}).get("running"):
        return HTMLResponse(
            '<div class="result-panel info" hx-get="/dashboard/api/job-status/triage" '
            'hx-trigger="every 2s" hx-swap="outerHTML">'
            '<span class="spinner"></span> Triage already running&hellip;</div>'
        )

    # Precondition: passages must exist before triage can do anything.
    passage_count = db.scalar(
        select(func.count()).select_from(NormalizedSourceRecord)
    ) or 0
    if passage_count == 0:
        return HTMLResponse(
            '<div class="result-panel warning">'
            '<strong>No passages to triage.</strong> '
            'Run <strong>Seed &amp; Ingest All</strong> (Step&nbsp;1) first to parse '
            'documents into passages, then come back here to triage them.'
            '</div>'
        )

    def _do_triage(db):
        global _triage_progress
        from src.ingestion.extractor import run_triage

        _triage_progress = {"running": True, "relevant": 0, "uncertain": 0, "skipped": 0, "total": 0, "done": 0}

        def _on_progress(msg: str):
            pass  # Logged by run_triage internally

        summary = run_triage(db, on_progress=_on_progress)

        _triage_progress = {"running": False, **summary}

        if summary["total"] == 0:
            return (
                '<div class="result-panel info">No untriaged passages found — '
                'all passages are already triaged. '
                'Use <strong>Reset Triage</strong> if you want to re-run uncertain results.</div>'
            )

        return (
            f'<div class="result-panel success">'
            f'Triaged <strong>{summary["total"]}</strong> passages: '
            f'<span style="color:var(--success);">{summary["relevant"]} relevant</span>, '
            f'<span style="color:var(--warning);">{summary["uncertain"]} uncertain</span>, '
            f'<span style="color:var(--text-muted);">{summary["skipped"]} skipped</span>. '
            f'<a href="/dashboard/triage">View results &rarr;</a>'
            f'</div>'
        )

    _run_in_background("triage", _do_triage)
    return HTMLResponse(
        '<div class="result-panel info" hx-get="/dashboard/api/job-status/triage" '
        'hx-trigger="every 2s" hx-swap="outerHTML">'
        '<span class="spinner"></span> Triage started&hellip; '
        'Watch the pipeline tracker above for live progress.</div>'
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
    from sqlalchemy import delete, or_

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


@router.post("/api/run/triage/reset-all")
def reset_triage_all(db: Session = Depends(get_db)) -> HTMLResponse:
    """Hard reset: clear ALL triage results so every passage is re-triaged."""
    from sqlalchemy import delete

    from src.db.models import SectionTriageResult
    from src.ingestion.extractor import _ensure_triage_table

    try:
        _ensure_triage_table(db)

        count = db.scalar(
            select(func.count()).select_from(SectionTriageResult)
        ) or 0

        if count == 0:
            return HTMLResponse(
                '<div class="result-panel info">No triage results to clear.</div>'
            )

        db.execute(delete(SectionTriageResult))
        db.commit()

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Cleared <strong>all {count}</strong> triage results. '
            f'Hit <strong>Triage Passages</strong> to re-triage everything with the updated triager.'
            f'</div>',
            headers={"HX-Trigger": "pipelineReset"},
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Reset error: {html_escape(str(e))}</div>'
        )


@router.get("/api/triage-results")
def triage_results_detail(db: Session = Depends(get_db)) -> HTMLResponse:
    """Return detailed triage results breakdown: by method, quality issues, low-confidence passages."""
    from src.db.models import (
        DocumentFamily,
        DocumentVersion,
        NormalizedSourceRecord,
        SectionTriageResult,
    )

    try:
        total = db.scalar(
            select(func.count()).select_from(SectionTriageResult)
        ) or 0

        if total == 0:
            return HTMLResponse(
                '<div class="result-panel info">No triage results yet. '
                'Run triage first.</div>'
            )

        # --- Breakdown by decision ---
        decision_counts = {}
        for row in db.execute(
            select(SectionTriageResult.decision, func.count())
            .group_by(SectionTriageResult.decision)
        ):
            decision_counts[row[0].value if hasattr(row[0], "value") else str(row[0])] = row[1]

        # --- Breakdown by method ---
        method_counts = {}
        for row in db.execute(
            select(SectionTriageResult.method, func.count())
            .group_by(SectionTriageResult.method)
        ):
            method_counts[row[0].value if hasattr(row[0], "value") else str(row[0])] = row[1]

        # --- Quality failures (method=quality_fail) ---
        quality_fail_count = method_counts.get("quality_fail", 0)

        # --- LLM failures (method=passthrough — all are LLM failures) ---
        passthrough_count = method_counts.get("passthrough", 0)

        # --- Low-confidence passages (relevant/uncertain with confidence < 0.5) ---
        low_conf_count = db.scalar(
            select(func.count()).where(
                SectionTriageResult.decision.in_(["relevant", "uncertain"]),
                SectionTriageResult.confidence < 0.5,
            )
        ) or 0

        # --- Quality flags summary ---
        # Get the most common quality flags across all triaged passages
        quality_flag_rows = db.execute(
            select(SectionTriageResult.quality_flags)
            .where(SectionTriageResult.quality_flags.isnot(None))
            .limit(2000)
        ).all()
        flag_counts: dict[str, int] = {}
        for (flags,) in quality_flag_rows:
            if isinstance(flags, list):
                for f in flags:
                    flag_counts[f] = flag_counts.get(f, 0) + 1

        # --- Problem passages: LLM failures + quality issues + low confidence ---
        _problem_cols = [
            SectionTriageResult.id,
            SectionTriageResult.decision,
            SectionTriageResult.method,
            SectionTriageResult.confidence,
            SectionTriageResult.pdf_quality_score,
            SectionTriageResult.quality_flags,
            SectionTriageResult.llm_reasoning,
            NormalizedSourceRecord.section_path,
            NormalizedSourceRecord.text_content,
            DocumentFamily.label,
        ]
        _problem_join = (
            select(*_problem_cols)
            .join(NormalizedSourceRecord, SectionTriageResult.source_record_id == NormalizedSourceRecord.id)
            .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
            .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
        )

        # Query 1: LLM failures (passthrough method) — show these first
        llm_fail_rows = db.execute(
            _problem_join.where(
                SectionTriageResult.method == "passthrough",
            )
            .order_by(SectionTriageResult.created_at.desc())
            .limit(30)
        ).all()

        # Query 2: Quality/confidence issues (non-passthrough)
        quality_rows = db.execute(
            _problem_join.where(
                SectionTriageResult.method != "passthrough",
                (
                    (SectionTriageResult.pdf_quality_score < 0.5)
                    | (SectionTriageResult.method == "quality_fail")
                    | (
                        SectionTriageResult.decision.in_(["relevant", "uncertain"])
                        & (SectionTriageResult.confidence < 0.5)
                    )
                ),
            )
            .order_by(SectionTriageResult.pdf_quality_score.asc().nullslast())
            .limit(20)
        ).all()

        # --- Build HTML ---
        # Decision summary
        decision_colors = {
            "relevant": "var(--success)", "not_relevant": "var(--text-muted)",
            "uncertain": "var(--warning)",
        }
        decision_html = ""
        for dec in ["relevant", "not_relevant", "uncertain"]:
            count = decision_counts.get(dec, 0)
            pct = round(count / total * 100, 1) if total else 0
            color = decision_colors.get(dec, "var(--text)")
            label = dec.replace("_", " ").title()
            decision_html += (
                f'<div style="display:flex;justify-content:space-between;padding:3px 0;">'
                f'<span style="color:{color};font-weight:600;">{label}</span>'
                f'<span>{count} ({pct}%)</span></div>'
            )

        # Method summary
        method_labels = {
            "keyword": "Keyword match",
            "orrick_cross_check": "Orrick + LLM",
            "llm_generic": "LLM generic",
            "quality_fail": "Quality fail",
            "passthrough": "Passthrough",
            "manual_review": "Manual review",
        }
        method_html = ""
        for method in ["keyword", "orrick_cross_check", "llm_generic", "quality_fail", "passthrough", "manual_review"]:
            count = method_counts.get(method, 0)
            if count == 0:
                continue
            label = method_labels.get(method, method)
            is_problem = method in ("quality_fail", "passthrough")
            style = ' style="color:var(--danger);"' if is_problem else ""
            method_html += (
                f'<div style="display:flex;justify-content:space-between;padding:3px 0;">'
                f'<span{style}>{label}</span>'
                f'<span{style}>{count}</span></div>'
            )

        # Quality flags
        flags_html = ""
        if flag_counts:
            flags_html = '<div style="margin-top:8px;"><strong>Quality Flags:</strong></div>'
            for flag, cnt in sorted(flag_counts.items(), key=lambda x: -x[1]):
                flags_html += (
                    f'<div style="display:flex;justify-content:space-between;padding:2px 0;'
                    f'font-size:12px;color:var(--danger);">'
                    f'<span>{html_escape(flag)}</span><span>{cnt}</span></div>'
                )

        # --- Helper to render a problem rows table ---
        def _render_problem_table(rows, title, subtitle):
            if not rows:
                return ""
            html_out = (
                f'<div style="margin-top:12px;">'
                f'<strong>{title}</strong> '
                f'<span style="font-size:12px;color:var(--text-muted);">{subtitle}</span>'
                f'</div>'
                '<table class="review-table" style="margin-top:6px;font-size:12px;">'
                '<thead><tr>'
                '<th>Document</th><th>Section</th><th>Decision</th>'
                '<th>Method</th><th>Conf.</th><th>Quality</th><th>Flags</th>'
                '<th>Error / Reasoning</th>'
                '</tr></thead><tbody>'
            )
            for row in rows:
                tr_id, dec, meth, conf, qual, flags, reasoning, section, text_content, doc_label = row
                dec_str = dec.value if hasattr(dec, "value") else str(dec)
                meth_str = meth.value if hasattr(meth, "value") else str(meth)
                flags_str = ", ".join(flags) if isinstance(flags, list) and flags else "—"
                reason_full = html_escape((reasoning or "—")[:500])
                reason_short = html_escape((reasoning or "—")[:120])
                qual_str = f"{qual:.2f}" if qual is not None else "—"
                conf_str = f"{conf:.2f}" if conf is not None else "—"
                section_str = html_escape((section or "—")[:40])
                doc_str = html_escape((doc_label or "—")[:30])
                # Snippet of the passage for context
                snippet = html_escape((text_content or "")[:80])

                qual_color = "var(--danger)" if qual is not None and qual < 0.3 else (
                    "var(--warning)" if qual is not None and qual < 0.6 else "var(--text)"
                )
                # Highlight LLM failures
                is_llm_fail = isinstance(flags, list) and any("llm_" in f for f in flags)
                row_style = ' style="background:rgba(255,0,0,0.05);"' if is_llm_fail else ""

                html_out += (
                    f'<tr{row_style}>'
                    f'<td title="{html_escape(doc_label or "")}">{doc_str}</td>'
                    f'<td title="{html_escape(section or "")}\n---\n{snippet}">{section_str}</td>'
                    f'<td>{dec_str}</td>'
                    f'<td>{meth_str}</td>'
                    f'<td>{conf_str}</td>'
                    f'<td style="color:{qual_color};">{qual_str}</td>'
                    f'<td style="font-size:11px;color:var(--danger);">{html_escape(flags_str)}</td>'
                    f'<td style="font-size:11px;max-width:300px;overflow:hidden;'
                    f'text-overflow:ellipsis;white-space:nowrap;"'
                    f' title="{reason_full}">{reason_short}</td>'
                    f'</tr>'
                )
            html_out += '</tbody></table>'
            return html_out

        # Render LLM failures table
        llm_fail_html = _render_problem_table(
            llm_fail_rows,
            f"LLM Failures ({len(llm_fail_rows)} shown)",
            "(model returned garbage, timed out, or HTTP error — defaulted to uncertain/passthrough)",
        )

        # Render quality/confidence issues table
        quality_html = _render_problem_table(
            quality_rows,
            f"Quality / Confidence Issues ({len(quality_rows)} shown)",
            "(low PDF quality, quality fail, or low-confidence relevant/uncertain)",
        )

        problems_html = llm_fail_html + quality_html

        # Alert badges
        alerts = []
        if passthrough_count > 0:
            alerts.append(
                f'<span class="tracker-badge failed">{passthrough_count} LLM failures (passthrough)</span> '
                f'<span hx-get="/dashboard/api/failed-triage-count" '
                f'hx-trigger="load" hx-swap="outerHTML"></span>'
            )
        if quality_fail_count > 0:
            alerts.append(
                f'<span class="tracker-badge failed">{quality_fail_count} quality failures</span>'
            )
        if low_conf_count > 0:
            alerts.append(
                f'<span class="tracker-badge" style="background:var(--warning);color:#000;">'
                f'{low_conf_count} low confidence</span>'
            )

        alerts_html = " ".join(alerts) if alerts else (
            '<span style="color:var(--success);">No issues detected.</span>'
        )

        html = f"""
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:12px;">
          <div>
            <strong>Decisions</strong> <span style="font-size:12px;color:var(--text-muted);">({total} total)</span>
            {decision_html}
          </div>
          <div>
            <strong>Methods</strong>
            {method_html}
            {flags_html}
          </div>
        </div>
        <div style="margin-bottom:8px;">
          <strong>Alerts:</strong> {alerts_html}
        </div>
        {problems_html}
        """
        return HTMLResponse(html)

    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error loading triage results: {html_escape(str(e))}</div>'
        )


@router.get("/api/triage-warnings")
def triage_warnings(limit: int = 200) -> HTMLResponse:
    """Return triage warnings from output/triage_warnings.jsonl."""
    import json as _json
    from pathlib import Path

    log_path = Path("output/triage_warnings.jsonl")
    if not log_path.exists():
        return HTMLResponse(
            '<div class="result-panel info">No triage warnings recorded yet.</div>'
        )

    try:
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        entries = [_json.loads(line) for line in lines[-limit:]]

        if not entries:
            return HTMLResponse(
                '<div class="result-panel info">No triage warnings recorded yet.</div>'
            )

        # Group by warning type
        by_type: dict[str, int] = {}
        for e in entries:
            wt = e.get("warning_type", "unknown")
            by_type[wt] = by_type.get(wt, 0) + 1

        summary_chips = " ".join(
            f'<span class="status-chip" style="background:var(--warning);color:#000;">'
            f'{wtype}: {count}</span>'
            for wtype, count in sorted(by_type.items(), key=lambda x: -x[1])
        )

        rows_html = ""
        for e in reversed(entries[-100:]):
            ts = e.get("timestamp", "")[:19].replace("T", " ")
            wt = html_escape(e.get("warning_type", ""))
            rid = e.get("record_id", "—")
            details = html_escape(e.get("details", "")[:200])
            raw = html_escape(e.get("raw_response", "")[:150])
            raw_cell = f'<td style="font-size:10px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="{html_escape(e.get("raw_response", ""))}">{raw}</td>' if raw else '<td>—</td>'

            rows_html += (
                f'<tr>'
                f'<td style="white-space:nowrap;">{ts}</td>'
                f'<td><code>{wt}</code></td>'
                f'<td>{rid}</td>'
                f'<td style="max-width:300px;">{details}</td>'
                f'{raw_cell}'
                f'</tr>'
            )

        copy_js = (
            "var rows = document.querySelectorAll('#triage-warn-table tbody tr');"
            "var lines = ['Time\\tType\\tRecord\\tDetails\\tRaw Response'];"
            "rows.forEach(function(r){"
            "  var cells = r.querySelectorAll('td');"
            "  lines.push(Array.from(cells).map(function(c){return c.innerText;}).join('\\t'));"
            "});"
            "navigator.clipboard.writeText(lines.join('\\n'))"
            ".then(function(){alert('Copied ' + rows.length + ' rows to clipboard.');})"
            ".catch(function(){alert('Copy failed — use Download CSV instead.');});"
        )
        return HTMLResponse(
            f'<div style="margin-bottom:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">'
            f'<strong>{len(entries)}</strong> warnings total. {summary_chips}'
            f'<a class="btn" style="font-size:11px;padding:2px 8px;" '
            f'href="/dashboard/api/triage-warnings/export.csv">&#11015; Download CSV</a>'
            f'<button class="btn" style="font-size:11px;padding:2px 8px;" '
            f'onclick="{html_escape(copy_js)}">&#128203; Copy to Clipboard</button>'
            f'<button class="btn" style="font-size:11px;padding:2px 8px;" '
            f'hx-post="/dashboard/api/triage-warnings/clear" hx-target="#triage-warnings-panel" '
            f'hx-swap="innerHTML" hx-confirm="Clear all triage warnings?">Clear Log</button>'
            f'</div>'
            f'<div style="max-height:400px;overflow:auto;">'
            f'<table id="triage-warn-table" class="tracker-table" style="font-size:11px;">'
            f'<thead><tr><th>Time</th><th>Type</th><th>Record</th>'
            f'<th>Details</th><th>Raw Response</th></tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>'
        )
    except Exception as e:
        return HTMLResponse(
            f'<div class="result-panel error">Error reading warnings: {html_escape(str(e))}</div>'
        )


@router.post("/api/triage-warnings/clear")
def clear_triage_warnings() -> HTMLResponse:
    """Clear the triage warnings log file."""
    from pathlib import Path
    log_path = Path("output/triage_warnings.jsonl")
    if log_path.exists():
        log_path.unlink()
    return HTMLResponse(
        '<div class="result-panel success">Triage warnings cleared.</div>'
    )


@router.get("/api/triage-warnings/export.csv")
def export_triage_warnings_csv() -> StreamingResponse:
    """Download all triage warnings as a CSV file."""
    import csv
    import io
    import json as _json
    from pathlib import Path

    log_path = Path("output/triage_warnings.jsonl")
    entries = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").strip().splitlines():
            try:
                entries.append(_json.loads(line))
            except Exception:
                pass

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "warning_type", "record_id", "details", "raw_response"])
    for e in entries:
        writer.writerow([
            e.get("timestamp", ""),
            e.get("warning_type", ""),
            e.get("record_id", ""),
            e.get("details", ""),
            e.get("raw_response", ""),
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=triage_warnings.csv"},
    )


@router.get("/api/failed-extractions/export.csv")
def export_failed_extractions_csv(db: Session = Depends(get_db)) -> StreamingResponse:
    """Download all failed extraction attempts as a CSV file."""
    import csv
    import io

    from src.db.models import FailedExtractionAttempt

    rows = db.scalars(
        select(FailedExtractionAttempt).order_by(FailedExtractionAttempt.created_at.desc())
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "source_record_id", "agent_name", "error_type",
        "error_message", "extraction_job_id", "retried", "retry_succeeded", "created_at",
    ])
    for r in rows:
        writer.writerow([
            r.id, r.source_record_id, r.agent_name, r.error_type,
            r.error_message, r.extraction_job_id, r.retried, r.retry_succeeded, r.created_at,
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=failed_extractions.csv"},
    )


@router.get("/api/low-confidence/export.csv")
def export_low_confidence_extractions_csv(
    db: Session = Depends(get_db),
    tier: str | None = None,
) -> StreamingResponse:
    """Download low-confidence extractions (Tier C and/or D) as CSV for LLM review.

    Query params:
      - tier: filter by 'C', 'D', or 'C,D' (default: both)
    """
    import csv
    import io
    import json as _json

    from src.db.models import ConfidenceTier, NormalizedSourceRecord

    tiers_to_export = []
    if tier:
        if 'C' in tier.upper():
            tiers_to_export.append(ConfidenceTier.c)
        if 'D' in tier.upper():
            tiers_to_export.append(ConfidenceTier.d)
    else:
        tiers_to_export = [ConfidenceTier.c, ConfidenceTier.d]

    # Fetch extractions with full context
    query = (
        select(Extraction, NormalizedSourceRecord, DocumentVersion)
        .join(NormalizedSourceRecord, Extraction.source_record_id == NormalizedSourceRecord.id)
        .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
        .where(Extraction.confidence_tier.in_(tiers_to_export))
        .order_by(Extraction.confidence_score.asc(), Extraction.created_at.desc())
    )

    rows = db.execute(query).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "extraction_id",
        "law_jurisdiction",
        "law_title",
        "extraction_type",
        "confidence_score",
        "confidence_tier",
        "passage_text",
        "evidence_spans",
        "payload_summary",
        "full_payload_json",
        "review_status",
        "created_at",
    ])

    for ext, rec, dv in rows:
        doc_family = dv.family
        jurisdiction = doc_family.source.jurisdiction_code if doc_family and doc_family.source else "Unknown"

        # Build payload summary (first 300 chars of JSON representation)
        payload_json = _json.dumps(ext.payload, default=str)
        payload_summary = payload_json[:300] + ("..." if len(payload_json) > 300 else "")

        # Format evidence spans
        spans_str = "; ".join([
            f"{s.get('text', '')[:50]}... (conf: {s.get('confidence_score', 0):.2f})"
            for s in (ext.evidence_spans or [])
        ]) if ext.evidence_spans else "No evidence spans"

        writer.writerow([
            ext.id,
            jurisdiction,
            doc_family.canonical_title if doc_family else "",
            ext.extraction_type.value,
            f"{ext.confidence_score:.3f}",
            ext.confidence_tier.value,
            (rec.normalized_text or "")[:500],  # First 500 chars of passage
            spans_str[:200],  # Truncate for CSV
            payload_summary,
            payload_json,  # Full JSON for reference
            ext.review_status.value,
            ext.created_at.isoformat() if ext.created_at else "",
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=low_confidence_extractions.csv"},
    )


@router.get("/api/low-confidence/export.jsonl")
def export_low_confidence_extractions_jsonl(
    db: Session = Depends(get_db),
    tier: str | None = None,
) -> StreamingResponse:
    """Download low-confidence extractions as JSONL for batch LLM processing.

    Query params:
      - tier: filter by 'C', 'D', or 'C,D' (default: both)

    Each line is a complete JSON object with full context:
      - extraction metadata (id, type, confidence, tier)
      - law metadata (jurisdiction, title, bill_number)
      - passage text and evidence spans
      - full extraction payload
    """
    import json as _json

    from src.db.models import ConfidenceTier, NormalizedSourceRecord

    tiers_to_export = []
    if tier:
        if 'C' in tier.upper():
            tiers_to_export.append(ConfidenceTier.c)
        if 'D' in tier.upper():
            tiers_to_export.append(ConfidenceTier.d)
    else:
        tiers_to_export = [ConfidenceTier.c, ConfidenceTier.d]

    query = (
        select(Extraction, NormalizedSourceRecord, DocumentVersion)
        .join(NormalizedSourceRecord, Extraction.source_record_id == NormalizedSourceRecord.id)
        .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
        .where(Extraction.confidence_tier.in_(tiers_to_export))
        .order_by(Extraction.confidence_score.asc(), Extraction.created_at.desc())
    )

    rows = db.execute(query).all()

    def generate():
        for ext, rec, dv in rows:
            doc_family = dv.family
            obj = {
                "extraction": {
                    "id": ext.id,
                    "type": ext.extraction_type.value,
                    "confidence_score": float(ext.confidence_score),
                    "confidence_tier": ext.confidence_tier.value,
                    "review_status": ext.review_status.value,
                    "created_at": ext.created_at.isoformat() if ext.created_at else None,
                    "payload": ext.payload,
                },
                "law": {
                    "jurisdiction": doc_family.source.jurisdiction_code if doc_family and doc_family.source else "Unknown",
                    "title": doc_family.canonical_title if doc_family else "",
                    "bill_number": doc_family.metadata_.get("bill_number", "") if doc_family and doc_family.metadata_ else "",
                },
                "passage": {
                    "text": rec.normalized_text,
                    "source_record_id": rec.id,
                },
                "evidence_spans": ext.evidence_spans or [],
            }
            yield _json.dumps(obj, default=str) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=low_confidence_extractions.jsonl"},
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
        # Also clear downstream: applicability_conditions, obligation_dependencies,
        # failed_extraction_attempts
        db.execute(delete(ApplicabilityCondition))
        db.execute(delete(ObligationDependency))
        db.execute(delete(ReviewAction))
        db.execute(delete(ReviewQueueItem))
        # Clear failed attempts (references both extractions and extraction_jobs)
        try:
            from src.db.models import FailedExtractionAttempt
            db.execute(delete(FailedExtractionAttempt))
        except Exception:
            pass  # Table may not exist yet
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


@router.post("/api/run/generate-summaries")
def generate_summaries(
    overwrite: bool = False,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Generate plain-English summaries for extractions that don't have one.

    Summaries are deterministic templates built from the verified JSON payload.
    They are stored in metadata_['plain_summary'] and displayed in the review
    queue and product API. The raw payload remains authoritative — summaries
    are presentation-only.
    """
    try:
        from src.core.summary_generator import generate_summaries_batch

        result = generate_summaries_batch(db, overwrite=overwrite)

        if result["total"] == 0:
            return HTMLResponse(
                '<div class="result-panel info">'
                'All extractions already have summaries. '
                'Use overwrite=true to regenerate.'
                '</div>'
            )

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Generated <strong>{result["generated"]}</strong> summaries '
            f'({result["failed"]} failed, {result["total"]} total). '
            f'Summaries are now visible in the review queue.'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error: {html_escape(str(e))}</div>'
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

    # Add manual_review enum value to Postgres if needed.
    # ALTER TYPE ... ADD VALUE must run outside a transaction, so use autocommit.
    try:
        triage.method = TriageMethod.manual_review
        db.flush()  # Test if Postgres accepts the value
    except Exception:
        db.rollback()
        # Re-fetch triage after rollback
        triage = db.get(SectionTriageResult, triage_id)
        try:
            from src.db.engine import engine as _engine
            with _engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text(
                    "ALTER TYPE triagemethod ADD VALUE IF NOT EXISTS 'manual_review'"
                ))
        except Exception:
            pass  # Value may already exist
        # Now retry the update
        triage.decision = TriageDecision(decision)
        triage.confidence = 1.0
        triage.llm_reasoning = f"Manual override: {old_decision} → {decision}"
        triage.method = TriageMethod.manual_review

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
    # Add export buttons if there are low-confidence items
    export_button = ""
    if tiers["C"] + tiers["D"] > 0:
        export_button = (
            f'<div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;">'
            f'<a href="/dashboard/api/low-confidence/export.csv" '
            f'class="btn btn-sm" title="Export as CSV for spreadsheet review"> '
            f'&#11015; CSV ({tiers["C"] + tiers["D"]}) </a>'
            f'<a href="/dashboard/api/low-confidence/export.jsonl" '
            f'class="btn btn-sm" title="Export as JSONL for batch LLM processing"> '
            f'&#11015; JSONL ({tiers["C"] + tiers["D"]}) </a>'
            f'</div>'
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
      {export_button}
    </div>
    """

    # --- Per-agent stats ---
    agent_html = ""
    if d["agent_stats"]:
        agent_rows = ""
        for name, stats in sorted(d["agent_stats"].items()):
            afr = stats["failure_rate"]
            afr_color = "var(--success)" if afr < 0.05 else "var(--warning)" if afr < 0.2 else "var(--danger)"
            avg_ms = stats.get("avg_duration_ms", 0)
            dur_color = "var(--success)" if avg_ms < 10000 else "var(--warning)" if avg_ms < 30000 else "var(--danger)"
            dur_label = f"{avg_ms/1000:.1f}s" if avg_ms else "—"
            agent_rows += f"""
            <tr>
              <td><code>{html_escape(name)}</code></td>
              <td>{stats['calls']}</td>
              <td style="color:var(--success);">{stats['successes']}</td>
              <td>{stats['abstentions']}</td>
              <td style="color:{'var(--danger)' if stats['errors'] > 0 else 'var(--text-muted)'};">{stats['errors']}</td>
              <td style="color:{afr_color};">{afr:.0%}</td>
              <td>{stats['tokens']:,}</td>
              <td style="color:{dur_color};">{dur_label}</td>
            </tr>
            """
        agent_html = f"""
        <div style="margin-bottom:12px;">
          <div style="font-size:12px;font-weight:600;margin-bottom:4px;">Agent Performance</div>
          <table class="data-table" style="font-size:12px;">
            <thead><tr>
              <th>Agent</th><th>Calls</th><th>OK</th><th>Abstain</th>
              <th>Errors</th><th>Fail%</th><th>Tokens</th><th>Avg Time</th>
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


@router.post("/api/run/extract/pause")
def pause_extract() -> HTMLResponse:
    """Pause the extraction loop between passages (resumable)."""
    from src.ingestion.extractor import is_cancelled, is_paused, request_pause

    if is_cancelled():
        return HTMLResponse('<div class="result-panel warning">Run already terminated.</div>')
    if is_paused():
        return HTMLResponse('<div class="result-panel info">Already paused.</div>')
    request_pause()
    return HTMLResponse(
        '<div class="result-panel info">'
        'Paused — current passage will finish, then the loop will wait. '
        '<button class="btn btn-sm" style="margin-left:8px;" '
        'hx-post="/dashboard/api/run/extract/resume" '
        'hx-target="#extract-pause-status" hx-swap="innerHTML">Resume</button>'
        '</div>'
    )


@router.post("/api/run/extract/resume")
def resume_extract() -> HTMLResponse:
    """Resume a paused extraction run."""
    from src.ingestion.extractor import is_cancelled, is_paused, request_resume

    if is_cancelled():
        return HTMLResponse('<div class="result-panel warning">Run was terminated — start a new extraction.</div>')
    if not is_paused():
        return HTMLResponse('<div class="result-panel info">Extraction is already running.</div>')
    request_resume()
    return HTMLResponse(
        '<div class="result-panel success">'
        'Resumed. '
        '<button class="btn btn-sm" style="margin-left:8px;" '
        'hx-post="/dashboard/api/run/extract/pause" '
        'hx-target="#extract-pause-status" hx-swap="innerHTML">Pause</button>'
        '</div>'
    )


@router.get("/api/run/extract/health")
def extract_health(db: Session = Depends(get_db)) -> HTMLResponse:
    """Return a short health snippet: pause state, stuck detection, recent errors."""
    from sqlalchemy import desc
    from sqlalchemy import select as sa_select

    from src.db.models import PipelineEvent
    from src.ingestion.extractor import is_cancelled, is_paused, seconds_since_last_passage

    paused = is_paused()
    cancelled = is_cancelled()
    idle_secs = seconds_since_last_passage()

    # Stuck if passage started > 90 s ago and run is not paused/cancelled
    stuck = idle_secs > 90 and not paused and not cancelled and idle_secs > 0

    # Last 5 agent_error events across any recent run. The pipeline_events
    # table is created by migration t6u2v8w0x021; if it is missing (migration
    # not yet applied) degrade gracefully rather than poisoning the session.
    error_rows = []
    try:
        error_rows = db.execute(
            sa_select(
                PipelineEvent.agent_name,
                PipelineEvent.error_message,
                PipelineEvent.created_at,
                PipelineEvent.model_id,
                PipelineEvent.details,
            )
            .where(PipelineEvent.event_type == "agent_error")
            .order_by(desc(PipelineEvent.created_at))
            .limit(10)
        ).all()
    except Exception:
        db.rollback()

    # Provider-health badge: if recent failures are auth/quota errors, the run
    # is failing for a *backend* reason (e.g. out of NVIDIA credits) — prompt a
    # switch rather than letting it look like generic model noise.
    provider_badge = ""
    quota_hits = [
        r for r in error_rows
        if (r.details or {}).get("error_type") in ("quota_error", "auth_error")
    ]
    if quota_hits:
        kinds = {(r.details or {}).get("error_type") for r in quota_hits}
        on_nvidia = any((r.model_id or "").endswith("-nvidia") for r in quota_hits)
        kind_label = "quota/credit" if "quota_error" in kinds else "auth"
        backend = "NVIDIA" if on_nvidia else "the provider"
        provider_badge = (
            '<div style="margin-top:6px;padding:6px 10px;background:rgba(243,156,18,0.12);'
            'border-left:3px solid #f39c12;font-size:0.82em;">'
            f'&#9888; <strong>{kind_label} errors from {backend}</strong> '
            f'({len(quota_hits)} recent). '
            'Consider switching the extraction provider on the '
            '<a href="/dashboard/models">Models page</a> and retrying failed items.'
            '</div>'
        )

    state_badge = ""
    if cancelled:
        state_badge = '<span style="color:#e74c3c;font-weight:600;">● Terminated</span>'
    elif paused:
        state_badge = '<span style="color:#f39c12;font-weight:600;">⏸ Paused</span>'
    elif stuck:
        m = int(idle_secs // 60)
        s = int(idle_secs % 60)
        state_badge = f'<span style="color:#e74c3c;font-weight:600;">⚠ Possibly stuck ({m}m {s}s on current passage)</span>'
    elif idle_secs > 0:
        state_badge = f'<span style="color:#27ae60;font-weight:600;">▶ Running</span> <span style="color:#888;font-size:0.85em;">last passage {int(idle_secs)}s ago</span>'

    _ETYPE_COLOR = {
        "quota_error": "#f39c12",
        "auth_error": "#e74c3c",
        "validation_error": "#e67e22",
        "timeout_error": "#9b59b6",
        "llm_error": "#888",
        "db_error": "#8e44ad",
    }

    errors_html = ""
    if error_rows:
        def _row(r):
            etype = (r.details or {}).get("error_type", "")
            type_tag = ""
            if etype:
                type_tag = (
                    f'<span style="font-size:0.72em;color:{_ETYPE_COLOR.get(etype, "#888")};'
                    f'font-weight:600;">{html_escape(etype)}</span> '
                )
            backend = ""
            if r.model_id:
                backend = "NVIDIA" if r.model_id.endswith("-nvidia") else "local"
            return (
                f'<tr><td style="padding:2px 6px;color:#888;font-size:0.8em;white-space:nowrap;">'
                f'{html_escape(r.agent_name or "—")}'
                f'{(" · " + backend) if backend else ""}</td>'
                f'<td style="padding:2px 6px;font-size:0.8em;max-width:520px;overflow:hidden;'
                f'text-overflow:ellipsis;white-space:nowrap;" title="{html_escape(r.error_message or "")}">'
                f'{type_tag}{html_escape((r.error_message or "")[:300])}</td>'
                f'<td style="padding:2px 6px;color:#888;font-size:0.8em;white-space:nowrap;">'
                f'{r.created_at.strftime("%H:%M:%S") if r.created_at else ""}</td>'
                f'</tr>'
            )
        rows = "".join(_row(r) for r in error_rows[:5])
        errors_html = (
            '<div style="margin-top:6px;">'
            '<div style="font-size:0.8em;color:#888;margin-bottom:2px;">Recent agent errors</div>'
            f'<table style="border-collapse:collapse;width:100%;">{rows}</table>'
            '</div>'
        )

    return HTMLResponse(
        f'<div style="font-size:0.85em;">{state_badge}{provider_badge}{errors_html}</div>'
    )


@router.get("/api/run/extract/latency")
def extract_latency(db: Session = Depends(get_db)) -> HTMLResponse:
    """Per-agent p50/p95 latency from PipelineEvent rows (last 24 h)."""
    from sqlalchemy import text as sa_text

    try:
        rows = db.execute(sa_text("""
            SELECT
                agent_name,
                COUNT(*) AS call_count,
                ROUND(AVG(duration_ms))::int AS avg_ms,
                ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms))::int AS p50_ms,
                ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms))::int AS p95_ms,
                MAX(duration_ms) AS max_ms
            FROM pipeline_events
            WHERE event_type IN ('agent_success', 'agent_error')
              AND duration_ms IS NOT NULL
              AND created_at > NOW() - INTERVAL '24 hours'
            GROUP BY agent_name
            ORDER BY avg_ms DESC NULLS LAST
        """)).all()
    except Exception:
        db.rollback()
        return HTMLResponse(
            '<div style="color:var(--text-muted);font-size:13px;padding:8px 0;">'
            'Latency data unavailable — run <code>alembic upgrade head</code> to '
            'create the <code>pipeline_events</code> table.</div>'
        )

    if not rows:
        return HTMLResponse(
            '<div style="color:var(--text-muted);font-size:13px;padding:8px 0;">'
            'No latency data in the last 24 hours.</div>'
        )

    def fmt_ms(ms):
        if ms is None:
            return "—"
        if ms >= 60_000:
            return f"{ms / 60_000:.1f}m"
        if ms >= 1_000:
            return f"{ms / 1_000:.1f}s"
        return f"{ms}ms"

    max_p95 = max((r.p95_ms or 0) for r in rows) or 1

    trs = ""
    for r in rows:
        p95 = r.p95_ms or 0
        bar_pct = round(p95 / max_p95 * 100)
        bar_color = "#27ae60" if p95 < 10_000 else "#f39c12" if p95 < 30_000 else "#e74c3c"
        trs += (
            f'<tr>'
            f'<td style="padding:3px 8px;font-size:12px;"><code>{html_escape(r.agent_name or "")}</code></td>'
            f'<td style="padding:3px 8px;font-size:12px;text-align:right;">{r.call_count}</td>'
            f'<td style="padding:3px 8px;font-size:12px;text-align:right;">{fmt_ms(r.avg_ms)}</td>'
            f'<td style="padding:3px 8px;font-size:12px;text-align:right;">{fmt_ms(r.p50_ms)}</td>'
            f'<td style="padding:3px 8px;font-size:12px;text-align:right;">{fmt_ms(r.p95_ms)}</td>'
            f'<td style="padding:3px 8px;min-width:80px;">'
            f'<div style="height:8px;border-radius:3px;background:{bar_color};width:{bar_pct}%;"></div>'
            f'</td>'
            f'</tr>'
        )

    return HTMLResponse(
        '<table style="border-collapse:collapse;width:100%;">'
        '<thead><tr style="border-bottom:1px solid var(--border);">'
        '<th style="padding:3px 8px;font-size:11px;text-align:left;">Agent</th>'
        '<th style="padding:3px 8px;font-size:11px;text-align:right;">Calls</th>'
        '<th style="padding:3px 8px;font-size:11px;text-align:right;">Avg</th>'
        '<th style="padding:3px 8px;font-size:11px;text-align:right;">P50</th>'
        '<th style="padding:3px 8px;font-size:11px;text-align:right;">P95</th>'
        '<th style="padding:3px 8px;font-size:11px;">P95 bar</th>'
        '</tr></thead>'
        f'<tbody>{trs}</tbody>'
        '</table>'
        '<div style="font-size:10px;color:var(--text-muted);margin-top:4px;">Last 24 h · green &lt;10s · yellow &lt;30s · red ≥30s</div>'
    )


@router.get("/api/run/failed-detail")
def get_failed_detail(db: Session = Depends(get_db)) -> HTMLResponse:
    """Unretried FailedExtractionAttempt rows with section path and full error."""
    from sqlalchemy import desc

    rows = db.execute(
        select(
            FailedExtractionAttempt.id,
            FailedExtractionAttempt.agent_name,
            FailedExtractionAttempt.error_type,
            FailedExtractionAttempt.error_message,
            FailedExtractionAttempt.created_at,
            NormalizedSourceRecord.section_path,
            NormalizedSourceRecord.id.label("record_id"),
        )
        .join(NormalizedSourceRecord,
              FailedExtractionAttempt.source_record_id == NormalizedSourceRecord.id,
              isouter=True)
        .where(FailedExtractionAttempt.retried == False)  # noqa: E712
        .order_by(desc(FailedExtractionAttempt.created_at))
        .limit(100)
    ).all()

    if not rows:
        return HTMLResponse(
            '<div style="color:var(--text-muted);font-size:13px;padding:8px 0;">'
            'No unretried failures.</div>'
        )

    type_colors = {
        "llm_error": "#e74c3c",
        "validation_error": "#e67e22",
        "db_error": "#8e44ad",
        "quota_error": "#f39c12",
        "auth_error": "#e74c3c",
        "timeout_error": "#9b59b6",
    }

    trs = ""
    for r in rows:
        color = type_colors.get(r.error_type or "", "#888")
        section = html_escape(r.section_path or "—")
        msg_short = html_escape((r.error_message or "")[:300])
        msg_full = html_escape(r.error_message or "")
        ts = r.created_at.strftime("%m-%d %H:%M") if r.created_at else ""
        trs += (
            f'<tr style="border-bottom:1px solid var(--border);">'
            f'<td style="padding:4px 6px;font-size:11px;white-space:nowrap;">{ts}</td>'
            f'<td style="padding:4px 6px;font-size:11px;">'
            f'<code>{html_escape(r.agent_name or "")}</code></td>'
            f'<td style="padding:4px 6px;font-size:11px;">'
            f'<span style="color:{color};font-weight:600;">{html_escape(r.error_type or "")}</span></td>'
            f'<td style="padding:4px 6px;font-size:11px;color:var(--text-muted);">{section}</td>'
            f'<td style="padding:4px 6px;font-size:11px;max-width:340px;">'
            f'<span title="{msg_full}">{msg_short}{"…" if len(r.error_message or "") > 300 else ""}</span>'
            f'</td>'
            f'</tr>'
        )

    return HTMLResponse(
        f'<div style="font-size:11px;color:var(--text-muted);margin-bottom:4px;">'
        f'{len(rows)} unretried failure{"s" if len(rows) != 1 else ""}</div>'
        '<div style="overflow-x:auto;">'
        '<table style="border-collapse:collapse;width:100%;min-width:600px;">'
        '<thead><tr style="border-bottom:1px solid var(--border);">'
        '<th style="padding:3px 6px;font-size:11px;text-align:left;">Time</th>'
        '<th style="padding:3px 6px;font-size:11px;text-align:left;">Agent</th>'
        '<th style="padding:3px 6px;font-size:11px;text-align:left;">Type</th>'
        '<th style="padding:3px 6px;font-size:11px;text-align:left;">Section</th>'
        '<th style="padding:3px 6px;font-size:11px;text-align:left;">Error</th>'
        '</tr></thead>'
        f'<tbody>{trs}</tbody>'
        '</table>'
        '</div>'
    )


@router.get("/api/run/pipeline-events")
def get_pipeline_events(
    offset: int = 0,
    limit: int = 50,
    event_type: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Paginated PipelineEvent log with optional event_type filter."""
    from sqlalchemy import desc
    from sqlalchemy import func as sa_func

    q = select(PipelineEvent).order_by(desc(PipelineEvent.created_at))
    if event_type and event_type != "all":
        q = q.where(PipelineEvent.event_type == event_type)

    try:
        total = db.scalar(select(sa_func.count()).select_from(q.subquery())) or 0
        rows = db.scalars(q.offset(offset).limit(limit)).all()
    except Exception:
        db.rollback()
        return HTMLResponse(
            '<div style="color:var(--text-muted);font-size:13px;padding:8px 0;">'
            'Event log unavailable — run <code>alembic upgrade head</code> to '
            'create the <code>pipeline_events</code> table.</div>'
        )

    event_types = ["all", "agent_error", "agent_success", "agent_abstention",
                   "passage_complete", "circuit_breaker", "validation_error", "run_start", "run_complete"]
    filter_buttons = ""
    for et in event_types:
        active = (et == event_type) or (et == "all" and not event_type)
        style = (
            "background:var(--primary);color:#fff;"
            if active else
            "background:var(--bg-secondary);color:var(--text);"
        )
        filter_buttons += (
            f'<button style="padding:2px 8px;font-size:11px;border:1px solid var(--border);'
            f'border-radius:3px;cursor:pointer;{style}" '
            f'hx-get="/dashboard/api/run/pipeline-events?offset=0&limit={limit}&event_type={et}" '
            f'hx-target="#pipeline-event-log" hx-swap="innerHTML">{et}</button>'
        )

    type_colors = {
        "agent_error": "#e74c3c",
        "agent_success": "#27ae60",
        "agent_abstention": "#888",
        "passage_complete": "#3b82f6",
        "circuit_breaker": "#8e44ad",
        "validation_error": "#f39c12",
        "run_start": "#27ae60",
        "run_complete": "#27ae60",
    }

    trs = ""
    for r in rows:
        color = type_colors.get(r.event_type or "", "#888")
        dur = f"{r.duration_ms / 1000:.1f}s" if r.duration_ms else "—"
        msg = html_escape((r.error_message or "")[:300])
        msg_full = html_escape(r.error_message or "")
        ts = r.created_at.strftime("%H:%M:%S") if r.created_at else ""
        backend = "—"
        if r.model_id:
            backend = "NVIDIA" if r.model_id.endswith("-nvidia") else "local"
        trs += (
            f'<tr style="border-bottom:1px solid var(--border);">'
            f'<td style="padding:3px 6px;font-size:11px;white-space:nowrap;color:var(--text-muted);">{ts}</td>'
            f'<td style="padding:3px 6px;font-size:11px;">'
            f'<span style="color:{color};font-weight:600;">{html_escape(r.event_type or "")}</span></td>'
            f'<td style="padding:3px 6px;font-size:11px;"><code>{html_escape(r.agent_name or "")}</code></td>'
            f'<td style="padding:3px 6px;font-size:11px;color:var(--text-muted);" title="{html_escape(r.model_id or "")}">{backend}</td>'
            f'<td style="padding:3px 6px;font-size:11px;text-align:right;">{dur}</td>'
            f'<td style="padding:3px 6px;font-size:11px;text-align:right;">'
            f'{r.extraction_count if r.extraction_count is not None else "—"}</td>'
            f'<td style="padding:3px 6px;font-size:11px;max-width:300px;">'
            f'<span title="{msg_full}">{msg}{"…" if len(r.error_message or "") > 300 else ""}</span>'
            f'</td>'
            f'</tr>'
        )

    prev_offset = max(0, offset - limit)
    next_offset = offset + limit
    has_prev = offset > 0
    has_next = next_offset < total

    pagination = (
        f'<div style="display:flex;gap:8px;align-items:center;margin-top:6px;font-size:12px;">'
        f'<span style="color:var(--text-muted);">{offset + 1}–{min(offset + limit, total)} of {total}</span>'
    )
    if has_prev:
        pagination += (
            f'<button style="padding:2px 8px;font-size:11px;border:1px solid var(--border);'
            f'border-radius:3px;cursor:pointer;" '
            f'hx-get="/dashboard/api/run/pipeline-events?offset={prev_offset}&limit={limit}&event_type={event_type}" '
            f'hx-target="#pipeline-event-log" hx-swap="innerHTML">← Prev</button>'
        )
    if has_next:
        pagination += (
            f'<button style="padding:2px 8px;font-size:11px;border:1px solid var(--border);'
            f'border-radius:3px;cursor:pointer;" '
            f'hx-get="/dashboard/api/run/pipeline-events?offset={next_offset}&limit={limit}&event_type={event_type}" '
            f'hx-target="#pipeline-event-log" hx-swap="innerHTML">Next →</button>'
        )
    pagination += '</div>'

    if not rows:
        table_html = '<div style="color:var(--text-muted);font-size:13px;padding:8px 0;">No events found.</div>'
    else:
        table_html = (
            '<div style="overflow-x:auto;">'
            '<table style="border-collapse:collapse;width:100%;min-width:550px;">'
            '<thead><tr style="border-bottom:1px solid var(--border);">'
            '<th style="padding:3px 6px;font-size:11px;text-align:left;">Time</th>'
            '<th style="padding:3px 6px;font-size:11px;text-align:left;">Type</th>'
            '<th style="padding:3px 6px;font-size:11px;text-align:left;">Agent</th>'
            '<th style="padding:3px 6px;font-size:11px;text-align:left;">Backend</th>'
            '<th style="padding:3px 6px;font-size:11px;text-align:right;">Dur</th>'
            '<th style="padding:3px 6px;font-size:11px;text-align:right;">Ext</th>'
            '<th style="padding:3px 6px;font-size:11px;text-align:left;">Message</th>'
            '</tr></thead>'
            f'<tbody>{trs}</tbody>'
            '</table>'
            '</div>'
            + pagination
        )

    return HTMLResponse(
        f'<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:6px;">{filter_buttons}</div>'
        + table_html
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
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Run extraction with the active provider (runs in background thread).

    The backend (local LM Studio vs NVIDIA) is selected by the provider
    toggle on the Models page (persisted in config/agent_models.json and
    read by get_extraction_provider). There is no per-call provider override.

    Args:
        limit: Max passages to extract (None = all).
    """
    if _background_jobs.get("extract", {}).get("running"):
        return HTMLResponse(
            '<div class="result-panel info" hx-get="/dashboard/api/job-status/extract" '
            'hx-trigger="every 2s" hx-swap="outerHTML">'
            '<span class="spinner"></span> Extraction already running&hellip;</div>'
        )

    # Precondition: triage must have run and produced relevant passages.
    from src.db.models import SectionTriageResult, TriageDecision
    triaged_count = db.scalar(
        select(func.count()).select_from(SectionTriageResult)
        .where(SectionTriageResult.decision.in_([
            TriageDecision.relevant, TriageDecision.uncertain,
        ]))
    ) or 0
    if triaged_count == 0:
        passage_count = db.scalar(
            select(func.count()).select_from(NormalizedSourceRecord)
        ) or 0
        if passage_count == 0:
            return HTMLResponse(
                '<div class="result-panel warning">'
                '<strong>Nothing to extract.</strong> '
                'No passages have been parsed yet. Run <strong>Seed &amp; Ingest All</strong> '
                '(Step&nbsp;1), then <strong>Triage Passages</strong> (Step&nbsp;2), then extract.'
                '</div>'
            )
        return HTMLResponse(
            '<div class="result-panel warning">'
            '<strong>Triage has not run yet.</strong> '
            f'There are <strong>{passage_count:,}</strong> parsed passages but no triage results. '
            'Run <strong>Triage Passages</strong> (Step&nbsp;2) first — '
            'extraction only processes passages that triage marked as relevant.'
            '</div>'
        )

    def _do_extract(db, limit=None):
        from src.ingestion.extractor import run_extraction
        summary = run_extraction(db, limit=limit)

        # Label the result with whichever backend actually ran.
        from src.core.model_config import get_config
        active = get_config().provider
        label = "via NVIDIA" if active == "nvidia" else "via local LM Studio"

        tokens = summary.get("token_usage", {})
        panel_class = "success"
        cancelled_note = ""
        if summary.get("cancelled"):
            cancelled_note = (
                '<div style="margin-top:6px;font-size:13px;color:var(--warning);">'
                'Extraction was terminated by user. Remaining passages still unprocessed.'
                '</div>'
            )
            panel_class = "warning"
        # Circuit-breaker trip is the single most important "run aborted early"
        # signal — surface it prominently instead of only in the live feed.
        cb_note = ""
        if summary.get("circuit_breaker_tripped"):
            panel_class = "error"
            detail = html_escape(str(summary.get("circuit_breaker_detail", ""))[:400])
            cb_note = (
                '<div style="margin-top:8px;padding:8px 10px;background:rgba(231,76,60,0.08);'
                'border-left:3px solid #e74c3c;font-size:13px;">'
                '<strong>&#9888; Circuit breaker tripped — run stopped early.</strong>'
                f'<div style="margin-top:4px;color:var(--text-muted);font-size:12px;">{detail}</div>'
                '</div>'
            )

        # Pull the most recent agent_error (with its classified type + backend)
        # so the completion panel shows what failed without opening a drill-down.
        last_error_note = ""
        try:
            from sqlalchemy import desc as _desc

            from src.db.models import PipelineEvent as _PE
            row = db.execute(
                select(_PE.agent_name, _PE.error_message, _PE.model_id, _PE.details)
                .where(_PE.event_type == "agent_error")
                .order_by(_desc(_PE.created_at))
                .limit(1)
            ).first()
            if row and row.error_message:
                etype = (row.details or {}).get("error_type", "llm_error")
                backend = ""
                if row.model_id:
                    backend = " on " + ("NVIDIA" if row.model_id.endswith("-nvidia") else "local")
                last_error_note = (
                    '<div style="margin-top:6px;font-size:12px;color:var(--text-muted);">'
                    f'Last error ({html_escape(etype)}{backend}, agent '
                    f'{html_escape(row.agent_name or "?")}): '
                    f'{html_escape(row.error_message[:200])}'
                    '</div>'
                )
        except Exception:
            db.rollback()

        run_folder = summary.get("folder", "")
        run_note = ""
        if run_folder:
            from pathlib import Path
            folder_name = Path(run_folder).name
            run_note = (
                f'<div style="margin-top:6px;font-size:12px;color:var(--text-muted);">'
                f'Run archived to: <code>output/extraction_runs/{html_escape(folder_name)}/</code>'
                f'</div>'
            )
        return (
            f'<div class="result-panel {panel_class}">'
            f'Extracted {summary["total_extractions"]} items from '
            f'{summary["records_processed"]} passages {label}. '
            f'Tokens: {tokens.get("total_tokens", 0):,}'
            f'{cb_note}{cancelled_note}{last_error_note}{run_note}'
            f'</div>'
        )

    _run_in_background("extract", _do_extract, {"limit": limit})
    return HTMLResponse(
        '<div class="result-panel info" hx-get="/dashboard/api/job-status/extract" '
        'hx-trigger="every 2s" hx-swap="outerHTML">'
        '<span class="spinner"></span> Extraction started&hellip; '
        'Watch the extraction monitor below for live progress.</div>'
    )


@router.post("/api/run/retry-failed")
def run_retry_failed_extractions(
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Retry extraction for passages+agents that previously failed.

    Reads from failed_extraction_attempts table and re-runs only the
    specific agents that failed on each passage.
    """
    try:
        from src.ingestion.extractor import run_retry_failed
        summary = run_retry_failed(db)

        if summary["total"] == 0:
            return HTMLResponse(
                '<div class="result-panel success">'
                'No failed extractions to retry.'
                '</div>'
            )

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Retried {summary["retried"]} failed extractions: '
            f'{summary["succeeded"]} succeeded, '
            f'{summary["failed_again"]} failed again.'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Retry error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/retry-failed-triage")
def run_retry_failed_triage_endpoint(
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Delete triage_error rows and re-run triage for those passages."""
    try:
        from src.ingestion.extractor import run_retry_failed_triage
        summary = run_retry_failed_triage(db)

        if summary["cleared"] == 0:
            return HTMLResponse(
                '<div class="result-panel success">No failed triage rows to retry.</div>'
            )

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Cleared {summary["cleared"]} failed triage rows and re-ran triage: '
            f'{summary["relevant"]} relevant, '
            f'{summary["uncertain"]} uncertain, '
            f'{summary["skipped"]} skipped '
            f'({summary["total"]} passages processed).'
            f'</div>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Re-triage error: {html_escape(str(e))}</div>'
        )


@router.get("/api/failed-triage-count")
def get_failed_triage_count(db: Session = Depends(get_db)) -> HTMLResponse:
    """Return count of triage_error rows with a Re-triage Failed button."""
    try:
        from src.db.models import SectionTriageResult
        count = db.scalar(
            select(func.count()).select_from(SectionTriageResult)
            .where(
                SectionTriageResult.method == "passthrough",
                SectionTriageResult.quality_flags.contains(["llm_error"]),
            )
        ) or 0
        if count > 0:
            return HTMLResponse(
                f'<span class="badge warning">{count} triage errors</span> '
                f'<button class="btn btn-sm btn-warning" '
                f'hx-post="/dashboard/api/run/retry-failed-triage" '
                f'hx-target="#retriage-result" '
                f'hx-swap="innerHTML" '
                f'hx-indicator="#retriage-spinner">'
                f'Re-triage Failed</button>'
                f'<span id="retriage-spinner" class="htmx-indicator"> ...</span>'
                f'<span id="retriage-result"></span>'
            )
        return HTMLResponse('<span class="badge success">No triage errors</span>')
    except Exception:
        return HTMLResponse("")


@router.get("/api/failed-extractions-count")
def get_failed_extractions_count(db: Session = Depends(get_db)) -> HTMLResponse:
    """Return count of un-retried failed extraction attempts."""
    try:
        from src.db.models import FailedExtractionAttempt
        count = db.scalar(
            select(func.count()).select_from(FailedExtractionAttempt)
            .where(FailedExtractionAttempt.retried == False)  # noqa: E712
        ) or 0
        if count > 0:
            return HTMLResponse(
                f'<span class="badge warning">{count} failed</span> '
                f'<button class="btn btn-sm btn-warning" '
                f'hx-post="/dashboard/api/run/retry-failed" '
                f'hx-target="#retry-result" '
                f'hx-swap="innerHTML">Retry Failed</button> '
                f'<a class="btn btn-sm" href="/dashboard/api/failed-extractions/export.csv" '
                f'style="margin-left:4px;">&#11015; Download CSV</a> '
                f'<span id="retry-result"></span>'
            )
        return HTMLResponse('<span class="badge success">No failures</span>')
    except Exception:
        return HTMLResponse("")


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


@router.post("/api/run/sync-to-supabase")
def run_sync_to_supabase(
    dry_run: bool = False,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Sync pipeline data from local Docker Postgres to Regs Checker Supabase."""
    import os

    source_url = os.environ.get("REGS_DATABASE_URL")
    supabase_url = (
        os.environ.get("REGS_SUPABASE_URL")
        or os.environ.get("REGS_SUPABASE_PROJECT_URL")
    )
    supabase_key = (
        os.environ.get("REGS_SUPABASE_KEY")
        or os.environ.get("REGS_SUPABASE_ANON_KEY")
    )

    # Diagnostic: detect common misconfigurations
    if not source_url:
        return HTMLResponse(
            '<div class="result-panel warning">'
            'Sync skipped: <code>REGS_DATABASE_URL</code> not set in .env.'
            '</div>'
        )

    missing = []
    diagnostics = []
    if not supabase_url:
        missing.append("REGS_SUPABASE_URL")
    elif supabase_url.startswith("postgresql://"):
        diagnostics.append(
            f'<code>REGS_SUPABASE_URL</code> looks like a Postgres connection string '
            f'(<code>{html_escape(supabase_url[:50])}...</code>). '
            f'The sync uses the <strong>REST API</strong> and needs the HTTP URL, e.g. '
            f'<code>https://your-project.supabase.co</code>'
        )
    if not supabase_key:
        missing.append("REGS_SUPABASE_KEY")
    elif not supabase_key.startswith("eyJ"):
        diagnostics.append(
            '<code>REGS_SUPABASE_KEY</code> does not look like a JWT token '
            '(should start with <code>eyJ</code>). Get it from Supabase Dashboard &rarr; '
            'Settings &rarr; API &rarr; <code>service_role</code> key.'
        )

    if missing or diagnostics:
        parts = []
        if missing:
            parts.append(f'Missing env vars: {", ".join(f"<code>{m}</code>" for m in missing)}')
        parts.extend(diagnostics)
        return HTMLResponse(
            '<div class="result-panel warning">'
            '<strong>Sync config issue:</strong><br>'
            + '<br>'.join(parts)
            + '</div>'
        )

    try:
        from src.scripts.sync_to_supabase import sync_tables
        summary = sync_tables(
            source_url=source_url,
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            dry_run=dry_run,
        )

        total = sum(v.get("synced", v.get("source_count", 0)) for v in summary.values() if isinstance(v, dict))
        tables = len([v for v in summary.values() if isinstance(v, dict)])

        if dry_run:
            return HTMLResponse(
                f'<div class="result-panel warning">'
                f'<strong>Dry Run Preview</strong><br>'
                f'{tables} tables, ~{total} rows would be synced to Regs Checker Supabase.'
                f'</div>'
            )

        return HTMLResponse(
            f'<div class="result-panel success">'
            f'Synced {tables} tables ({total} rows) to Regs Checker Supabase.'
            f'</div>'
        )
    except Exception as e:
        return HTMLResponse(
            f'<div class="result-panel error">Sync error: {html_escape(str(e))}</div>'
        )


@router.post("/api/run/sync")
def run_sync(
    dry_run: bool = False,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Sync extractions from Regs Checker Supabase to Policy Navigator."""
    import os
    source_url = (
        os.environ.get("REGS_SUPABASE_URL")
        or os.environ.get("REGS_SUPABASE_PROJECT_URL")
    )
    target_url = os.environ.get("REGS_POLICY_NAVIGATOR_URL")

    missing = []
    if not source_url:
        missing.append("REGS_SUPABASE_URL")
    if not target_url:
        missing.append("REGS_POLICY_NAVIGATOR_URL")
    if missing:
        return HTMLResponse(
            '<div class="result-panel warning">'
            f'Sync skipped: missing {", ".join(f"<code>{m}</code>" for m in missing)} in .env.'
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

    source_url = (
        os.environ.get("REGS_SUPABASE_URL")
        or os.environ.get("REGS_SUPABASE_PROJECT_URL")
    )
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

    source_url = (
        os.environ.get("REGS_SUPABASE_URL")
        or os.environ.get("REGS_SUPABASE_PROJECT_URL")
    )
    target_url = os.environ.get("REGS_POLICY_NAVIGATOR_URL")

    if not source_url or not target_url:
        return HTMLResponse(
            '<div class="result-panel warning">Database URLs not configured.</div>'
        )

    try:
        from src.core.bridge_monitor import (
            detect_unbridged_families,
        )
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
        changes.append("title updated")
        family.canonical_title = new_title

    # Short cite
    new_cite = form.get("short_cite", "").strip()
    if new_cite and new_cite != family.short_cite:
        changes.append(f"short_cite: {family.short_cite} → {new_cite}")
        family.short_cite = new_cite

    # Subject area
    new_subject = form.get("subject_area", "").strip()
    if new_subject and new_subject != family.subject_area:
        changes.append("subject_area updated")
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
        changes.append("fetch_url updated")
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
            # Store reference (no S3 needed for uploaded files)
            s3_key = f"upload://{sha256}"

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

        # Parse and chunk (pass bytes directly, skip S3)
        job.status = IngestionStatus.parsing
        job.parse_started_at = datetime.utcnow()
        db.commit()

        records = parse_and_normalize(db, job, artifact, content_bytes=content_bytes)

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
            from src.ingestion._archived.iapp_pdf_tracker import IAPP_PDF_PATH, parse_iapp_pdf
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
        job.fetch_url = value
        # Reset failed jobs so they can retry with the new URL
        if job.status in (IngestionStatus.failed, IngestionStatus.requires_manual_review):
            job.status = IngestionStatus.pending
            job.error_message = None
        db.commit()
        return HTMLResponse(
            '<span style="color:var(--success);font-size:12px;">'
            'URL updated. Job reset to pending.</span>'
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
    from sqlalchemy import and_, or_

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
        bill_id = ""
        iapp_bill_number = ""
        iapp_status = ""
        leg_status = ""
        if dv and dv.family:
            source = dv.family.source
            jurisdiction = source.jurisdiction_code if source else ""
            short_cite = dv.family.short_cite or ""
            title = dv.family.canonical_title or ""
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
    from sqlalchemy import and_, or_

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


# ===================================================================
# COMPLIANCE CONCEPTS — Phase 5 grouping + review UI
# ===================================================================


@router.get("/concepts", response_class=HTMLResponse)
def concepts_page(request: Request, db: Session = Depends(get_db)):
    """Compliance concept overview page."""
    from src.core.concept_review import concept_review_counts

    try:
        counts = concept_review_counts(db)
    except Exception:
        db.rollback()
        counts = {
            "total": 0, "pending": 0, "flagged": 0, "approved": 0,
            "rejected": 0, "tracker_conflict": 0, "ungrounded": 0,
        }
    return _render(request, "concepts.html", {"counts": counts})


@router.post("/api/concepts/group", response_class=HTMLResponse)
def group_concepts_endpoint(
    document_version_id: int | None = Query(default=None),
) -> HTMLResponse:
    """Run concept grouping in background thread."""
    step_key = "concept_group"
    if _background_jobs.get(step_key, {}).get("running"):
        return HTMLResponse(
            f'<div class="result-panel info" hx-get="/dashboard/api/job-status/{step_key}" '
            f'hx-trigger="every 2s" hx-swap="outerHTML">'
            f'<span class="spinner"></span> Concept grouping already running&hellip;</div>'
        )

    def _do_group(db):
        from src.core.concept_grouping import run_concept_grouping

        results = run_concept_grouping(
            db,
            document_version_id=document_version_id,
        )
        db.commit()

        total_created = sum(r.concepts_created for r in results)
        total_flagged = sum(r.concepts_flagged for r in results)
        laws = len(results)

        if total_created == 0 and laws == 0:
            return '<div class="result-panel info">No extractions available to group into concepts.</div>'

        return (
            f'<div class="result-panel success">'
            f'Grouped <strong>{total_created}</strong> concepts across <strong>{laws}</strong> laws. '
            f'<strong>{total_flagged}</strong> flagged for review. '
            f'<a href="/dashboard/concepts">View concepts &rarr;</a>'
            f'</div>'
        )

    _run_in_background(step_key, _do_group)
    return HTMLResponse(
        f'<div class="result-panel info" hx-get="/dashboard/api/job-status/{step_key}" '
        f'hx-trigger="every 2s" hx-swap="outerHTML">'
        f'<span class="spinner"></span> Concept grouping started&hellip;</div>'
    )


@router.get("/api/concepts/list", response_class=HTMLResponse)
def list_concepts(
    concept_type: str | None = Query(default=None),
    grounding_status: str | None = Query(default=None),
    review_status: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Return filterable concepts table as HTML fragment."""
    try:
        stmt = (
            select(
                ComplianceConcept,
                DocumentFamily.short_cite,
                Source.jurisdiction_code,
            )
            .join(DocumentVersion, ComplianceConcept.document_version_id == DocumentVersion.id)
            .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
            .join(Source, DocumentFamily.source_id == Source.id)
            .order_by(ComplianceConcept.confidence_score.asc().nullslast(), ComplianceConcept.id)
            .limit(500)
        )
        if concept_type:
            stmt = stmt.where(ComplianceConcept.concept_type == concept_type)
        if grounding_status:
            stmt = stmt.where(ComplianceConcept.grounding_status == grounding_status)
        if review_status:
            stmt = stmt.where(
                ComplianceConcept.review_status == ConceptReviewStatus(review_status)
            )

        rows = db.execute(stmt).all()
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error loading concepts: {html_escape(str(e))}</div>'
        )

    if not rows:
        return HTMLResponse(
            '<div class="result-panel info">No concepts found. Run "Group Concepts" first.</div>'
        )

    grounding_colors = {
        "tracker_grounded": "var(--success)",
        "iapp_grounded": "var(--info)",
        "tracker_conflict": "var(--danger)",
        "ungrounded": "var(--warning)",
    }
    tier_colors = {"A": "var(--success)", "B": "var(--info)", "C": "var(--warning)", "D": "var(--danger)"}

    table_rows = ""
    for concept, short_cite, jur_code in rows:
        rs = concept.review_status.value if hasattr(concept.review_status, "value") else str(concept.review_status)
        gs = concept.grounding_status or "ungrounded"
        gs_color = grounding_colors.get(gs, "var(--text-muted)")
        tier = concept.confidence_tier or "—"
        tier_color = tier_colors.get(tier, "var(--text-muted)")
        score = f"{concept.confidence_score:.2f}" if concept.confidence_score is not None else "—"
        ctype = html_escape(concept.concept_type or "—")
        actor = html_escape(concept.regulated_actor_family or concept.right_holder_family or "—")
        title = html_escape((concept.title or "")[:80])
        cite = html_escape(short_cite or "")
        jur = html_escape(jur_code or "")

        table_rows += (
            f'<tr>'
            f'<td><strong>{jur}</strong> {cite}</td>'
            f'<td style="font-size:11px;">{ctype}</td>'
            f'<td>{actor}</td>'
            f'<td title="{title}" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{title}</td>'
            f'<td style="text-align:center;color:{tier_color};font-weight:bold;">{tier}</td>'
            f'<td style="text-align:right;">{score}</td>'
            f'<td style="text-align:center;">{concept.member_count}</td>'
            f'<td style="color:{gs_color};font-size:11px;">{gs}</td>'
            f'<td style="font-size:11px;">{rs}</td>'
            f'</tr>'
        )

    return HTMLResponse(
        f'<div style="margin-top:8px;">'
        f'<div class="table-wrap">'
        f'<table class="review-table">'
        f'<thead><tr>'
        f'<th>Law</th><th>Type</th><th>Actor</th><th>Title</th>'
        f'<th style="text-align:center;">Tier</th>'
        f'<th style="text-align:right;">Score</th>'
        f'<th style="text-align:center;">Members</th>'
        f'<th>Grounding</th><th>Review</th>'
        f'</tr></thead>'
        f'<tbody>{table_rows}</tbody>'
        f'</table></div>'
        f'<div style="font-size:12px;color:var(--text-muted);margin-top:4px;">'
        f'Showing {len(rows)} concepts (capped at 500). '
        f'Use filters to narrow results.'
        f'</div></div>'
    )


@router.get("/api/concepts/review-queue", response_class=HTMLResponse)
def concept_review_queue_fragment(
    jurisdiction: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Return priority-banded concept review queue as HTML."""
    from src.core.concept_review import get_concept_review_queue

    try:
        items = get_concept_review_queue(db, limit=100, jurisdiction=jurisdiction or None)
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<div class="result-panel error">Error loading queue: {html_escape(str(e))}</div>'
        )

    if not items:
        return HTMLResponse(
            '<div class="result-panel success" style="margin-top:8px;">'
            'No concepts need review — queue is empty.'
            '</div>'
        )

    band_labels = {
        0: ("Tracker Conflict", "var(--danger)"),
        1: ("Flagged — Tier D", "var(--danger)"),
        2: ("Flagged", "var(--warning)"),
        3: ("Ungrounded", "var(--warning)"),
        4: ("Clean", "var(--success)"),
    }

    # Group by priority band
    from collections import defaultdict
    by_band: dict[int, list] = defaultdict(list)
    for item in items:
        by_band[item.priority_band].append(item)

    html_parts = ['<div style="margin-top:8px;">']
    for band in sorted(by_band.keys()):
        band_items = by_band[band]
        label, color = band_labels.get(band, (f"Band {band}", "var(--text)"))
        html_parts.append(
            f'<div style="margin-bottom:12px;">'
            f'<div style="font-weight:600;color:{color};font-size:13px;margin-bottom:6px;">'
            f'{label} ({len(band_items)})</div>'
        )
        for item in band_items:
            rs_val = item.review_status if isinstance(item.review_status, str) else item.review_status.value
            html_parts.append(
                f'<div style="display:flex;align-items:baseline;gap:8px;padding:6px 8px;'
                f'border:1px solid var(--border);border-radius:4px;margin-bottom:4px;'
                f'font-size:12px;background:var(--bg-secondary);">'
                f'<strong style="min-width:24px;color:{color};">{item.priority_band}</strong>'
                f'<span style="min-width:40px;color:var(--text-muted);">{item.jurisdiction or "—"}</span>'
                f'<span style="min-width:120px;font-size:11px;">{html_escape(item.concept_type)}</span>'
                f'<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" '
                f'title="{html_escape(item.title)}">{html_escape(item.title[:60])}</span>'
                f'<span style="min-width:32px;text-align:center;font-weight:bold;">'
                f'{item.confidence_tier or "—"}</span>'
                f'<span style="min-width:36px;text-align:right;color:var(--text-muted);">'
                f'{f"{item.confidence_score:.2f}" if item.confidence_score is not None else "—"}</span>'
                f'<form style="display:inline-flex;gap:4px;" '
                f'hx-post="/dashboard/api/concepts/resolve/{item.concept_id}" '
                f'hx-target="closest div[data-concept-row]" hx-swap="outerHTML" '
                f'hx-include="this">'
                f'<select name="status" style="font-size:11px;padding:2px 4px;">'
                f'<option value="approved">Approve</option>'
                f'<option value="rejected">Reject</option>'
                f'<option value="flagged" {"selected" if rs_val == "flagged" else ""}>Flag</option>'
                f'</select>'
                f'<button type="submit" class="btn btn-sm btn-primary" style="font-size:11px;">Save</button>'
                f'</form>'
                f'</div>'
            )
        html_parts.append('</div>')

    html_parts.append('</div>')
    return HTMLResponse("".join(html_parts))


@router.post("/api/concepts/resolve/{concept_id}", response_class=HTMLResponse)
def resolve_concept_action(
    concept_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Record analyst review decision on a concept."""
    from src.core.concept_review import resolve_concept

    try:
        status_enum = ConceptReviewStatus(status)
    except ValueError:
        return HTMLResponse(
            f'<div class="result-panel error">Invalid status: {html_escape(status)}</div>'
        )

    found = resolve_concept(db, concept_id, status_enum)
    db.commit()

    if not found:
        return HTMLResponse(
            f'<div class="result-panel error">Concept {concept_id} not found.</div>'
        )
    return HTMLResponse(
        f'<div style="padding:4px 8px;font-size:12px;color:var(--success);">'
        f'Concept {concept_id} marked <strong>{status}</strong>.'
        f'</div>'
    )


# ===================================================================
# MODEL CONFIGURATION — agent ↔ model assignment UI
# ===================================================================

@router.get("/models", response_class=HTMLResponse)
def models_page(request: Request):
    """Full-page model configuration UI.

    Settings are edited per-backend: the page shows the *active* provider's
    config block.  Model choices come from that backend's catalog — LM Studio's
    loaded models for "local", the NVIDIA catalog (lazy-loaded) for "nvidia".
    """
    from src.core.model_config import AGENT_DISPLAY, fetch_available_models, get_config

    cfg = get_config()
    active = cfg.provider

    # Only probe LM Studio when it's the active backend — avoids a 3s stall
    # on the NVIDIA path.  NVIDIA model ids are typed (with catalog datalist).
    if active == "local":
        available = fetch_available_models()
        model_ids = sorted({m["id"] for m in available}) if available else []
        lm_connected = len(available) > 0
    else:
        model_ids = []
        lm_connected = False

    active_agents = cfg.agents_for(active)
    agents_data = []
    for name, display in AGENT_DISPLAY.items():
        acfg = active_agents.get(name) or cfg.get(name)
        agents_data.append({
            "name": name,
            "label": display["label"],
            "description": display["description"],
            "model": acfg.model,
            "max_tokens": acfg.max_tokens,
            "context_length": acfg.context_length,
            "temperature": acfg.temperature,
            "reasoning_effort": acfg.reasoning_effort,
            "top_p": acfg.top_p,
        })

    from src.core.config import settings as _settings

    return _render(request, "models.html", {
        "agents": agents_data,
        "available_models": model_ids,
        "lm_studio_connected": lm_connected,
        "current_provider": active,
        "provider_kind": active,
        "nvidia_configured": bool(_settings.nvidia_api_key),
        "nvidia_model": _settings.nvidia_extraction_model,
    })


@router.get("/api/models/available")
def get_available_models():
    """HTMX endpoint: refresh available models from LM Studio."""
    from src.core.model_config import fetch_available_models

    available = fetch_available_models()
    model_ids = sorted({m["id"] for m in available}) if available else []

    if not model_ids:
        return HTMLResponse(
            '<div class="alert alert-warning">'
            "Could not reach LM Studio. Check that it's running on port 1234."
            "</div>"
        )

    options_html = "".join(
        f'<option value="{html_escape(mid)}">{html_escape(mid)}</option>'
        for mid in model_ids
    )
    return HTMLResponse(
        f'<div class="alert alert-success">'
        f"Connected &mdash; {len(model_ids)} model(s) available"
        f"</div>"
        f'<template id="model-options">{options_html}</template>'
        f"<script>"
        f"document.querySelectorAll('select.model-select').forEach(function(sel) {{"
        f"  var cur = sel.value;"
        f"  var tpl = document.getElementById('model-options');"
        f"  sel.innerHTML = '<option value=\"\">(default)</option>' + tpl.innerHTML;"
        f"  if (cur) {{"
        f"    var match = sel.querySelector('option[value=\"' + cur + '\"]');"
        f"    if (match) {{ match.selected = true; }}"
        f"    else {{"
        f"      var opt = document.createElement('option');"
        f"      opt.value = cur; opt.text = cur + ' (not loaded)'; opt.selected = true;"
        f"      sel.appendChild(opt);"
        f"    }}"
        f"  }}"
        f"}});"
        f"</script>"
    )


@router.post("/api/models/save")
async def save_model_config(request: Request):
    """Save agent model assignments for the *active* backend only.

    The other backend's settings are preserved untouched, so LM Studio and
    NVIDIA configurations stay independent.
    """
    from src.core.model_config import (
        AGENT_DISPLAY,
        AgentModelConfig,
        get_config,
        save_config,
    )
    from src.ingestion.extractor import reload_agents

    form = await request.form()
    store = get_config()
    active = store.provider

    agents = {}
    for name in AGENT_DISPLAY:
        model = (form.get(f"{name}_model", "") or "").strip()
        max_tokens = int(form.get(f"{name}_max_tokens", "65536") or "65536")
        context_length = int(form.get(f"{name}_context_length", "131072") or "131072")
        temperature = float(form.get(f"{name}_temperature", "0.0") or "0.0")
        reasoning_effort_raw = (form.get(f"{name}_reasoning_effort") or "").strip() or None
        top_p_raw = (form.get(f"{name}_top_p") or "").strip()
        top_p = float(top_p_raw) if top_p_raw else None
        agents[name] = AgentModelConfig(
            model=model,
            max_tokens=max_tokens,
            context_length=context_length,
            temperature=temperature,
            reasoning_effort=reasoning_effort_raw,
            top_p=top_p,
        )

    store.set_agents(active, agents)
    save_config(store)
    reload_agents()

    return HTMLResponse(
        '<div class="alert alert-success" id="save-result">'
        f"Saved <strong>{html_escape(active)}</strong> configuration. "
        "Agents will use it on the next extraction run."
        "</div>"
    )


@router.post("/api/models/reset")
def reset_model_config(request: Request):
    """Reset the *active* backend's agents to defaults (other backend untouched)."""
    from src.core.model_config import ModelConfigStore, get_config, save_config
    from src.ingestion.extractor import reload_agents

    store = get_config()
    active = store.provider
    defaults = ModelConfigStore.defaults()
    store.set_agents(active, defaults.agents_for(active))
    save_config(store)
    reload_agents()

    return HTMLResponse(
        '<div class="alert alert-success">'
        f"Reset <strong>{html_escape(active)}</strong> agents to defaults. "
        "Reload this page to see updated values."
        "</div>"
    )


@router.post("/api/models/set-provider")
async def set_extraction_provider(request: Request):
    """Switch the extraction backend between local LM Studio and the NVIDIA API.

    Persists the choice to config/agent_models.json, clears the provider cache,
    and reloads agents so the next extraction run uses the new backend.
    No server restart required.
    """
    from src.core.config import settings
    from src.core.llm_provider import clear_provider_cache
    from src.core.model_config import get_config, save_config
    from src.ingestion.extractor import is_paused, reload_agents

    form = await request.form()
    provider = (form.get("provider") or "").strip().lower()

    if provider not in ("local", "nvidia"):
        return HTMLResponse(
            '<div class="alert alert-danger">'
            f'Invalid provider "{html_escape(provider)}". Use "local" or "nvidia".'
            "</div>",
            status_code=400,
        )

    # Guard: don't switch to NVIDIA if the key isn't configured.
    if provider == "nvidia" and not settings.nvidia_api_key:
        return HTMLResponse(
            '<div class="alert alert-danger">'
            "NVIDIA_API_KEY is not set. Add it to your .env and restart the "
            "server before switching to the NVIDIA provider."
            "</div>",
            status_code=400,
        )

    store = get_config()
    store.provider = provider
    save_config(store)

    # Rebuild provider + agent instances so the change takes effect immediately.
    clear_provider_cache()
    reload_agents()

    if provider == "nvidia":
        label = f"NVIDIA API &mdash; <code>{html_escape(settings.nvidia_extraction_model)}</code>"
        note = (
            "Calls now route to integrate.api.nvidia.com. Verify your credits "
            "and rate limits before a full run."
        )
    else:
        label = f"Local LM Studio &mdash; <code>{html_escape(settings.local_extraction_model)}</code>"
        note = "Calls now route to your local LM Studio server."

    paused_note = (
        " Extraction is currently paused; the new provider applies when you resume."
        if is_paused()
        else ""
    )

    return HTMLResponse(
        '<div class="alert alert-success">'
        f"Extraction provider set to <strong>{label}</strong>. {note}{paused_note}"
        "</div>"
    )


@router.get("/api/models/status")
def get_models_status() -> HTMLResponse:
    """Compact LM Studio connection + loaded-model status for polling.

    Polled every 5 s from both the Models page header and the pipeline page
    Extract step.  Returns a self-contained HTML snippet (no outer wrapper
    needed) suitable for hx-swap="innerHTML".
    """
    from src.core.config import settings
    from src.core.model_config import fetch_available_models, get_config

    available = fetch_available_models(timeout=2.0)
    model_ids = sorted({m["id"] for m in available}) if available else []
    connected = len(model_ids) > 0

    cfg = get_config()
    # Collect unique models currently configured across extraction agents
    configured = sorted({
        cfg.get(name).model
        for name in ("obligation", "rights_protection", "definition_actor",
                     "threshold_exception", "compliance_mechanism", "preemption")
        if cfg.get(name).model
    })

    if connected:
        dot = '<span style="color:#27ae60;font-size:16px;" title="LM Studio connected">●</span>'
        model_list = " &nbsp;·&nbsp; ".join(
            f'<span style="font-family:monospace;font-size:12px;">{html_escape(m)}</span>'
            for m in model_ids
        )
        body = (
            f'{dot} <strong>LM Studio connected</strong> &mdash; '
            f'{len(model_ids)} model{"s" if len(model_ids) != 1 else ""} loaded: {model_list}'
        )
        if configured:
            cfg_str = " &nbsp;·&nbsp; ".join(
                f'<span style="font-family:monospace;font-size:12px;">{html_escape(m)}</span>'
                for m in configured
            )
            body += f'<br><span style="font-size:12px;color:var(--text-muted);">Configured for extraction: {cfg_str}</span>'
        color = "var(--success-bg, #ecfdf5)"
        border = "var(--success, #27ae60)"
    else:
        dot = '<span style="color:#e74c3c;font-size:16px;" title="LM Studio unreachable">●</span>'
        body = f'{dot} <strong>LM Studio not reachable</strong> &mdash; check it is running on <code>{html_escape(settings.local_llm_url)}</code>'
        color = "var(--warning-bg, #fffbeb)"
        border = "var(--warning, #f39c12)"

    # Also inject options into any .model-select dropdowns on the page
    options_js = ""
    if model_ids:
        options_html = "".join(
            f'<option value="{html_escape(mid)}">{html_escape(mid)}</option>'
            for mid in model_ids
        )
        # language=JavaScript
        options_js = (
            "<script>"
            "document.querySelectorAll('select.model-select, select.apply-all-select').forEach(function(sel){"
            "  var cur=sel.value;"
            "  if(!sel.dataset.populated){"
            "    sel.innerHTML='<option value=\"\">(default)</option>"
            + options_html.replace("'", "\\'")
            + "';"
            "    sel.dataset.populated='1';"
            "  }"
            "  if(cur){var m=sel.querySelector('option[value=\"'+cur+'\"]');if(m)m.selected=true;}"
            "});"
            "</script>"
        )

    return HTMLResponse(
        f'<div style="padding:8px 12px;background:{color};border:1px solid {border};'
        f'border-radius:var(--radius-sm,4px);font-size:13px;">{body}</div>'
        + options_js
    )


@router.post("/api/models/apply-all")
async def apply_model_to_all(request: Request) -> HTMLResponse:
    """Set all extraction agents (not triage) to the selected model and save.

    Accepts form param ``model`` — the model ID string.  Triage agent is
    intentionally excluded because it uses a smaller/faster model by design.
    """
    from src.core.model_config import (
        AGENT_DISPLAY,
        AgentModelConfig,
        get_config,
        save_config,
    )
    from src.ingestion.extractor import reload_agents

    form = await request.form()
    model = (form.get("model") or "").strip()

    if not model:
        return HTMLResponse(
            '<div class="alert alert-warning">No model selected.</div>'
        )

    store = get_config()
    active = store.provider
    existing_agents = store.agents_for(active)
    updated = {}
    for name in AGENT_DISPLAY:
        existing = existing_agents.get(name) or store.get(name)
        if name == "triage":
            # Keep triage as-is — it uses a smaller/faster model by design.
            updated[name] = existing
        else:
            updated[name] = AgentModelConfig(
                model=model,
                max_tokens=existing.max_tokens,
                context_length=existing.context_length,
                temperature=existing.temperature,
                reasoning_effort=existing.reasoning_effort,
            )

    store.set_agents(active, updated)
    save_config(store)
    reload_agents()

    return HTMLResponse(
        f'<div class="alert alert-success">'
        f'All extraction agents set to <code>{html_escape(model)}</code> '
        f'for the <strong>{html_escape(active)}</strong> backend. '
        f'Reload the page to see updated values.'
        f'</div>'
    )


@router.get("/api/models/nvidia-available")
def get_nvidia_models() -> HTMLResponse:
    """HTMX endpoint: fetch the NVIDIA catalog and populate model datalists.

    Separate from the LM Studio check so each backend is verified on its own.
    """
    import json as _json

    from src.core.config import settings
    from src.core.model_config import fetch_nvidia_models

    if not settings.nvidia_api_key:
        return HTMLResponse(
            '<div class="alert alert-warning">'
            "NVIDIA_API_KEY is not set. Add it to <code>.env</code> and restart."
            "</div>"
        )

    models = fetch_nvidia_models()
    if not models:
        return HTMLResponse(
            '<div class="alert alert-warning">'
            "Could not reach the NVIDIA catalog. Check your key, credits, and "
            "network access to <code>integrate.api.nvidia.com</code>."
            "</div>"
        )

    options_html = "".join(
        f'<option value="{html_escape(m)}"></option>' for m in models
    )
    return HTMLResponse(
        f'<div class="alert alert-success">'
        f"NVIDIA catalog reachable &mdash; {len(models)} models available. "
        f"Type to search in any model field below."
        f"</div>"
        f"<script>"
        f'var dl=document.getElementById("nvidia-model-list");'
        f"if(dl){{dl.innerHTML={_json.dumps(options_html)};}}"
        f"</script>"
    )
