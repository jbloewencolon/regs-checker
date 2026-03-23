"""Shared helpers for dashboard route modules.

Extracted from dashboard.py so that split sub-modules can import common
utilities without circular dependencies.  This file does NOT define a router.
"""

from __future__ import annotations

import csv
import threading
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    ApplicabilityCondition,
    DocumentVersion,
    Extraction,
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
    ObligationDependency,
    ReviewQueueItem,
    ReviewStatus,
    SectionTriageResult,
    TriageDecision,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXPORT_DIR = Path("export")

TRACKER_CSV = Path("static/ai_law_tracker.csv")
TRACKER_FIELDS = [
    "State/Terr", "AI Scope", "Relevant Law", "Bill ID",
    "Effective Date", "Key Requirements", "Enforcements Penalties",
    "Status", "Source URL",
]

# ---------------------------------------------------------------------------
# Pipeline lock — prevents concurrent pipeline operations from rapid clicks.
# Only one pipeline operation (pdf discovery, status check, extraction, sync)
# can run at a time. The lock is non-blocking: if busy, we return immediately.
# ---------------------------------------------------------------------------

_pipeline_lock = threading.Lock()


def _acquire_pipeline_lock() -> bool:
    """Try to acquire the pipeline lock (non-blocking).

    Returns True if acquired, False if another operation is running.
    Caller MUST release with _pipeline_lock.release() when done.
    """
    return _pipeline_lock.acquire(blocking=False)


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
# Tracker CSV helpers (needed by _get_pipeline_stats)
# ---------------------------------------------------------------------------

def _read_tracker() -> list[dict]:
    """Read the law tracker CSV and return rows as dicts."""
    if not TRACKER_CSV.exists():
        return []
    with open(TRACKER_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Dashboard statistics helpers
# ---------------------------------------------------------------------------

def _get_pipeline_stats(db: Session) -> dict:
    """Gather pipeline statistics for the dashboard."""
    pending_ingestion = db.scalar(
        select(func.count()).where(IngestionJob.status == IngestionStatus.pending)
    ) or 0

    total_passages_raw = db.scalar(
        select(func.count()).select_from(NormalizedSourceRecord)
    ) or 0

    total_extractions = db.scalar(
        select(func.count()).select_from(Extraction)
    ) or 0

    # Compute triage-aware passage counts.
    # Only relevant + uncertain passages are candidates for extraction.
    triage_count = 0
    triage_relevant = 0
    triage_skipped = 0
    try:
        triage_count = db.scalar(
            select(func.count()).select_from(SectionTriageResult)
        ) or 0
        if triage_count > 0:
            triage_relevant = db.scalar(
                select(func.count()).where(
                    SectionTriageResult.decision.in_([
                        TriageDecision.relevant,
                        TriageDecision.uncertain,
                    ])
                )
            ) or 0
            triage_skipped = triage_count - triage_relevant
    except Exception:
        db.rollback()
        triage_count = 0

    # total_passages = passages eligible for extraction.
    # If triage has run, only count passages that passed triage (relevant/uncertain)
    # plus any that haven't been triaged yet.  Do NOT count triage-skipped passages.
    untriaged = max(total_passages_raw - triage_count, 0)
    if triage_count > 0:
        total_passages = triage_relevant + untriaged
    else:
        total_passages = total_passages_raw

    # Passages that have at least one extraction
    extracted_ids = select(Extraction.source_record_id).distinct()
    passages_with_extractions = db.scalar(
        select(func.count(Extraction.source_record_id.distinct()))
    ) or 0

    # Unprocessed = eligible for extraction but no extractions yet
    if triage_count > 0:
        relevant_ids = (
            select(SectionTriageResult.source_record_id)
            .where(SectionTriageResult.decision.in_([
                TriageDecision.relevant,
                TriageDecision.uncertain,
            ]))
        )
        unprocessed_passages = db.scalar(
            select(func.count()).where(
                NormalizedSourceRecord.id.in_(relevant_ids),
                NormalizedSourceRecord.id.notin_(extracted_ids),
            )
        ) or 0
    else:
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

    # Dependency graph stats
    try:
        dependency_edges = db.scalar(
            select(func.count()).select_from(ObligationDependency)
        ) or 0
    except Exception:
        dependency_edges = 0
        db.rollback()

    # Applicability condition stats
    try:
        condition_nodes = db.scalar(
            select(func.count()).select_from(ApplicabilityCondition)
        ) or 0
    except Exception:
        condition_nodes = 0
        db.rollback()

    # Pending result files
    pending_results = len(list(EXPORT_DIR.glob("batch_*_results.json"))) if EXPORT_DIR.exists() else 0

    # Tracker CSV row count for the collapsible header
    tracker_count = len(_read_tracker())

    return {
        "tracker_count": tracker_count,
        "pending_ingestion": pending_ingestion,
        "total_passages": total_passages,
        "total_passages_raw": total_passages_raw,
        "unprocessed_passages": unprocessed_passages,
        "passages_with_extractions": passages_with_extractions,
        "triage_skipped": triage_skipped,
        "untriaged": untriaged,
        "total_extractions": total_extractions,
        "approved_extractions": approved_extractions,
        "pending_review": pending_review,
        "review_by_tier": review_by_tier,
        "pending_results": pending_results,
        "dependency_edges": dependency_edges,
        "condition_nodes": condition_nodes,
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
