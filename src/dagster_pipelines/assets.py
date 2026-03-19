"""Dagster asset definitions for the discovery, ingestion, extraction, and sync pipeline.

Follows Dagster's asset-based lineage model. Each asset represents a
materialized stage in the pipeline with full dependency tracking.

Assets:
  - discovered_legislation: Classify and extract metadata from candidate bills (local LLM)
  - ingested_documents: Fetch + parse documents into passages
  - extracted_obligations: Run AI extraction agents on passages (Anthropic Haiku)
  - synced_extractions: Sync extractions to Policy Navigator DB
  - bridge_gap_report: Detect document families without bridge rows
"""

import os

import dagster
import structlog
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.engine import SessionLocal
from src.db.models import (
    IngestionJob,
    IngestionStatus,
)
from src.ingestion.extractor import run_extraction
from src.ingestion.pipeline import process_single_job

logger = structlog.get_logger()


@dagster.asset(
    description="Classify candidate bills and extract metadata using local LLM",
    group_name="discovery",
)
def discovered_legislation(context: dagster.AssetExecutionContext) -> dict:
    """Use local LLM (Llama 3.1 8B) to classify and extract metadata from candidate bills.

    Scans the candidate_urls table for unprocessed URLs, fetches their content,
    and uses the discovery agent to:
      1. Classify whether the text is AI-related legislation
      2. Extract structured metadata (title, jurisdiction, bill number, etc.)

    Bills that pass classification are flagged for ingestion.
    Returns a summary dict with counts of classified and accepted bills.
    """
    from src.agents.discovery import DiscoveryAgent

    db = SessionLocal()
    try:
        # Query for unprocessed candidate URLs
        from sqlalchemy import text

        candidates = db.execute(
            text(
                "SELECT id, url, raw_text FROM candidate_urls "
                "WHERE classification_status = 'pending' "
                "ORDER BY created_at LIMIT 100"
            )
        ).mappings().all()

        if not candidates:
            context.log.info("No pending candidate URLs to classify")
            return {"classified": 0, "accepted": 0, "rejected": 0}

        agent = DiscoveryAgent()
        accepted = 0
        rejected = 0
        total_input_tokens = 0
        total_output_tokens = 0

        for candidate in candidates:
            raw_text = candidate["raw_text"]
            if not raw_text or len(raw_text.strip()) < 100:
                db.execute(
                    text(
                        "UPDATE candidate_urls SET classification_status = 'rejected', "
                        "classification_reason = 'too_short' WHERE id = :id"
                    ),
                    {"id": candidate["id"]},
                )
                rejected += 1
                continue

            result = agent.classify_bill(raw_text)
            total_input_tokens += result.input_tokens
            total_output_tokens += result.output_tokens

            if result.is_ai_legislation and result.confidence >= 0.7:
                # Extract metadata for accepted bills
                metadata = agent.extract_metadata(raw_text)
                total_input_tokens += metadata.input_tokens
                total_output_tokens += metadata.output_tokens

                db.execute(
                    text(
                        "UPDATE candidate_urls SET "
                        "classification_status = 'accepted', "
                        "classification_confidence = :confidence, "
                        "classification_reason = :reasoning, "
                        "extracted_title = :title, "
                        "extracted_jurisdiction = :jurisdiction, "
                        "extracted_bill_number = :bill_number "
                        "WHERE id = :id"
                    ),
                    {
                        "id": candidate["id"],
                        "confidence": result.confidence,
                        "reasoning": result.reasoning,
                        "title": metadata.title,
                        "jurisdiction": metadata.jurisdiction_code,
                        "bill_number": metadata.bill_number,
                    },
                )
                accepted += 1
            else:
                db.execute(
                    text(
                        "UPDATE candidate_urls SET "
                        "classification_status = 'rejected', "
                        "classification_confidence = :confidence, "
                        "classification_reason = :reasoning "
                        "WHERE id = :id"
                    ),
                    {
                        "id": candidate["id"],
                        "confidence": result.confidence,
                        "reasoning": result.reasoning,
                    },
                )
                rejected += 1

        db.commit()

        context.log.info(
            f"Discovery complete: {len(candidates)} candidates processed, "
            f"{accepted} accepted, {rejected} rejected, "
            f"tokens: {total_input_tokens} in / {total_output_tokens} out"
        )

        return {
            "classified": len(candidates),
            "accepted": accepted,
            "rejected": rejected,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
        }
    except Exception as e:
        db.rollback()
        context.log.error(f"Discovery failed: {e}")
        raise
    finally:
        db.close()


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


