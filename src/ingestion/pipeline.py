"""Ingestion pipeline — parse and chunk pending documents.

The fetch-based ingestion system has been archived. New documents are
ingested via local files (see src/ingestion/local_ingest.py).

This module is retained for:
  - Re-parsing already-fetched jobs (status=fetched)
  - Dashboard manual upload flow
  - Dagster ingested_documents asset compatibility
"""

from __future__ import annotations

import threading
from datetime import datetime

import structlog
from sqlalchemy import func, select

from src.core.circuit_breaker import CircuitBreakerTripped, FailureTracker
from src.db.models import (
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
)

# Global cancellation event — set to signal running pipeline to stop.
_cancel_event = threading.Event()


def request_cancel() -> None:
    """Signal the running fetch pipeline to stop after the current job."""
    _cancel_event.set()


def is_cancelled() -> bool:
    """Check whether cancellation has been requested."""
    return _cancel_event.is_set()


def clear_cancel() -> None:
    """Reset the cancellation flag (called at pipeline start)."""
    _cancel_event.clear()
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


def fetch_document(db, job: IngestionJob):
    """Look up the pre-downloaded RawArtifact for a job, or read from disk for
    local-mode jobs that were seeded without the ingest phase.

    Returns:
        RawArtifact — existing or newly-created artifact for this job.

    Raises:
        RuntimeError if no artifact and no local file can be found.
    """
    import hashlib
    from datetime import datetime as _dt
    from pathlib import Path

    from src.db.models import RawArtifact as RawArtifactModel

    artifact = db.scalars(
        select(RawArtifactModel)
        .where(RawArtifactModel.document_version_id == job.document_version_id)
        .order_by(RawArtifactModel.created_at.desc())
    ).first()
    if artifact is not None:
        return artifact

    # No artifact yet — try reading from disk for local-mode jobs.
    if (job.metadata_ or {}).get("ingest_mode") != "local":
        raise RuntimeError(
            "No raw artifact found. Use --mode seed-local to ingest "
            "local files, or upload via the dashboard."
        )

    from src.ingestion.local_ingest import _detect_content_type, _resolve_local_file
    canonical_id = (job.metadata_ or {}).get("canonical_law_id", "")
    local_file: Path | None = None

    # Try the path stored at seed time first.
    stored = (job.metadata_ or {}).get("local_file")
    if stored:
        p = Path(stored)
        if p.exists():
            local_file = p

    if local_file is None:
        local_file = _resolve_local_file(canonical_id)

    if local_file is None or not local_file.exists():
        raise RuntimeError(
            f"Local-mode job has no artifact and no source file on disk "
            f"(canonical_law_id={canonical_id!r}). "
            f"Add the file to output/law_texts/ and re-run."
        )

    content_bytes = local_file.read_bytes()
    content_type = _detect_content_type(local_file)
    sha256 = hashlib.sha256(content_bytes).hexdigest()

    # Dedup by hash in case another job already stored the same file.
    existing = db.query(RawArtifactModel).filter_by(sha256_hash=sha256).first()
    if existing:
        return existing

    artifact = RawArtifactModel(
        document_version_id=job.document_version_id,
        sha256_hash=sha256,
        s3_key=f"local://{local_file}",
        content_type=content_type,
        size_bytes=len(content_bytes),
        is_primary=True,
    )
    db.add(artifact)

    # Stamp retrieval time on the document version.
    dv = job.document_version
    if dv and not dv.retrieved_at:
        dv.retrieved_at = _dt.utcnow()
    if dv and not dv.source_hash:
        dv.source_hash = sha256

    db.flush()
    logger.info(
        "local_artifact_created_on_demand",
        job_id=job.id,
        canonical_id=canonical_id,
        size_bytes=len(content_bytes),
    )
    return artifact


