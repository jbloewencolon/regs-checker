"""Extraction pipeline — run AI agents against normalized passages.

Shared logic used by both:
  - Dagster extracted_obligations asset
  - CLI: python -m src.scripts.seed_pipeline --mode extract

Steps:
  1. Query NormalizedSourceRecords without extractions (or with pending ExtractionJob)
  2. Filter out tiny passages (<150 chars) and merge adjacent short fragments
  3. Select agents per passage based on content signals (keyword pre-screening)
  4. Run selected agents concurrently
  5. Validate output via Pydantic, verify evidence spans via string matching
  6. Compute confidence score and tier
  7. Write Extraction + ReviewQueueItem records
  8. Track progress in ExtractionJob table

Enhancements over initial implementation:
  - Multi-extraction support (agents can return multiple items per passage)
  - Concurrent agent execution via ThreadPoolExecutor
  - Extraction deduplication (content-hash guard)
  - Token usage tracking per extraction
  - Structured logging for observability

Cost optimizations:
  - Skip passages under MIN_PASSAGE_LENGTH chars (boilerplate/stubs)
  - Merge consecutive short passages from same section into single API call
  - Keyword-based agent selection skips irrelevant agents per passage
  - Orrick key_requirements injected as extraction context for higher accuracy
  - Batch API support via --batch flag (50% discount, 24h turnaround)
"""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import select

from src.agents.ambiguity import AmbiguityAgent
from src.agents.base import BaseExtractionAgent, ExtractionResult
from src.agents.definition_actor import DefinitionActorAgent
from src.agents.obligation import ObligationAgent
from src.agents.threshold_exception import ThresholdExceptionAgent
from src.core.confidence import compute_confidence
from src.core.orrick_validation import validate_extraction_against_orrick
from src.core.jurisdiction_check import (
    JurisdictionMismatch,
    validate_extraction_jurisdiction,
)
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Passages shorter than this are legislative boilerplate (headers, stubs, etc.)
MIN_PASSAGE_LENGTH = 150

# Circuit breaker: abort extraction if this many consecutive agent calls fail.
# Prevents silently skipping data when Ollama/GPU is down.
CIRCUIT_BREAKER_THRESHOLD = 3

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

# ---------------------------------------------------------------------------
# Keyword patterns for selective agent routing
# ---------------------------------------------------------------------------

_OBLIGATION_PATTERN = re.compile(
    r"\b(shall|must|may\s+not|prohibited|required|require|obligat)"
    r"\b",
    re.IGNORECASE,
)

_THRESHOLD_EXCEPTION_PATTERN = re.compile(
    r"(\b(unless|except|exempt|exclusion|carve.?out|safe.?harbor"
    r"|if\b|within\b|more\s+than|less\s+than|at\s+least|exceed|threshold)"
    r"\b|\d)",
    re.IGNORECASE,
)

_DEFINITION_ACTOR_PATTERN = re.compile(
    r'\b(means|defined\s+as|shall\s+mean|includes|"[A-Z][^"]{2,}"'
    r"|refers\s+to|the\s+term)\b",
    re.IGNORECASE,
)


class CircuitBreakerTripped(RuntimeError):
    """Raised when too many consecutive agent calls fail.

    Signals that the AI backend (Ollama, Anthropic API, etc.) is likely
    down or misconfigured. The pipeline should abort so the operator can
    check GPU/model status rather than silently skipping all remaining data.
    """


