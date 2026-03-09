"""Dagster asset definitions for the ingestion and extraction pipeline.

Follows Dagster's asset-based lineage model. Each asset represents a
materialized stage in the pipeline with full dependency tracking.
"""

import dagster
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.engine import SessionLocal
from src.db.models import (
    IngestionJob,
    IngestionStatus,
)
from src.ingestion.extractor import run_extraction
from src.ingestion.pipeline import process_single_job

logger = structlog.get_logger()


@dagster.asset(
    description="Fetch and parse documents from legislative sources",
    group_name="ingestion",
)
def ingested_documents(context: dagster.AssetExecutionContext) -> list[int]:
    """Fetch, parse, and normalize documents into passage-level records.

    Delegates to src.ingestion.pipeline which handles the full
    fetch → S3 store → parse → chunk workflow per job.

    Returns list of document_version_ids that were successfully processed.
    """
    db = SessionLocal()
    try:
        pending_jobs = db.scalars(
            select(IngestionJob).where(IngestionJob.status == IngestionStatus.pending)
        ).all()

        processed_versions = []
        for job in pending_jobs:
            passage_count = process_single_job(
                db, job, on_progress=lambda msg: context.log.info(msg)
            )
            if job.status == IngestionStatus.completed:
                processed_versions.append(job.document_version_id)
                context.log.info(
                    f"Ingested document version {job.document_version_id}: "
                    f"{passage_count} passages"
                )
            else:
                context.log.error(
                    f"Ingestion failed for job {job.id}: {job.error_message}"
                )

        return processed_versions
    finally:
        db.close()


@dagster.asset(
    description="Run extraction agents on ingested documents",
    group_name="extraction",
    deps=[ingested_documents],
)
def extracted_obligations(context: dagster.AssetExecutionContext) -> int:
    """Run 4 consolidated agents against all unprocessed passages.

    Delegates to the shared run_extraction() pipeline which handles:
      - Filtering tiny passages (<150 chars)
      - Merging adjacent short fragments
      - Selective agent routing based on content signals
      - Concurrent agent execution
      - Orrick key_requirements context injection

    Returns total number of extractions created.
    """
    db = SessionLocal()
    try:
        summary = run_extraction(
            db,
            limit=500,
            on_progress=lambda msg: context.log.info(msg),
        )

        context.log.info(
            f"Extraction complete: {summary['total_extractions']} extractions, "
            f"{summary['records_processed']} records processed, "
            f"{summary.get('records_skipped_short', 0)} short passages skipped, "
            f"{summary.get('passages_merged', 0)} passages merged, "
            f"{summary.get('agents_skipped_by_signal', 0)} agent calls avoided"
        )
        return summary["total_extractions"]
    finally:
        db.close()
