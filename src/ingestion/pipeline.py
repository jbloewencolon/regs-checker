"""Ingestion pipeline — fetch, store, parse, and chunk pending documents.

Shared logic used by both:
  - Dagster ingested_documents asset
  - CLI: python -m src.scripts.seed_pipeline --mode fetch

Steps per pending IngestionJob:
  1. Fetch document at fetch_url (PDF or HTML from legislature sites)
  2. Store raw bytes in MinIO (raw-artifacts bucket), content-addressed by SHA-256
  3. Parse text out of PDF/HTML
  4. Chunk into normalized_source_records (passage-level)
  5. Update ingestion_job status to completed (or failed with error)
"""

from __future__ import annotations

from datetime import datetime

import structlog

from src.db.models import (
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
)
from src.ingestion.connector import fetch_document
from src.ingestion.parser import parse_and_normalize

logger = structlog.get_logger()


def compute_parse_quality(records: list[NormalizedSourceRecord]) -> float:
    """Simple parse quality heuristic based on record characteristics."""
    if not records:
        return 0.0
    scores = []
    for r in records:
        text = r.text_content
        score = 1.0
        if len(text) < 20:
            score *= 0.5
        if len(text) > 5000:
            score *= 0.8
        scores.append(score)
    return sum(scores) / len(scores)


def process_single_job(
    db,
    job: IngestionJob,
    on_progress: callable | None = None,
) -> int:
    """Run the full fetch→store→parse→chunk pipeline for a single IngestionJob.

    Args:
        db: SQLAlchemy session
        job: The pending IngestionJob to process
        on_progress: Optional callback(message: str) for status updates

    Returns:
        Number of normalized_source_records created (0 on failure).

    Updates job.status to completed/failed and commits after each phase.
    """
    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg, job_id=job.id)

    try:
        # --- Phase 1: Fetch ---
        job.status = IngestionStatus.fetching
        job.fetch_started_at = datetime.utcnow()
        db.commit()
        _log(f"Fetching {job.fetch_url}")

        raw_artifact = fetch_document(db, job)

        job.status = IngestionStatus.fetched
        job.fetch_completed_at = datetime.utcnow()
        db.commit()
        _log(
            f"Stored artifact: {raw_artifact.content_type}, "
            f"{raw_artifact.size_bytes:,} bytes, sha256={raw_artifact.sha256_hash[:12]}"
        )

        # --- Phase 2: Parse + Chunk ---
        job.status = IngestionStatus.parsing
        job.parse_started_at = datetime.utcnow()
        db.commit()

        records = parse_and_normalize(db, job, raw_artifact)

        job.status = IngestionStatus.completed
        job.parse_completed_at = datetime.utcnow()
        job.parse_quality_score = compute_parse_quality(records)
        db.commit()
        _log(f"Parsed into {len(records)} passages (quality={job.parse_quality_score:.2f})")

        return len(records)

    except Exception as e:
        job.status = IngestionStatus.failed
        job.error_message = str(e)[:2000]
        db.commit()
        logger.error("ingestion_failed", job_id=job.id, error=str(e))
        return 0


def run_pending_ingestion(
    db,
    limit: int | None = None,
    on_progress: callable | None = None,
) -> dict:
    """Process all pending ingestion jobs.

    Args:
        db: SQLAlchemy session
        limit: Max number of jobs to process (None = all pending)
        on_progress: Optional callback(message: str) for status updates

    Returns:
        Summary dict with counts of completed, failed, total_passages.
    """
    from sqlalchemy import select

    query = select(IngestionJob).where(IngestionJob.status == IngestionStatus.pending)
    if limit:
        query = query.limit(limit)

    pending_jobs = db.scalars(query).all()

    summary = {
        "total_pending": len(pending_jobs),
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "total_passages": 0,
    }

    if not pending_jobs:
        if on_progress:
            on_progress("No pending ingestion jobs found.")
        return summary

    if on_progress:
        on_progress(f"Found {len(pending_jobs)} pending ingestion jobs")

    for i, job in enumerate(pending_jobs, 1):
        if on_progress:
            dv = job.document_version
            label = "unknown"
            if dv and dv.family:
                label = f"{dv.family.source.jurisdiction_code} - {dv.family.short_cite}"
            on_progress(f"\n[{i}/{len(pending_jobs)}] Job #{job.id}: {label}")

        passage_count = process_single_job(db, job, on_progress=on_progress)

        if job.status == IngestionStatus.completed:
            summary["completed"] += 1
            summary["total_passages"] += passage_count
        elif job.status == IngestionStatus.failed:
            summary["failed"] += 1
            if on_progress:
                on_progress(f"  FAILED: {job.error_message}")

    return summary