@dataclass
class TokenUsageSummary:
    """Aggregate token usage across an extraction run."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_calls: int = 0
    skipped_short: int = 0
    merged_passages: int = 0
    agents_skipped: int = 0

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_calls += 1


@dataclass
class MergedPassage:
    """A passage that may combine multiple short adjacent records."""

    text: str
    source_records: list[NormalizedSourceRecord] = field(default_factory=list)

    @property
    def primary_record(self) -> NormalizedSourceRecord:
        return self.source_records[0]


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
    """Build context dict for an extraction agent.

    Includes Orrick key_requirements and enforcement metadata when available,
    giving the model richer signal about what the passage is about.
    """
    dv = record.document_version
    df = dv.family if dv else None
    s = df.source if df else None
    ctx: dict[str, Any] = {
        "document_title": df.canonical_title if df else None,
        "jurisdiction": s.jurisdiction_code if s else None,
        "section_path": record.section_path,
    }

    # Surface reference URLs so agents can cite authoritative sources
    if df:
        if df.primary_source_url:
            ctx["primary_source_url"] = df.primary_source_url
        if df.orrick_reference_url:
            ctx["orrick_reference_url"] = df.orrick_reference_url
        if df.iapp_reference_url:
            ctx["iapp_reference_url"] = df.iapp_reference_url

    # Inject Orrick tracker metadata as context when available
    if df and df.metadata_:
        key_reqs = df.metadata_.get("key_requirements")
        if key_reqs:
            ctx["key_requirements"] = key_reqs
        enforcement = df.metadata_.get("enforcement")
        if enforcement:
            ctx["enforcement_summary"] = enforcement

    return ctx


def _check_jurisdiction(db, record: NormalizedSourceRecord, passage_text: str) -> bool:
    """Run jurisdiction cross-check before extraction.

    Returns True if the passage passes validation, False if it should be skipped.
    Logs a warning on mismatch rather than raising, so the pipeline continues
    processing other passages.
    """
    dv = record.document_version
    if not dv or not dv.family or not dv.family.source:
        return True  # No metadata to check against

    source = dv.family.source
    expected_jurisdiction = source.jurisdiction_code

    try:
        result = validate_extraction_jurisdiction(
            expected_jurisdiction=expected_jurisdiction,
            source_jurisdiction=expected_jurisdiction,  # same for metadata
            passage_text=passage_text,
            document_family_id=dv.family.id,
            strict=False,
        )
        if not result["valid"]:
            logger.warning(
                "extraction_skipped_jurisdiction_mismatch",
                record_id=record.id,
                family_id=dv.family.id,
                expected=result["expected"],
                detected=result["detected"],
                method=result["method"],
                reason=result["reason"],
            )
            return False
        return True
    except Exception as e:
        logger.error("jurisdiction_check_error", record_id=record.id, error=str(e))
        return True  # Fail open — don't block extraction on check errors


def _content_hash(agent_name: str, text: str) -> str:
    """Compute a deduplication hash for (agent, passage_text)."""
    return hashlib.sha256(f"{agent_name}:{text}".encode()).hexdigest()[:24]


def _select_agents_for_passage(
    text: str, all_agents: dict[str, BaseExtractionAgent]
) -> dict[str, BaseExtractionAgent]:
    """Select which agents to run based on passage content signals.

    Keyword pre-screening avoids wasting API calls on passages that
    are unlikely to contain content relevant to a specific agent.
    Ambiguity always runs (catches vague terms even in short passages).
    """
    selected: dict[str, BaseExtractionAgent] = {}

    # Ambiguity always runs
    if "ambiguity" in all_agents:
        selected["ambiguity"] = all_agents["ambiguity"]

    # Obligation: needs modal verbs
    if "obligation" in all_agents and _OBLIGATION_PATTERN.search(text):
        selected["obligation"] = all_agents["obligation"]

    # Threshold/exception: needs numbers, dates, or conditional language
    if "threshold_exception" in all_agents and _THRESHOLD_EXCEPTION_PATTERN.search(text):
        selected["threshold_exception"] = all_agents["threshold_exception"]

    # Definition/actor: needs definitional language
    if "definition_actor" in all_agents and _DEFINITION_ACTOR_PATTERN.search(text):
        selected["definition_actor"] = all_agents["definition_actor"]

    return selected


def _wrap_passages(
    records: list[NormalizedSourceRecord],
) -> list[MergedPassage]:
    """Wrap each record into a single-record MergedPassage (no merging).

    Passage merging was a cost-optimization for cloud API calls.  Running
    locally on dedicated hardware (e.g. R9700 + Ollama) makes merging
    unnecessary, and disabling it eliminates two classes of bugs:
      - Evidence span char-offsets becoming invalid after concatenation
      - N×M extraction duplication when multi-extraction agents run on
        merged passages with multiple source_records

    Records are still sorted by (document_version_id, ordinal) so agents
    process passages in document order.
    """
    if not records:
        return []

    sorted_records = sorted(records, key=lambda r: (r.document_version_id, r.ordinal))
    return [
        MergedPassage(text=r.text_content, source_records=[r])
        for r in sorted_records
    ]


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


def _group_agents_by_model(
    agents: dict[str, BaseExtractionAgent],
) -> list[dict[str, BaseExtractionAgent]]:
    """Group agents by model_override to minimise VRAM model swaps.

    Returns a list of dicts (one per model group), ordered so agents sharing
    the same model run together. Agents within a group can run concurrently;
    groups run sequentially so Ollama only loads one model at a time.
    """
    groups: dict[str | None, dict[str, BaseExtractionAgent]] = {}
    for name, agent in agents.items():
        key = agent.model_override
        groups.setdefault(key, {})[name] = agent
    return list(groups.values())


def extract_single_record(
    db,
    passage: MergedPassage,
    agents: dict[str, BaseExtractionAgent],
    extraction_job: ExtractionJob | None = None,
    parse_quality: float | None = None,
    token_usage: TokenUsageSummary | None = None,
    existing_hashes: set[str] | None = None,
    consecutive_failures: list[int] | None = None,
) -> int:
    """Run selected agents against a passage.

    Returns extraction count. Agents are selected based on content signals.

    To avoid VRAM thrashing when using local models via Ollama, agents are
    grouped by their model_override and each group runs sequentially.  Agents
    within the same model group still run concurrently.

    Deduplication is based on a content hash of (agent_name, passage_text).

    Args:
        consecutive_failures: Single-element list used as a mutable counter
            for consecutive agent failures across calls.  When the count
            reaches CIRCUIT_BREAKER_THRESHOLD, a CircuitBreakerTripped
            exception is raised so the caller can abort the run.
    """
    record = passage.primary_record
    ctx = _build_context(db, record)
    extractions_created = 0

    # Jurisdiction cross-check: skip if document state doesn't match law state
    if not _check_jurisdiction(db, record, passage.text):
        return 0

    # Select agents based on passage content
    selected_agents = _select_agents_for_passage(passage.text, agents)

    if token_usage is not None:
        token_usage.agents_skipped += len(agents) - len(selected_agents)

    if not selected_agents:
        logger.debug("all_agents_skipped", record_id=record.id)
        return 0

    # Group agents by model to minimise Ollama VRAM model swaps.
    # Each group runs sequentially; agents within a group run concurrently.
    model_groups = _group_agents_by_model(selected_agents)
    agent_results: list[tuple[str, str, ExtractionResult | Exception]] = []

    for group in model_groups:
        with ThreadPoolExecutor(max_workers=len(group)) as executor:
            futures = {}
            for agent_name, agent in group.items():
                # Deduplication guard
                content_hash = _content_hash(agent_name, passage.text)
                if existing_hashes is not None and content_hash in existing_hashes:
                    logger.debug(
                        "extraction_deduplicated",
                        agent=agent_name,
                        record_id=record.id,
                    )
                    continue

                future = executor.submit(
                    _run_agent, agent_name, agent, passage.text, ctx
                )
                futures[future] = (agent_name, content_hash)

            # Collect results for this model group
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
            # Circuit breaker: track consecutive failures
            if consecutive_failures is not None:
                consecutive_failures[0] += 1
                if consecutive_failures[0] >= CIRCUIT_BREAKER_THRESHOLD:
                    raise CircuitBreakerTripped(
                        f"Aborting extraction: {consecutive_failures[0]} consecutive "
                        f"agent failures. Last error from '{name}': {result}. "
                        f"Check your Ollama/GPU status."
                    )
            continue

        # Successful call — reset consecutive failure counter
        if consecutive_failures is not None:
            consecutive_failures[0] = 0

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

        # Write extractions against all source records in the merged passage
        for source_record in passage.source_records:
            for item in result.extractions:
                try:
                    evidence = item.get("evidence_spans", [])
                    orrick_sim = validate_extraction_against_orrick(item, ctx)
                    confidence = compute_confidence(
                        schema_valid=True,
                        evidence_spans=evidence,
                        extraction_payload=item,
                        schema_class=schema_class,
                        parse_quality_score=parse_quality,
                        orrick_similarity=orrick_sim,
                    )

                    extraction = Extraction(
                        source_record_id=source_record.id,
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
                        record_id=source_record.id,
                        confidence_score=confidence.total_score,
                        confidence_tier=confidence.tier,
                        evidence_verified=sum(1 for e in evidence if e.get("verified")),
                        evidence_total=len(evidence),
                        orrick_alignment=confidence.orrick_alignment,
                        orrick_matched_tokens=confidence.orrick_matched_tokens[:5],
                    )

                except Exception as e:
                    logger.error(
                        "extraction_record_failed",
                        agent=name,
                        record_id=source_record.id,
                        error=str(e),
                    )

    return extractions_created


def run_extraction(
    db,
    limit: int | None = None,
    on_progress: callable | None = None,
    batch_mode: bool = False,
) -> dict:
    """Run extraction agents against all unprocessed passages.

    Args:
        db: SQLAlchemy session
        limit: Max passages to process (None = all unprocessed)
        on_progress: Optional callback(message: str) for status updates
        batch_mode: If True, submit requests via Anthropic Batch API
                    (50% discount, results within 24h)

    Returns:
        Summary dict with counts and token usage.
    """
    if batch_mode:
        return _run_batch_extraction(db, limit=limit, on_progress=on_progress)

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

    summary: dict[str, Any] = {
        "total_records": len(records),
        "total_extractions": 0,
        "records_processed": 0,
        "records_failed": 0,
        "records_skipped_short": 0,
        "passages_merged": 0,
        "agents_skipped_by_signal": 0,
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

    # Filter out tiny passages
    original_count = len(records)
    records = [r for r in records if len(r.text_content) >= MIN_PASSAGE_LENGTH]
    skipped_short = original_count - len(records)
    summary["records_skipped_short"] = skipped_short
    token_usage.skipped_short = skipped_short

    if skipped_short:
        _log(f"Skipped {skipped_short} passages under {MIN_PASSAGE_LENGTH} chars")

    # Group records by document_version for ExtractionJob tracking
    dv_records: dict[int, list[NormalizedSourceRecord]] = {}
    for record in records:
        dv_id = record.document_version_id
        dv_records.setdefault(dv_id, []).append(record)

    _log(f"Spanning {len(dv_records)} document versions")

    # Circuit breaker: mutable counter shared across all passages.
    # A single-element list so extract_single_record can mutate it.
    consecutive_failures: list[int] = [0]

    for dv_id, dv_group in dv_records.items():
        merged_passages = _wrap_passages(dv_group)

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

        _log(
            f"\n[{label}] Processing {len(merged_passages)} passages "
            f"({len(dv_group)} records)..."
        )

        job_extractions = 0
        job_failures = 0

        for i, passage in enumerate(merged_passages):
            try:
                count = extract_single_record(
                    db, passage, agents, extraction_job, parse_quality,
                    token_usage, existing_hashes, consecutive_failures,
                )
                job_extractions += count
                extraction_job.records_processed += len(passage.source_records)
                summary["records_processed"] += len(passage.source_records)

                # Commit in batches of 10 to avoid holding huge transactions
                if (i + 1) % 10 == 0:
                    db.commit()
                    _log(f"  {i + 1}/{len(merged_passages)} passages processed...")

            except CircuitBreakerTripped:
                # Commit what we have so far, then abort the entire run
                extraction_job.status = "failed"
                extraction_job.completed_at = datetime.utcnow()
                db.commit()
                summary["total_extractions"] += job_extractions
                summary["circuit_breaker_tripped"] = True
                _log(
                    f"\n*** CIRCUIT BREAKER TRIPPED ***\n"
                    f"  {consecutive_failures[0]} consecutive agent failures.\n"
                    f"  Pipeline aborted. Check Ollama/GPU status before retrying.\n"
                    f"  Partial results committed: {job_extractions} extractions."
                )
                return summary

            except Exception as e:
                job_failures += 1
                extraction_job.records_failed += len(passage.source_records)
                summary["records_failed"] += len(passage.source_records)
                logger.error(
                    "record_extraction_error",
                    record_id=passage.primary_record.id,
                    error=str(e),
                )

        # Finalize extraction job
        extraction_job.status = "completed" if job_failures == 0 else "completed_with_errors"
        extraction_job.completed_at = datetime.utcnow()
        db.commit()

        summary["total_extractions"] += job_extractions
        _log(
            f"  Done: {job_extractions} extractions from {len(merged_passages)} passages "
            f"({job_failures} failures)"
        )

    summary["agents_skipped_by_signal"] = token_usage.agents_skipped

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
    _log(
        f"Savings: {token_usage.skipped_short} short passages skipped, "
        f"{token_usage.merged_passages} passages merged, "
        f"{token_usage.agents_skipped} agent calls avoided by signal filtering"
    )
    return summary


# ---------------------------------------------------------------------------
# Batch API support
# ---------------------------------------------------------------------------


def _run_batch_extraction(
    db,
    limit: int | None = None,
    on_progress: callable | None = None,
) -> dict:
    """Submit extraction requests via Anthropic Batch API (50% cost discount).

    Collects all (passage, agent, prompt) combinations, submits them as a
    single batch, and returns a summary with the batch ID for later retrieval.

    Results are available within 24 hours. Use `retrieve_batch_results()`
    to process completed batches.

    Raises:
        ValueError: If extraction_provider is not "anthropic".
    """
    from src.core.config import settings

    if settings.extraction_provider != "anthropic":
        raise ValueError(
            f"Batch mode requires extraction_provider='anthropic', "
            f"but got '{settings.extraction_provider}'. "
            f"The Anthropic Batch API is not available for local models."
        )

    import anthropic

    from src.agents.base import BaseExtractionAgent

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    agents = _get_agents()

    # Find passages without extractions
    query = (
        select(NormalizedSourceRecord)
        .outerjoin(Extraction)
        .where(Extraction.id.is_(None))
    )
    if limit:
        query = query.limit(limit)

    records = db.scalars(query).all()

    # Filter short passages
    records = [r for r in records if len(r.text_content) >= MIN_PASSAGE_LENGTH]

    if not records:
        _log("No unprocessed passages found.")
        return {"batch_id": None, "requests_submitted": 0}

    # Merge short passages
    dv_records: dict[int, list[NormalizedSourceRecord]] = {}
    for record in records:
        dv_records.setdefault(record.document_version_id, []).append(record)

    # Build batch requests
    batch_requests = []
    for dv_id, dv_group in dv_records.items():
        merged_passages = _wrap_passages(dv_group)

        for passage in merged_passages:
            record = passage.primary_record
            ctx = _build_context(db, record)
            selected = _select_agents_for_passage(passage.text, agents)

            for agent_name, agent in selected.items():
                prompt = agent._resolve_extraction_prompt(passage.text, ctx)
                system_prompt = agent._resolve_system_prompt()
                system_prompt += (
                    "\n\nReturn only raw JSON with no markdown formatting, "
                    "no code fences, and no preamble."
                )

                # Custom ID encodes record_id + agent for result matching.
                # Batch API only allows [a-zA-Z0-9_-], max 64 chars.
                # Use "--" as an unambiguous delimiter: record IDs use
                # single "-", and agent names never contain "--".
                record_ids = "-".join(str(r.id) for r in passage.source_records)
                custom_id = f"{record_ids}--{agent_name}"[:64]

                batch_requests.append({
                    "custom_id": custom_id,
                    "params": {
                        "model": settings.extraction_model,
                        "max_tokens": settings.extraction_max_tokens,
                        "temperature": settings.extraction_temperature,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                })

    if not batch_requests:
        _log("No batch requests to submit (all filtered/deduplicated).")
        return {"batch_id": None, "requests_submitted": 0}

    _log(f"Submitting {len(batch_requests)} requests to Batch API...")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    batch = client.messages.batches.create(requests=batch_requests)

    _log(
        f"Batch submitted: {batch.id}\n"
        f"  Requests: {len(batch_requests)}\n"
        f"  Status: {batch.processing_status}\n"
        f"  Results will be available within 24 hours.\n"
        f"  Retrieve with: --mode batch-results --batch-id {batch.id}"
    )

    return {
        "batch_id": batch.id,
        "requests_submitted": len(batch_requests),
        "status": batch.processing_status,
    }


def run_recovery_extraction(
    db,
    limit: int | None = None,
    on_progress: callable | None = None,
) -> dict:
    """Re-extract passages that have partial results (some agents succeeded, others failed).

    The original batch run dropped ~637 extractions due to Pydantic validation bugs
    (int threshold_value, bare-string responsibilities). Those bugs are now fixed,
    but the affected passages already have *some* extractions so the normal
    ``run_extraction()`` query (``WHERE Extraction.id IS NULL``) skips them.

    This function:
      1. Finds passages with at least one extraction
      2. Determines which agents SHOULD have run (keyword screening)
      3. Checks which extraction types already exist
      4. Re-runs only the missing agents

    Args:
        db: SQLAlchemy session
        limit: Max passages to process (None = all)
        on_progress: Optional callback for status messages

    Returns:
        Summary dict with counts.
    """
    from sqlalchemy import distinct, func as sqlfunc

    agents = _get_agents()
    token_usage = TokenUsageSummary()

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    # Step 1: Find all passages that HAVE extractions
    records_with_extractions = (
        db.execute(
            select(
                NormalizedSourceRecord.id,
                sqlfunc.array_agg(distinct(Extraction.extraction_type)),
            )
            .join(Extraction, Extraction.source_record_id == NormalizedSourceRecord.id)
            .group_by(NormalizedSourceRecord.id)
        )
        .all()
    )

    if not records_with_extractions:
        _log("No passages with existing extractions found.")
        return {"total_checked": 0, "gaps_found": 0, "extractions_created": 0}

    _log(f"Checking {len(records_with_extractions)} passages for missing agent results...")

    # Step 2: For each passage, determine gaps
    gaps: list[tuple[int, list[str]]] = []  # (record_id, [missing_agent_names])

    for record_id, existing_types_raw in records_with_extractions:
        # existing_types_raw is an array of ExtractionType enum values
        existing_types = set()
        for t in existing_types_raw:
            if isinstance(t, ExtractionType):
                existing_types.add(t)
            else:
                try:
                    existing_types.add(ExtractionType(t))
                except ValueError:
                    pass

        # Load the record to check keyword signals
        record = db.get(NormalizedSourceRecord, record_id)
        if not record or len(record.text_content) < MIN_PASSAGE_LENGTH:
            continue

        # Which agents should have run?
        expected_agents = _select_agents_for_passage(record.text_content, agents)

        # Which agents' extraction types are missing?
        missing_agents = []
        for agent_name in expected_agents:
            agent_types = AGENT_EXTRACTION_TYPES[agent_name]
            # If NONE of this agent's types exist, the agent didn't produce results
            if not any(t in existing_types for t in agent_types):
                missing_agents.append(agent_name)

        if missing_agents:
            gaps.append((record_id, missing_agents))

    if not gaps:
        _log("No gaps found — all passages have complete extraction coverage.")
        return {
            "total_checked": len(records_with_extractions),
            "gaps_found": 0,
            "extractions_created": 0,
        }

    if limit:
        gaps = gaps[:limit]

    _log(f"Found {len(gaps)} passages with missing agent results. Re-extracting...")

    # Count missing agents by type for reporting
    agent_gap_counts: dict[str, int] = {}
    for _, missing in gaps:
        for agent_name in missing:
            agent_gap_counts[agent_name] = agent_gap_counts.get(agent_name, 0) + 1
    for agent_name, count in sorted(agent_gap_counts.items()):
        _log(f"  {agent_name}: {count} passages missing")

    # Step 3: Re-run missing agents
    total_extractions = 0
    errors = 0
    existing_hashes: set[str] = set()

    for i, (record_id, missing_agents) in enumerate(gaps):
        record = db.get(NormalizedSourceRecord, record_id)
        if not record:
            continue

        ctx = _build_context(db, record)
        passage = MergedPassage(text=record.text_content, source_records=[record])

        # Only run the missing agents
        selected = {name: agents[name] for name in missing_agents if name in agents}

        # Get parse quality
        ingestion_job = db.scalars(
            select(IngestionJob).where(
                IngestionJob.document_version_id == record.document_version_id
            )
        ).first()
        parse_quality = ingestion_job.parse_quality_score if ingestion_job else None

        for agent_name, agent in selected.items():
            content_hash = _content_hash(agent_name, record.text_content)
            if content_hash in existing_hashes:
                continue

            try:
                result = agent.extract(record.text_content, ctx)

                if token_usage is not None:
                    token_usage.add(result.input_tokens, result.output_tokens)

                if result.abstention is not None:
                    continue

                existing_hashes.add(content_hash)
                primary_type = AGENT_EXTRACTION_TYPES[agent_name][0]
                schema_class = EXTRACTION_TYPE_SCHEMAS.get(primary_type.value)

                for item in result.extractions:
                    try:
                        evidence = item.get("evidence_spans", [])
                        orrick_sim = validate_extraction_against_orrick(item, ctx)
                        confidence = compute_confidence(
                            schema_valid=True,
                            evidence_spans=evidence,
                            extraction_payload=item,
                            schema_class=schema_class,
                            parse_quality_score=parse_quality,
                            orrick_similarity=orrick_sim,
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
                        )
                        db.add(extraction)
                        db.flush()

                        db.add(ReviewQueueItem(
                            extraction_id=extraction.id,
                            priority=_confidence_to_priority(confidence.tier),
                            status=ReviewStatus.pending,
                        ))
                        total_extractions += 1

                    except Exception as e:
                        logger.error(
                            "recovery_extraction_failed",
                            agent=agent_name,
                            record_id=record.id,
                            error=str(e),
                        )
                        errors += 1

            except Exception as e:
                logger.error(
                    "recovery_agent_error",
                    agent=agent_name,
                    record_id=record.id,
                    error=str(e),
                )
                errors += 1

        # Commit every 10 passages
        if (i + 1) % 10 == 0:
            db.commit()
            _log(f"  {i + 1}/{len(gaps)} passages processed...")

    db.commit()

    _log(
        f"\nRecovery complete:"
        f"\n  Passages checked:    {len(records_with_extractions)}"
        f"\n  Gaps found:          {len(gaps)}"
        f"\n  Extractions created: {total_extractions}"
        f"\n  Errors:              {errors}"
        f"\n  Token usage:         {token_usage.total_tokens:,} tokens across {token_usage.total_calls} calls"
    )

    return {
        "total_checked": len(records_with_extractions),
        "gaps_found": len(gaps),
        "extractions_created": total_extractions,
        "errors": errors,
        "token_usage": {
            "input_tokens": token_usage.total_input_tokens,
            "output_tokens": token_usage.total_output_tokens,
            "total_tokens": token_usage.total_tokens,
            "total_calls": token_usage.total_calls,
        },
    }


def retrieve_batch_results(
    db,
    batch_id: str,
    on_progress: callable | None = None,
) -> dict:
    """Retrieve and process results from a completed Anthropic Batch API run.

    Parses each result's custom_id (format: "recordId1-recordId2_agentName")
    to match results back to source records, then runs the same validation
    and confidence scoring pipeline as the synchronous path.

    Args:
        db: SQLAlchemy session
        batch_id: Anthropic batch ID (e.g. "msgbatch_01VGYkKdLkMjsacQdLVRBnfv")
        on_progress: Optional callback(message: str) for status updates

    Returns:
        Summary dict with counts.

    Raises:
        ValueError: If extraction_provider is not "anthropic".
    """
    from src.core.config import settings

    if settings.extraction_provider != "anthropic":
        raise ValueError(
            f"Batch result retrieval requires extraction_provider='anthropic', "
            f"but got '{settings.extraction_provider}'. "
            f"The Anthropic Batch API is not available for local models."
        )

    import json

    import anthropic

    from src.agents.base import BaseExtractionAgent

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    # Check batch status
    batch = client.messages.batches.retrieve(batch_id)
    _log(f"Batch {batch_id}: status={batch.processing_status}")

    if batch.processing_status != "ended":
        _log(
            f"Batch not yet complete. Status: {batch.processing_status}\n"
            f"  Try again later."
        )
        return {
            "batch_id": batch_id,
            "status": batch.processing_status,
            "extractions_created": 0,
            "results_processed": 0,
            "errors": 0,
        }

    # Retrieve results
    _log("Retrieving batch results...")
    results_iter = client.messages.batches.results(batch_id)

    agents = _get_agents()
    total_extractions = 0
    results_processed = 0
    errors = 0

    for entry in results_iter:
        results_processed += 1
        custom_id = entry.custom_id

        # Parse custom_id: "recordId1-recordId2--agentName"
        # The "--" delimiter is unambiguous: record IDs use single "-"
        # and agent names never contain "--".
        # Fall back to legacy "_"-based parsing for old batches.
        if "--" in custom_id:
            record_ids_str, _, agent_name = custom_id.partition("--")
        else:
            # Legacy format: "recordId1-recordId2_agentName"
            last_underscore = custom_id.rfind("_")
            if last_underscore == -1:
                logger.error("batch_invalid_custom_id", custom_id=custom_id)
                errors += 1
                continue
            record_ids_str = custom_id[:last_underscore]
            agent_name = custom_id[last_underscore + 1:]
            # Handle compound agent names (e.g., "threshold_exception")
            if agent_name not in AGENT_EXTRACTION_TYPES:
                prefix = record_ids_str
                suffix = agent_name
                last2 = prefix.rfind("_")
                if last2 != -1:
                    candidate = prefix[last2 + 1:] + "_" + suffix
                    if candidate in AGENT_EXTRACTION_TYPES:
                        agent_name = candidate
                        record_ids_str = prefix[:last2]

        if agent_name not in AGENT_EXTRACTION_TYPES:
            logger.error(
                "batch_unknown_agent",
                custom_id=custom_id,
                parsed_agent=agent_name,
            )
            errors += 1
            continue

        # Parse record IDs
        try:
            record_ids = [int(rid) for rid in record_ids_str.split("-") if rid]
        except ValueError:
            logger.error("batch_invalid_record_ids", custom_id=custom_id)
            errors += 1
            continue

        # Check result type
        if entry.result.type == "errored":
            logger.error(
                "batch_result_error",
                custom_id=custom_id,
                error=str(entry.result.error),
            )
            errors += 1
            continue

        if entry.result.type != "succeeded":
            logger.warning(
                "batch_result_skipped",
                custom_id=custom_id,
                result_type=entry.result.type,
            )
            errors += 1
            continue

        # Extract text from response
        message = entry.result.message
        raw_text = ""
        for block in message.content:
            if block.type == "text":
                raw_text = block.text
                break

        if not raw_text.strip():
            logger.warning("batch_empty_response", custom_id=custom_id)
            errors += 1
            continue

        # Parse JSON (with code fence stripping)
        cleaned = BaseExtractionAgent._strip_code_fences(raw_text)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(
                "batch_json_parse_error",
                custom_id=custom_id,
                error=str(e),
                raw_preview=raw_text[:200],
            )
            errors += 1
            continue

        # Check for abstention
        if parsed.get("detected") is False:
            logger.debug("batch_abstention", custom_id=custom_id)
            continue

        # Get agent and schema
        agent = agents.get(agent_name)
        if not agent:
            logger.error("batch_agent_not_found", agent_name=agent_name)
            errors += 1
            continue

        schema = agent.get_output_schema()
        primary_type = AGENT_EXTRACTION_TYPES[agent_name][0]
        schema_class = EXTRACTION_TYPE_SCHEMAS.get(primary_type.value)

        # Handle multi-extraction
        items = parsed.get("extractions", [parsed])
        if not isinstance(items, list):
            items = [items]

        # Load source records
        source_records = db.scalars(
            select(NormalizedSourceRecord).where(
                NormalizedSourceRecord.id.in_(record_ids)
            )
        ).all()

        if not source_records:
            logger.error("batch_records_not_found", record_ids=record_ids)
            errors += 1
            continue

        # Get parse quality for confidence scoring
        first_record = source_records[0]
        ingestion_job = db.scalars(
            select(IngestionJob).where(
                IngestionJob.document_version_id == first_record.document_version_id
            )
        ).first()
        parse_quality = ingestion_job.parse_quality_score if ingestion_job else None

        # Reconstruct passage text for evidence verification
        passage_text = "\n".join(r.text_content for r in source_records)

        # Build context for Orrick similarity validation
        batch_ctx = _build_context(db, first_record)

        for item in items:
            try:
                validated = schema.model_validate(item)
                evidence_spans = item.get("evidence_spans", [])
                verified_spans = agent._verify_evidence_spans(evidence_spans, passage_text)

                result_dict = validated.model_dump(by_alias=True)
                result_dict["evidence_spans"] = verified_spans
                result_dict["_model_id"] = settings.extraction_model
                result_dict["_batch_id"] = batch_id

                orrick_sim = validate_extraction_against_orrick(result_dict, batch_ctx)
                confidence = compute_confidence(
                    schema_valid=True,
                    evidence_spans=verified_spans,
                    extraction_payload=result_dict,
                    schema_class=schema_class,
                    parse_quality_score=parse_quality,
                    orrick_similarity=orrick_sim,
                )

                # Write extraction for each source record
                for source_record in source_records:
                    extraction = Extraction(
                        source_record_id=source_record.id,
                        extraction_type=primary_type,
                        payload=result_dict,
                        evidence_spans=verified_spans,
                        confidence_score=confidence.total_score,
                        confidence_tier=ConfidenceTier(confidence.tier),
                        review_status=ReviewStatus.pending,
                        model_id=settings.extraction_model,
                    )
                    db.add(extraction)
                    db.flush()

                    db.add(ReviewQueueItem(
                        extraction_id=extraction.id,
                        priority=_confidence_to_priority(confidence.tier),
                        status=ReviewStatus.pending,
                    ))
                    total_extractions += 1

            except (json.JSONDecodeError, ValidationError) as e:
                logger.error(
                    "batch_validation_error",
                    custom_id=custom_id,
                    error=str(e),
                )
                errors += 1

        # Commit periodically
        if results_processed % 50 == 0:
            db.commit()
            _log(f"  {results_processed} results processed...")

    db.commit()

    _log(
        f"\nBatch results processed:"
        f"\n  Results:     {results_processed}"
        f"\n  Extractions: {total_extractions}"
        f"\n  Errors:      {errors}"
    )

    return {
        "batch_id": batch_id,
        "status": "processed",
        "results_processed": results_processed,
        "extractions_created": total_extractions,
        "errors": errors,
    }
