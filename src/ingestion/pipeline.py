"""Ingestion pipeline — fetch, store, parse, and chunk pending documents.

Shared logic used by both:
  - Dagster ingested_documents asset
  - CLI: python -m src.scripts.seed_pipeline --mode fetch

Steps per pending IngestionJob:
  1. Fetch document at fetch_url (PDF or HTML from legislature sites)
  2. Store raw bytes in MinIO (raw-artifacts bucket), content-addressed by SHA-256
  3. Run Discovery Agent to classify content — if the URL was stale and
     the content is not AI legislation, trigger fallback verification
  4. Parse text out of PDF/HTML
  5. Chunk into normalized_source_records (passage-level)
  6. Update ingestion_job status to completed (or failed with error)
"""

from __future__ import annotations

from datetime import datetime

import structlog

from src.core.circuit_breaker import CircuitBreakerTripped, FailureTracker
from src.core.config import settings
from src.db.models import (
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
)
from src.ingestion.connector import fetch_document
from src.ingestion.parser import parse_and_normalize

logger = structlog.get_logger()


def _verify_content_or_fallback(
    db,
    job: IngestionJob,
    raw_artifact,
    _log: callable,
) -> bool:
    """Run Discovery Agent to classify fetched content; trigger fallback if stale.

    Returns True if the content is valid AI legislation (proceed with parsing).
    Returns False if the content is invalid and could not be auto-corrected
    (the job is marked requires_manual_review).
    """
    from src.agents.discovery import DiscoveryAgent
    from src.db.models import RawArtifact

    # Only run classification on text-like content
    if raw_artifact.content_type not in (
        "text/html",
        "text/plain",
        "application/pdf",
    ):
        return True

    # Read a sample of the raw text for classification
    try:
        from src.ingestion.parser import extract_text_sample
        text_sample = extract_text_sample(raw_artifact, max_chars=4000)
    except Exception:
        # If we can't extract text yet, skip classification and let parse handle it
        return True

    if not text_sample or len(text_sample.strip()) < 50:
        return True  # Too short to classify, proceed normally

    try:
        discovery = DiscoveryAgent()
        classification = discovery.classify_bill(text_sample)

        _log(
            f"Discovery classification: is_ai_legislation={classification.is_ai_legislation}, "
            f"confidence={classification.confidence:.2f}"
        )

        if classification.is_ai_legislation:
            return True  # Content is valid, proceed

        # --- Fallback: content is NOT AI legislation ---
        _log(
            f"Content at {job.fetch_url} is not AI legislation "
            f"(reason: {classification.reasoning}). Triggering fallback verification..."
        )

        # Build bill metadata from the document family
        dv = job.document_version
        df = dv.family if dv else None
        s = df.source if df else None
        bill_metadata = {
            "title": df.canonical_title if df else None,
            "jurisdiction": s.jurisdiction_code if s else None,
            "bill_number": df.short_cite if df else None,
            "primary_source_url": df.primary_source_url if df else None,
            "orrick_reference_url": df.orrick_reference_url if df else None,
            "iapp_reference_url": df.iapp_reference_url if df else None,
        }

        # Check if search is configured
        if not settings.search_provider:
            _log(
                "No search provider configured — marking job for manual review."
            )
            job.status = IngestionStatus.requires_manual_review
            job.error_message = (
                f"Discovery Agent classified content as non-AI-legislation "
                f"(confidence={classification.confidence:.2f}). "
                f"No search provider configured for fallback verification."
            )
            db.commit()
            return False

        # Search for the correct URL
        from src.ingestion.web_search import search_for_bill
        query_parts = [v for v in bill_metadata.values() if v]
        query = " ".join(query_parts) + " full text official"

        search_results = search_for_bill(query, max_results=3)

        if not search_results:
            _log("No search results found — marking job for manual review.")
            job.status = IngestionStatus.requires_manual_review
            job.error_message = (
                f"Discovery Agent classified content as non-AI-legislation. "
                f"Web search returned no results."
            )
            db.commit()
            return False

        # Run Verification Agent to pick the best URL
        from src.agents.verification import VerificationAgent
        verifier = VerificationAgent()
        verification = verifier.verify_url(
            bill_metadata=bill_metadata,
            search_results=[
                {"title": r.title, "url": r.url, "snippet": r.snippet}
                for r in search_results
            ],
        )

        _log(
            f"Verification result: url={verification.suggested_url}, "
            f"confidence={verification.confidence:.2f}, "
            f"reason={verification.reasoning}"
        )

        # Store the AI-suggested URL and mark for review
        job.ai_suggested_url = verification.suggested_url
        job.status = IngestionStatus.requires_manual_review
        job.error_message = (
            f"Discovery Agent classified content as non-AI-legislation "
            f"(confidence={classification.confidence:.2f}). "
            f"Verification Agent suggested: {verification.suggested_url} "
            f"(confidence={verification.confidence:.2f})."
        )
        db.commit()
        return False

    except Exception as e:
        logger.error(
            "content_verification_error",
            job_id=job.id,
            error=str(e),
        )
        # Fail open — don't block ingestion on verification errors
        return True


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

        # --- Phase 1.5: Discovery classification + fallback verification ---
        if not _verify_content_or_fallback(db, job, raw_artifact, _log):
            # Content was not AI legislation and fallback couldn't auto-fix
            return 0

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
        "failed_jobs": [],       # List of {job_id, label, url, error} for UI
        "manual_review_jobs": [],  # Jobs needing manual doc insertion
    }

    if not pending_jobs:
        if on_progress:
            on_progress("No pending ingestion jobs found.")
        return summary

    if on_progress:
        on_progress(f"Found {len(pending_jobs)} pending ingestion jobs")

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
