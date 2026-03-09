"""Seed script for populating the database with initial documents for ingestion.

Usage:
    # Seed Colorado SB205 manually:
    python -m src.scripts.seed_pipeline --mode manual

    # Discover and seed all bills from Orrick AI Law Tracker:
    python -m src.scripts.seed_pipeline --mode orrick

    # Fetch + parse + chunk all pending ingestion jobs:
    python -m src.scripts.seed_pipeline --mode fetch

    # Fetch with a limit (useful for testing):
    python -m src.scripts.seed_pipeline --mode fetch --limit 5

    # Re-queue failed jobs back to pending and retry:
    python -m src.scripts.seed_pipeline --mode retry-failed

    # Re-queue only specific error types:
    python -m src.scripts.seed_pipeline --mode retry-failed --error-filter 403
    python -m src.scripts.seed_pipeline --mode retry-failed --error-filter "SSL"
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import structlog

from src.db.engine import SessionLocal
from src.db.models import (
    DocumentFamily,
    DocumentVersion,
    IngestionJob,
    IngestionStatus,
    LegalEvent,
    LegalEventType,
    Source,
    TemporalStatus,
)

logger = structlog.get_logger()


def seed_colorado_sb205(db) -> IngestionJob:
    """Seed Colorado SB21-169 (the Colorado AI Act) for ingestion.

    This is the primary test document for the pipeline — creates all records
    needed for Dagster to pick up and process.
    """
    # Source
    source = db.query(Source).filter_by(
        jurisdiction_code="CO", connector_id="colorado_ga"
    ).first()
    if not source:
        source = Source(
            jurisdiction_code="CO",
            jurisdiction_name="Colorado",
            source_type="state_statute",
            base_url="https://leg.colorado.gov",
            connector_id="colorado_ga",
        )
        db.add(source)
        db.flush()

    # Document Family
    family = db.query(DocumentFamily).filter_by(
        source_id=source.id, short_cite="SB21-169"
    ).first()
    if not family:
        family = DocumentFamily(
            source_id=source.id,
            canonical_title="Colorado SB21-169 - Concerning Consumer Protections for "
            "Interactions with Artificial Intelligence Systems",
            short_cite="SB21-169",
            subject_area="artificial_intelligence",
            metadata_={
                "bill_number": "SB21-169",
                "session": "2024 Regular Session",
                "also_known_as": "Colorado AI Act",
            },
        )
        db.add(family)
        db.flush()

    # Document Version (enrolled/final version)
    version = db.query(DocumentVersion).filter_by(
        family_id=family.id, version_label="Enrolled"
    ).first()
    if not version:
        version = DocumentVersion(
            family_id=family.id,
            version_label="Enrolled",
            temporal_status=TemporalStatus.active,
            effective_date=date(2026, 2, 1),
            metadata_={
                "source": "Colorado General Assembly website",
            },
        )
        db.add(version)
        db.flush()

        # Legal events for SB21-169
        events = [
            (LegalEventType.enactment, date(2024, 5, 8), "Signed by Governor", "Governor"),
            (LegalEventType.effective, date(2026, 2, 1), "Effective date", "Colorado Legislature"),
        ]
        for event_type, event_date, desc, authority in events:
            db.add(LegalEvent(
                document_version_id=version.id,
                event_type=event_type,
                event_date=event_date,
                description=desc,
                authority=authority,
            ))

    # Ingestion Job
    existing_job = db.query(IngestionJob).filter_by(
        document_version_id=version.id
    ).first()
    if existing_job:
        logger.info("job_exists", job_id=existing_job.id, status=existing_job.status)
        return existing_job

    job = IngestionJob(
        document_version_id=version.id,
        status=IngestionStatus.pending,
        fetch_url="https://leg.colorado.gov/sites/default/files/2024a_205_signed.pdf",
    )
    db.add(job)
    db.flush()

    logger.info("seeded_sb205", job_id=job.id, version_id=version.id)
    return job


def seed_federal_nist_ai_rmf(db) -> IngestionJob:
    """Seed the NIST AI Risk Management Framework for ingestion."""
    source = db.query(Source).filter_by(
        jurisdiction_code="US", connector_id="federal_nist"
    ).first()
    if not source:
        source = Source(
            jurisdiction_code="US",
            jurisdiction_name="United States (Federal)",
            source_type="federal_framework",
            base_url="https://www.nist.gov",
            connector_id="federal_nist",
        )
        db.add(source)
        db.flush()

    family = db.query(DocumentFamily).filter_by(
        source_id=source.id, short_cite="NIST AI 100-1"
    ).first()
    if not family:
        family = DocumentFamily(
            source_id=source.id,
            canonical_title="NIST AI Risk Management Framework (AI RMF 1.0)",
            short_cite="NIST AI 100-1",
            subject_area="artificial_intelligence",
            metadata_={"framework_version": "1.0"},
        )
        db.add(family)
        db.flush()

    version = db.query(DocumentVersion).filter_by(
        family_id=family.id, version_label="1.0"
    ).first()
    if not version:
        version = DocumentVersion(
            family_id=family.id,
            version_label="1.0",
            temporal_status=TemporalStatus.active,
            effective_date=date(2023, 1, 26),
        )
        db.add(version)
        db.flush()

        db.add(LegalEvent(
            document_version_id=version.id,
            event_type=LegalEventType.effective,
            event_date=date(2023, 1, 26),
            description="NIST AI RMF 1.0 published",
            authority="NIST",
        ))

    existing_job = db.query(IngestionJob).filter_by(
        document_version_id=version.id
    ).first()
    if existing_job:
        return existing_job

    job = IngestionJob(
        document_version_id=version.id,
        status=IngestionStatus.pending,
        fetch_url="https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf",
    )
    db.add(job)
    db.flush()

    logger.info("seeded_nist_rmf", job_id=job.id)
    return job


def seed_via_orrick(db) -> list[IngestionJob]:
    """Scrape Orrick AI Law Tracker and seed all discovered bills."""
    from src.ingestion.orrick_scraper import scrape_tracker, seed_from_tracker

    records = scrape_tracker()
    jobs = seed_from_tracker(db, records)
    return jobs


def run_fetch(db, limit: int | None = None) -> dict:
    """Fetch, store, parse, and chunk all pending ingestion jobs."""
    from src.ingestion.pipeline import run_pending_ingestion

    return run_pending_ingestion(db, limit=limit, on_progress=print)


def retry_failed_jobs(db, error_filter: str | None = None) -> dict:
    """Re-queue failed ingestion jobs back to pending, then re-run them.

    Args:
        db: SQLAlchemy session
        error_filter: If set, only retry jobs whose error_message contains this
                      substring (case-insensitive). E.g. "403", "SSL", "timeout".

    Returns:
        Summary dict with requeued count and fetch results.
    """
    from src.ingestion.pipeline import run_pending_ingestion

    failed_jobs = db.query(IngestionJob).filter(
        IngestionJob.status == IngestionStatus.failed
    ).all()

    if error_filter:
        needle = error_filter.lower()
        failed_jobs = [
            j for j in failed_jobs
            if j.error_message and needle in j.error_message.lower()
        ]

    if not failed_jobs:
        print("No matching failed jobs found.")
        return {"requeued": 0, "completed": 0, "failed": 0, "total_passages": 0}

    # Show what we're about to retry
    print(f"Re-queuing {len(failed_jobs)} failed jobs:")
    for job in failed_jobs:
        dv = job.document_version
        label = "unknown"
        if dv and dv.family:
            label = f"{dv.family.source.jurisdiction_code} - {dv.family.short_cite}"
        err_snippet = (job.error_message or "")[:80]
        print(f"  Job #{job.id}: {label}  ({err_snippet})")

    # Reset to pending
    for job in failed_jobs:
        job.status = IngestionStatus.pending
        job.error_message = None
    db.commit()

    print(f"\nRe-queued {len(failed_jobs)} jobs. Starting fetch...\n")

    # Now run the fetch pipeline on the re-queued jobs
    summary = run_pending_ingestion(db, on_progress=print)
    summary["requeued"] = len(failed_jobs)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Seed the regs-checker pipeline")
    parser.add_argument(
        "--mode",
        choices=["manual", "orrick", "fetch", "retry-failed"],
        default="manual",
        help=(
            "Pipeline mode: "
            "'manual' seeds hardcoded docs, "
            "'orrick' scrapes Orrick tracker, "
            "'fetch' processes all pending ingestion jobs, "
            "'retry-failed' re-queues and retries failed jobs"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of jobs to process in fetch mode (default: all)",
    )
    parser.add_argument(
        "--error-filter",
        type=str,
        default=None,
        help="Only retry failed jobs matching this substring (e.g. '403', 'SSL', 'timeout')",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.mode == "manual":
            job1 = seed_colorado_sb205(db)
            job2 = seed_federal_nist_ai_rmf(db)
            db.commit()
            print(f"Seeded CO SB21-169: IngestionJob #{job1.id} (status: {job1.status})")
            print(f"Seeded NIST AI RMF: IngestionJob #{job2.id} (status: {job2.status})")
        elif args.mode == "orrick":
            jobs = seed_via_orrick(db)
            db.commit()
            print(f"Seeded {len(jobs)} laws from Orrick AI Law Tracker")
            for job in jobs:
                dv = job.document_version
                print(
                    f"  Job #{job.id}: {dv.family.source.jurisdiction_code} - "
                    f"{dv.family.short_cite}"
                )
        elif args.mode == "fetch":
            summary = run_fetch(db, limit=args.limit)
            print(f"\n{'=' * 60}")
            print("Ingestion complete:")
            print(f"  Pending:         {summary['total_pending']}")
            print(f"  Completed:       {summary['completed']}")
            print(f"  Failed:          {summary['failed']}")
            print(f"  Total passages:  {summary['total_passages']}")
        elif args.mode == "retry-failed":
            summary = retry_failed_jobs(db, error_filter=args.error_filter)
            print(f"\n{'=' * 60}")
            print("Retry complete:")
            print(f"  Re-queued:       {summary['requeued']}")
            print(f"  Completed:       {summary['completed']}")
            print(f"  Still failed:    {summary['failed']}")
            print(f"  Total passages:  {summary['total_passages']}")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
