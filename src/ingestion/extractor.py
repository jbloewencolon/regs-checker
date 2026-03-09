"""Extraction pipeline — run AI agents against normalized passages.

Shared logic used by both:
  - Dagster extracted_obligations asset
  - CLI: python -m src.scripts.seed_pipeline --mode extract

Steps:
  1. Query NormalizedSourceRecords without extractions (or with pending ExtractionJob)
  2. For each passage, run all 4 agents (obligation, definition_actor, threshold_exception, ambiguity)
  3. Validate output via Pydantic, verify evidence spans via string matching
  4. Compute confidence score and tier
  5. Write Extraction + ReviewQueueItem records
  6. Track progress in ExtractionJob table

Enhancements over initial implementation:
  - Multi-extraction support (agents can return multiple items per passage)
  - Concurrent agent execution via ThreadPoolExecutor
  - Extraction deduplication (content-hash guard)
  - Token usage tracking per extraction
  - Structured logging for observability
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import select

from src.agents.ambiguity import AmbiguityAgent
from src.agents.base import BaseExtractionAgent, ExtractionResult
from src.agents.definition_actor import DefinitionActorAgent
from src.agents.obligation import ObligationAgent
from src.agents.threshold_exception import ThresholdExceptionAgent
from src.core.confidence import compute_confidence
from src.db.models import (
    ConfidenceTier,
    Extraction,
    ExtractionJob,
    ExtractionType,
    IngestionJob,
    NormalizedSourceRecord,
    ReviewQueueItem,
    ReviewStatus,
)
from src.schemas.extraction import EXTRACTION_TYPE_SCHEMAS

logger = structlog.get_logger()

# Agent registry — 4 consolidated agents per Recommendation #1
AGENTS: dict[str, BaseExtractionAgent] = {}

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


@dataclass
class TokenUsageSummary:
    """Aggregate token usage across an extraction run."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_calls += 1


def _get_agents() -> dict[str, BaseExtractionAgent]:
    """Lazy-init agents (avoids Anthropic client creation at import time)."""
    global AGENTS
    if not AGENTS:
        AGENTS = {
            "obligation": ObligationAgent(),
            "definition_actor": DefinitionActorAgent(),
            "threshold_exception": ThresholdExceptionAgent(),
            "ambiguity": AmbiguityAgent(),
        }
    return AGENTS


def _confidence_to_priority(tier: str) -> int:
    """Map confidence tier to review priority (higher = more urgent)."""
    return {"A": 0, "B": 1, "C": 2, "D": 3}.get(tier, 1)


def _build_context(db, record: NormalizedSourceRecord) -> dict:
    """Build context dict for an extraction agent."""
    dv = record.document_version
    df = dv.family if dv else None
    s = df.source if df else None
    return {
        "document_title": df.canonical_title if df else None,
        "jurisdiction": s.jurisdiction_code if s else None,
        "section_path": record.section_path,
    }


def _content_hash(agent_name: str, text: str) -> str:
    """Compute a deduplication hash for (agent, passage_text)."""
    return hashlib.sha256(f"{agent_name}:{text}".encode()).hexdigest()[:24]


def _run_agent(
    agent_name: str,
    agent: BaseExtractionAgent,
    passage: str,
    context: dict,
) -> tuple[str, ExtractionResult | Exception]:
    """Run a single agent (designed for ThreadPoolExecutor)."""
    try:
        result = agent.extract(passage, context)
        return agent_name, result
    except Exception as e:
        return agent_name, e


def extract_single_record(
    db,
    record: NormalizedSourceRecord,
    agents: dict[str, BaseExtractionAgent],
    extraction_job: ExtractionJob | None = None,
    parse_quality: float | None = None,
    token_usage: TokenUsageSummary | None = None,
    existing_hashes: set[str] | None = None,
) -> int:
    """Run all agents against a single passage. Returns extraction count.

    Agents run concurrently via ThreadPoolExecutor for improved throughput.
    Deduplication is based on a content hash of (agent_name, passage_text).
    """
    ctx = _build_context(db, record)
    extractions_created = 0

    # Build futures for concurrent execution
    agent_results: list[tuple[str, str, ExtractionResult | Exception]] = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for agent_name, agent in agents.items():
            # Deduplication guard
            content_hash = _content_hash(agent_name, record.text_content)
            if existing_hashes is not None and content_hash in existing_hashes:
                logger.debug(
                    "extraction_deduplicated",
                    agent=agent_name,
                    record_id=record.id,
                )
                continue

            future = executor.submit(
                _run_agent, agent_name, agent, record.text_content, ctx
            )
            futures[future] = (agent_name, content_hash)

        # Collect results
        for future in as_completed(futures):
            agent_name, content_hash = futures[future]
            name, result = future.result()
            agent_results.append((name, content_hash, result))

    # Process results (back on main thread for DB writes)
    for name, content_hash, result in agent_results:
        if isinstance(result, Exception):
            logger.error(
                "agent_extraction_failed",
                agent=name,
                record_id=record.id,
                error=str(result),
                section_path=record.section_path,
            )
            continue

        # Track token usage
        if token_usage is not None:
            token_usage.add(result.input_tokens, result.output_tokens)

        # Log structured result
        logger.info(
            "agent_extraction_completed",
            agent=name,
            record_id=record.id,
            extraction_count=len(result.extractions),
            abstained=result.abstention is not None,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            template_version=result.template_version,
        )

        if result.abstention is not None:
            continue

        # Mark hash as seen
        if existing_hashes is not None:
            existing_hashes.add(content_hash)

        # Process each extraction from the multi-extraction result
        primary_type = AGENT_EXTRACTION_TYPES[name][0]
        schema_class = EXTRACTION_TYPE_SCHEMAS.get(primary_type.value)

        for item in result.extractions:
            try:
                evidence = item.get("evidence_spans", [])
                confidence = compute_confidence(
                    schema_valid=True,
                    evidence_spans=evidence,
                    extraction_payload=item,
                    schema_class=schema_class,
                    parse_quality_score=parse_quality,
                )

                extraction = Extraction(
                    source_record_id=record.id,
                    extraction_type=primary_type,
                    payload=item,
                    evidence_spans=evidence,
                    confidence_score=confidence.total_score,
                    confidence_tier=ConfidenceTier(confidence.tier),
                    review_status=ReviewStatus.pending,
                    prompt_template_version=result.prompt_hash,
                    model_id=result.model_id,
                    extraction_job_id=extraction_job.id if extraction_job else None,
                )
                db.add(extraction)
                db.flush()

                db.add(ReviewQueueItem(
                    extraction_id=extraction.id,
                    priority=_confidence_to_priority(confidence.tier),
                    status=ReviewStatus.pending,
                ))
                extractions_created += 1

                logger.info(
                    "extraction_created",
                    extraction_id=extraction.id,
                    agent=name,
                    record_id=record.id,
                    confidence_score=confidence.total_score,
                    confidence_tier=confidence.tier,
                    evidence_verified=sum(1 for e in evidence if e.get("verified")),
                    evidence_total=len(evidence),
                )

            except Exception as e:
                logger.error(
                    "extraction_record_failed",
                    agent=name,
                    record_id=record.id,
                    error=str(e),
                )

    return extractions_created