def process_single_job(
    db,
    job: IngestionJob,
    on_progress: callable | None = None,
    force_reparse: bool = False,
) -> int:
    """Run the full fetch→store→parse→chunk pipeline for a single IngestionJob.

    For local-mode jobs that were seeded without the ingest phase (seed-only),
    fetch_document now reads the file from disk and creates the artifact inline,
    so "Parse Documents" works even when "Seed & Ingest All" was not used.

    Args:
        db: SQLAlchemy session
        job: The pending IngestionJob to process
        on_progress: Optional callback(message: str) for status updates
        force_reparse: SFH-1h (audit SF-10) — re-parsing deletes the version's
            existing passages, which severs the FK lineage of every extraction
            built on them (triage results cascade too). Within a single static
            version that's survivable; once amended-bill versions arrive it
            silently destroys the history needed to diff an amendment and
            answer "what changed". When existing passages carry linked
            extractions, the delete now requires this explicit flag; the
            orphaned-extraction count is logged either way. A text CHANGE
            should never come through here at all — it must create a NEW
            DocumentVersion (predecessor_id set) with its own passage set
            (SFH-4b, diff-driven re-extraction).

    Returns:
        Number of normalized_source_records created (0 on failure).

    Updates job.status to completed/failed and commits after each phase.
    """
    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg, job_id=job.id)

    try:
        # --- Phase 1: Fetch (lookup pre-downloaded artifact) ---
        job.status = IngestionStatus.fetching
        db.commit()
        _log(f"Fetching artifact for job {job.id}…")

        raw_artifact = fetch_document(db, job)

        job.status = IngestionStatus.fetched
        db.commit()
        _log(
            f"Found artifact: {raw_artifact.content_type}, "
            f"{raw_artifact.size_bytes:,} bytes"
        )

        # Delete old parsed records so re-parse starts clean.
        # SFH-1h (SF-10): when those records carry linked extractions, the
        # delete severs extraction lineage — require an explicit opt-in and
        # log exactly what would be lost instead of discarding it silently.
        from src.db.models import Extraction
        from src.db.models import NormalizedSourceRecord as NSR
        old_records = db.scalars(
            select(NSR).where(
                NSR.document_version_id == job.document_version_id
            )
        ).all()
        if old_records:
            old_ids = [r.id for r in old_records]
            linked_extractions = db.scalar(
                select(func.count()).select_from(Extraction).where(
                    Extraction.source_record_id.in_(old_ids)
                )
            ) or 0
            if linked_extractions and not force_reparse:
                job.status = IngestionStatus.failed
                job.error_message = (
                    f"Re-parse blocked (SF-10): {len(old_records)} existing "
                    f"passages carry {linked_extractions} linked extraction(s) "
                    f"whose lineage the re-parse would destroy. If the source "
                    f"text changed, ingest it as a NEW document version "
                    f"(predecessor_id set) instead. To deliberately discard "
                    f"this version's extraction lineage, re-run with "
                    f"force_reparse=True."
                )
                db.commit()
                _log(f"✗ {job.error_message}")
                return 0
            if linked_extractions:
                logger.warning(
                    "reparse_lineage_discarded",
                    job_id=job.id,
                    document_version_id=job.document_version_id,
                    passages_deleted=len(old_records),
                    extractions_orphaned=linked_extractions,
                )
            for rec in old_records:
                db.delete(rec)
            db.commit()
            _log(f"Cleared {len(old_records)} old passages for re-parse")

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
    query = select(IngestionJob).where(
        IngestionJob.status.in_([
            IngestionStatus.pending,
            IngestionStatus.fetched,  # Already downloaded, needs re-parse
        ]),
    )
    if limit:
        query = query.limit(limit)

    pending_jobs = db.scalars(query).all()

    # Sort by status — fetched (re-parse) first, then pending
    pending_jobs.sort(key=lambda j: (0 if j.status == IngestionStatus.fetched else 1))

    summary = {
        "total_pending": len(pending_jobs),
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "total_passages": 0,
        "failed_jobs": [],       # List of {job_id, label, url, error} for UI
        "manual_review_jobs": [],  # Jobs needing manual doc insertion
    }

    if not pending_jobs:
        if on_progress:
            on_progress("No pending ingestion jobs found.")
        return summary

    if on_progress:
        on_progress(f"Found {len(pending_jobs)} pending ingestion jobs")

    # Clear any stale cancellation from a previous run
    clear_cancel()

    # Circuit breaker: abort if too many consecutive fetches fail
    # (network down, S3 unreachable, etc.)
    tracker = FailureTracker(
        context="fetch & parse (document downloads)",
        max_consecutive=5,
        max_failure_rate=0.8,
        min_items_for_rate=10,
    )

    try:
      for i, job in enumerate(pending_jobs, 1):
        # Check for cancellation between jobs
        if is_cancelled():
            if on_progress:
                on_progress(f"\nPipeline terminated by user after {i - 1} jobs.")
            summary["cancelled"] = True
            break

        if on_progress:
            dv = job.document_version
            label = "unknown"
            if dv and dv.family:
                label = f"{dv.family.source.jurisdiction_code} - {dv.family.short_cite}"
            on_progress(f"\n[{i}/{len(pending_jobs)}] Job #{job.id}: {label}")

        passage_count = process_single_job(db, job, on_progress=on_progress)

        dv = job.document_version
        label = "unknown"
        if dv and dv.family:
            label = f"{dv.family.source.jurisdiction_code} - {dv.family.short_cite}"

        if job.status == IngestionStatus.completed:
            summary["completed"] += 1
            summary["total_passages"] += passage_count
            tracker.record_success()
        elif job.status == IngestionStatus.failed:
            summary["failed"] += 1
            failure_info = {
                "job_id": job.id,
                "label": label,
                "url": job.fetch_url,
                "error": job.error_message,
            }
            summary["failed_jobs"].append(failure_info)
            tracker.record_failure(
                f"Job #{job.id} ({label}): {job.error_message[:100]}"
            )
            if on_progress:
                on_progress(
                    f"  FAILED: {job.error_message}\n"
                    f"    URL: {job.fetch_url}\n"
                    f"    → To fix: manually insert the document or update the URL"
                )
        elif job.status == IngestionStatus.requires_manual_review:
            summary["skipped"] += 1
            review_info = {
                "job_id": job.id,
                "label": label,
                "url": job.fetch_url,
                "ai_suggested_url": getattr(job, "ai_suggested_url", None),
                "error": job.error_message,
            }
            summary["manual_review_jobs"].append(review_info)
            # Manual review isn't a failure — don't count against circuit breaker
            tracker.record_success()
            if on_progress:
                suggested = getattr(job, "ai_suggested_url", None)
                on_progress(
                    f"  NEEDS MANUAL REVIEW: {job.error_message}\n"
                    f"    Original URL: {job.fetch_url}"
                    + (f"\n    AI-suggested URL: {suggested}" if suggested else "")
                    + "\n    → Insert the document manually or approve the suggested URL"
                )

    except CircuitBreakerTripped as cb:
        summary["circuit_breaker_tripped"] = True
        summary["circuit_breaker_detail"] = str(cb)
        if on_progress:
            on_progress(f"\n{cb}")

    return summary