@dagster.asset(
    description="Sync extractions from Regs Checker to Policy Navigator DB",
    group_name="sync",
    deps=[extracted_obligations],
)
def synced_extractions(context: dagster.AssetExecutionContext) -> int:
    """Incrementally sync new extractions to Policy Navigator's synced_extractions table.

    Uses cursor-based sync (MAX(system_a_extraction_id)) and the law_document_bridge
    table to resolve document families to law IDs. Applies the sync exclusion list
    and payload format adapter before inserting.

    Returns total number of rows synced.
    """
    from src.scripts.sync_extractions import sync_extractions

    source_url = os.environ.get("REGS_SUPABASE_URL")
    target_url = os.environ.get("REGS_POLICY_NAVIGATOR_URL")

    if not source_url or not target_url:
        context.log.warning(
            "Sync skipped: REGS_SUPABASE_URL and/or REGS_POLICY_NAVIGATOR_URL not set"
        )
        return 0

    summary = sync_extractions(
        source_url=source_url,
        target_url=target_url,
        dry_run=False,
    )

    context.log.info(
        f"Sync complete: {summary['synced']} rows synced, "
        f"{summary.get('skipped_no_bridge', 0)} skipped (no bridge), "
        f"{summary.get('skipped_excluded', 0)} skipped (excluded), "
        f"cursor {summary['cursor_start']} → {summary['cursor_end']}"
    )
    return summary["synced"]


@dagster.asset(
    description="Check legislative status of all tracked bills via Orrick and IAPP",
    group_name="discovery",
)
def bill_status_check(context: dagster.AssetExecutionContext) -> dict:
    """Cross-reference all tracked bills against Orrick and IAPP trackers.

    Detects when bills change status (e.g. pending → enacted, pending → dead)
    and updates DocumentVersion.temporal_status + logs a LegalEvent.

    Returns summary dict with counts of checked, changed, and errored bills.
    """
    from src.ingestion.status_checker import check_all_statuses

    db = SessionLocal()
    try:
        result = check_all_statuses(db, dry_run=False)

        for change in result.changes:
            context.log.info(
                f"Status changed: {change.jurisdiction_code} — "
                f"{change.family_title}: {change.old_status} → {change.new_status} "
                f"(source: {change.source})"
            )

        context.log.info(
            f"Status check complete: {result.checked} bills checked, "
            f"{result.changed} status changes detected, "
            f"{result.errors} errors"
        )

        return {
            "checked": result.checked,
            "changed": result.changed,
            "errors": result.errors,
            "pdf_records": result.pdf_records,
            "iapp_records": result.iapp_records,
        }
    except Exception as e:
        db.rollback()
        context.log.error(f"Status check failed: {e}")
        raise
    finally:
        db.close()


@dagster.asset(
    description="Detect document families without bridge rows in Policy Navigator",
    group_name="sync",
    deps=[extracted_obligations],
)
def bridge_gap_report(context: dagster.AssetExecutionContext) -> int:
    """Check for document families that have extractions but no law_document_bridge row.

    These families cannot be synced to Policy Navigator until bridge rows are created.
    Logs the gap report and returns the number of unbridged families.
    """
    from src.core.bridge_monitor import (
        detect_unbridged_families,
        format_bridge_gap_notification,
    )

    source_url = os.environ.get("REGS_SUPABASE_URL")
    target_url = os.environ.get("REGS_POLICY_NAVIGATOR_URL")

    if not source_url or not target_url:
        context.log.warning(
            "Bridge gap check skipped: REGS_SUPABASE_URL and/or "
            "REGS_POLICY_NAVIGATOR_URL not set"
        )
        return 0

    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)
    source_session = sessionmaker(bind=source_engine)()
    target_session = sessionmaker(bind=target_engine)()

    try:
        report = detect_unbridged_families(source_session, target_session)

        if report.has_gaps:
            notification = format_bridge_gap_notification(report)
            context.log.warning(notification)
        else:
            context.log.info(
                f"No bridge gaps. {report.bridged_families}/{report.total_families} "
                f"families have bridge rows."
            )

        return report.unbridged_families
    finally:
        source_session.close()
        target_session.close()