def run_extraction(
    db,
    limit: int | None = None,
    on_progress: callable | None = None,
) -> dict:
    """Run extraction agents against all unprocessed passages.

    Args:
        db: SQLAlchemy session
        limit: Max passages to process (None = all unprocessed)
        on_progress: Optional callback(message: str) for status updates

    Returns:
        Summary dict with counts and token usage.
    """
    agents = _get_agents()
    token_usage = TokenUsageSummary()

    # Build set of existing content hashes for deduplication
    existing_hashes: set[str] = set()

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    # Find passages without any extractions
    query = (
        select(NormalizedSourceRecord)
        .outerjoin(Extraction)
        .where(Extraction.id.is_(None))
    )
    if limit:
        query = query.limit(limit)

    records = db.scalars(query).all()

    summary = {
        "total_records": len(records),
        "total_extractions": 0,
        "records_processed": 0,
        "records_failed": 0,
        "token_usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_calls": 0,
        },
    }

    if not records:
        _log("No unprocessed passages found.")
        return summary

    _log(f"Found {len(records)} passages to extract from")

    # Group records by document_version for ExtractionJob tracking
    dv_records: dict[int, list[NormalizedSourceRecord]] = {}
    for record in records:
        dv_id = record.document_version_id
        dv_records.setdefault(dv_id, []).append(record)

    _log(f"Spanning {len(dv_records)} document versions")

    for dv_id, dv_group in dv_records.items():
        # Create ExtractionJob for tracking
        extraction_job = ExtractionJob(
            document_version_id=dv_id,
            agent_name="all_agents",
            status="running",
            records_total=len(dv_group),
            started_at=datetime.utcnow(),
        )
        db.add(extraction_job)
        db.flush()

        # Get parse quality from the ingestion job
        ingestion_job = db.scalars(
            select(IngestionJob).where(
                IngestionJob.document_version_id == dv_id
            )
        ).first()
        parse_quality = ingestion_job.parse_quality_score if ingestion_job else None

        # Get document label for logging
        first_rec = dv_group[0]
        dv = first_rec.document_version
        label = "unknown"
        if dv and dv.family:
            label = f"{dv.family.source.jurisdiction_code} - {dv.family.short_cite}"

        _log(f"\n[{label}] Processing {len(dv_group)} passages...")

        job_extractions = 0
        job_failures = 0

        for i, record in enumerate(dv_group):
            try:
                count = extract_single_record(
                    db, record, agents, extraction_job, parse_quality,
                    token_usage, existing_hashes,
                )
                job_extractions += count
                extraction_job.records_processed += 1
                summary["records_processed"] += 1

                # Commit in batches of 10 to avoid holding huge transactions
                if (i + 1) % 10 == 0:
                    db.commit()
                    _log(f"  {i + 1}/{len(dv_group)} passages processed...")

            except Exception as e:
                job_failures += 1
                extraction_job.records_failed += 1
                summary["records_failed"] += 1
                logger.error("record_extraction_error", record_id=record.id, error=str(e))

        # Finalize extraction job
        extraction_job.status = "completed" if job_failures == 0 else "completed_with_errors"
        extraction_job.completed_at = datetime.utcnow()
        db.commit()

        summary["total_extractions"] += job_extractions
        _log(
            f"  Done: {job_extractions} extractions from {len(dv_group)} passages "
            f"({job_failures} failures)"
        )

    # Finalize token usage in summary
    summary["token_usage"] = {
        "input_tokens": token_usage.total_input_tokens,
        "output_tokens": token_usage.total_output_tokens,
        "total_tokens": token_usage.total_tokens,
        "total_calls": token_usage.total_calls,
    }

    _log(f"\nExtraction complete: {summary['total_extractions']} total extractions")
    _log(
        f"Token usage: {token_usage.total_input_tokens:,} input + "
        f"{token_usage.total_output_tokens:,} output = "
        f"{token_usage.total_tokens:,} total across {token_usage.total_calls} API calls"
    )
    return summary
