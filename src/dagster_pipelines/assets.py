"""Dagster asset definitions for the ingestion and extraction pipeline.

Follows Dagster's asset-based lineage model. Each asset represents a
materialized stage in the pipeline with full dependency tracking.
"""

import dagster
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agents.ambiguity import AmbiguityAgent
from src.agents.base import BaseExtractionAgent
from src.agents.definition_actor import DefinitionActorAgent
from src.agents.obligation import ObligationAgent
from src.agents.threshold_exception import ThresholdExceptionAgent
from src.core.confidence import compute_confidence
from src.db.engine import SessionLocal
from src.db.models import (
    ConfidenceTier,
    Extraction,
    ExtractionType,
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
    ReviewQueueItem,
    ReviewStatus,
)
from src.ingestion.pipeline import process_single_job
from src.schemas.extraction import EXTRACTION_TYPE_SCHEMAS, AbstentionResult

logger = structlog.get_logger()

# Agent registry — 4 consolidated agents per Recommendation #1
AGENTS: dict[str, BaseExtractionAgent] = {
    "obligation": ObligationAgent(),
    "definition_actor": DefinitionActorAgent(),
    "threshold_exception": ThresholdExceptionAgent(),
    "ambiguity": AmbiguityAgent(),
}

# Maps agent names to the ExtractionType values they produce
AGENT_EXTRACTION_TYPES: dict[str, list[ExtractionType]] = {
    "obligation": [ExtractionType.obligation, ExtractionType.timeline, ExtractionType.enforcement],
    "definition_actor": [
        ExtractionType.definition,
        ExtractionType.actor_mapping,
        ExtractionType.framework_ref,
    ],
    "threshold_exception": [ExtractionType.threshold, ExtractionType.exception],
    "ambiguity": [ExtractionType.ambiguity],
}


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

    Each agent makes a single LLM call per passage (Recs #1, #2, #3).
    Returns total number of extractions created.
    """
    db = SessionLocal()
    total_extractions = 0

    try:
        # Find passages without extractions
        records = db.scalars(
            select(NormalizedSourceRecord)
            .outerjoin(Extraction)
            .where(Extraction.id.is_(None))
            .limit(500)
        ).all()

        for agent_name, agent in AGENTS.items():
            context.log.info(f"Running {agent_name} agent on {len(records)} passages")

            for record in records:
                ctx = _build_context(db, record)

                try:
                    result = agent.extract(record.text_content, ctx)

                    if isinstance(result, AbstentionResult):
                        continue

                    # Determine primary extraction type
                    primary_type = AGENT_EXTRACTION_TYPES[agent_name][0]
                    schema_class = EXTRACTION_TYPE_SCHEMAS.get(
                        primary_type.value
                    )

                    # Compute confidence score (Rec #8)
                    evidence = result.get("evidence_spans", [])
                    job = db.scalars(
                        select(IngestionJob).where(
                            IngestionJob.document_version_id == record.document_version_id
                        )
                    ).first()
                    parse_quality = job.parse_quality_score if job else None

                    confidence = compute_confidence(
                        schema_valid=True,
                        evidence_spans=evidence,
                        extraction_payload=result,
                        schema_class=schema_class,
                        parse_quality_score=parse_quality,
                    )

                    # Create extraction record
                    extraction = Extraction(
                        source_record_id=record.id,
                        extraction_type=primary_type,
                        payload=result,
                        evidence_spans=evidence,
                        confidence_score=confidence.total_score,
                        confidence_tier=ConfidenceTier(confidence.tier),
                        review_status=ReviewStatus.pending,
                        prompt_template_version=result.get("_prompt_hash"),
                        model_id=result.get("_model_id"),
                    )
                    db.add(extraction)
                    db.flush()

                    # Route to review queue
                    review_item = ReviewQueueItem(
                        extraction_id=extraction.id,
                        priority=_confidence_to_priority(confidence.tier),
                        status=ReviewStatus.pending,
                    )
                    db.add(review_item)
                    total_extractions += 1

                except Exception as e:
                    context.log.error(
                        f"Extraction failed: {agent_name} on record {record.id}: {e}"
                    )

            db.commit()

        context.log.info(f"Total extractions created: {total_extractions}")
        return total_extractions
    finally:
        db.close()


def _build_context(db: Session, record: NormalizedSourceRecord) -> dict:
    """Build context dict for an extraction agent."""
    dv = record.document_version
    df = dv.family if dv else None
    s = df.source if df else None
    return {
        "document_title": df.canonical_title if df else None,
        "jurisdiction": s.jurisdiction_code if s else None,
        "section_path": record.section_path,
    }


def _confidence_to_priority(tier: str) -> int:
    """Map confidence tier to review priority (higher = more urgent)."""
    return {"A": 0, "B": 1, "C": 2, "D": 3}.get(tier, 1)
