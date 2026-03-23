"""Extraction pipeline — run AI agents against normalized passages.

Shared logic used by both:
  - Dagster extracted_obligations asset
  - CLI: python -m src.scripts.seed_pipeline --mode extract

Steps:
  1. Query NormalizedSourceRecords without extractions (or with pending ExtractionJob)
  2. Filter out tiny passages (<150 chars) and merge adjacent short fragments
  2b. TRIAGE: Run section-level AI-relevance filter (keyword + Orrick + LLM)
      - Passages marked "not_relevant" skip the agent battery
      - Passages marked "uncertain" proceed (conservative)
      - All decisions stored in section_triage_results for review
  3. Select agents per passage via negative screening (exclude only boilerplate)
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
  - Negative screening excludes agents only for definitively irrelevant passages
  - Orrick key_requirements injected as extraction context for higher accuracy
  - Batch API support via --batch flag (50% discount, 24h turnaround)
"""

from __future__ import annotations

import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import select

from src.agents.ambiguity import AmbiguityAgent
from src.agents.base import BaseExtractionAgent, ExtractionResult
from src.agents.compliance_mechanism import ComplianceMechanismAgent
from src.agents.definition_actor import DefinitionActorAgent
from src.agents.obligation import ObligationAgent
from src.agents.rights_protection import RightsProtectionAgent
from src.agents.threshold_exception import ThresholdExceptionAgent
from src.core.circuit_breaker import CircuitBreakerTripped, FailureTracker
from src.core.confidence import compute_confidence
from src.core.orrick_validation import validate_extraction_against_orrick
from src.core.jurisdiction_check import (
    JurisdictionMismatch,
    validate_extraction_jurisdiction,
)
from src.db.models import (
    ConfidenceTier,
    DocumentVersion,
    Extraction,
    ExtractionJob,
    ExtractionType,
    IngestionJob,
    NormalizedSourceRecord,
    ObligationDependency,
    ReviewQueueItem,
    ReviewStatus,
    SectionTriageResult,
    TriageDecision,
    TriageMethod,
)
from src.schemas.extraction import EXTRACTION_TYPE_SCHEMAS

logger = structlog.get_logger()

# Global cancellation event — set to signal running extraction to stop.
_cancel_event = threading.Event()


def request_cancel() -> None:
    """Signal the running extraction pipeline to stop after the current passage."""
    _cancel_event.set()


def is_cancelled() -> bool:
    """Check whether cancellation has been requested."""
    return _cancel_event.is_set()


def clear_cancel() -> None:
    """Reset the cancellation flag (called at extraction start)."""
    _cancel_event.clear()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Passages shorter than this are legislative boilerplate (headers, stubs, etc.)
MIN_PASSAGE_LENGTH = 150

# Circuit breaker: abort extraction if this many consecutive agent calls fail.
# Prevents silently skipping data when LM Studio/GPU is down.
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
    "rights_protection": [ExtractionType.rights_protection],
    "compliance_mechanism": [ExtractionType.compliance_mechanism],
}


def _discriminate_extraction_type(
    agent_name: str, payload: dict[str, Any]
) -> ExtractionType:
    """Determine the specific extraction sub-type from the payload content.

    Consolidated agents produce multiple sub-types but the pipeline
    previously always tagged with the primary type (index [0]).  This
    function inspects the payload to choose the most specific type when
    the extraction is primarily about a sub-type.

    Rules:
      - obligation agent:
          * ``enforcement`` if enforcing_body or penalty populated but
            subject/action are absent or generic
          * ``timeline`` if effective_date or compliance_deadline
            populated but subject/action are absent or generic
          * ``obligation`` (default)
      - definition_actor agent:
          * ``actor_mapping`` if actors are the primary content and
            term/definition_text are absent
          * ``framework_ref`` if framework_refs are the primary content
            and term/definition_text are absent
          * ``definition`` (default)
      - threshold_exception agent:
          * ``exception`` if no threshold data but has exceptions
          * ``threshold`` (default)
      - single-type agents: return their only type
    """
    types = AGENT_EXTRACTION_TYPES.get(agent_name)
    if not types or len(types) == 1:
        return types[0] if types else ExtractionType.obligation

    primary = types[0]

    # --- Obligation agent: obligation vs enforcement vs timeline ---
    if agent_name == "obligation":
        has_subject = bool(payload.get("subject", "").strip())
        has_action = bool(payload.get("action", "").strip())
        has_core_obligation = has_subject and has_action

        enf = payload.get("enforcement") or {}
        has_enforcement = bool(
            enf.get("enforcing_body") or enf.get("penalty_type") or enf.get("penalty_description")
        )

        tl = payload.get("timeline") or {}
        has_timeline = bool(
            tl.get("effective_date") or tl.get("compliance_deadline")
            or tl.get("sunset_date") or tl.get("phase_in_period")
        )

        if not has_core_obligation:
            if has_enforcement:
                return ExtractionType.enforcement
            if has_timeline:
                return ExtractionType.timeline

        return ExtractionType.obligation

    # --- Definition & Actor agent: definition vs actor_mapping vs framework_ref ---
    if agent_name == "definition_actor":
        has_term = bool(payload.get("term", "").strip())
        has_def_text = bool(payload.get("definition_text", "").strip())
        has_core_definition = has_term and has_def_text

        actors = payload.get("actors") or []
        framework_refs = payload.get("framework_refs") or []

        if not has_core_definition:
            if actors:
                return ExtractionType.actor_mapping
            if framework_refs:
                return ExtractionType.framework_ref

        return ExtractionType.definition

    # --- Threshold & Exception agent: threshold vs exception ---
    if agent_name == "threshold_exception":
        has_threshold = bool(
            payload.get("threshold_type") or payload.get("threshold_value")
            or payload.get("threshold_condition")
        )
        exceptions = payload.get("exceptions") or []

        if not has_threshold and exceptions:
            return ExtractionType.exception

        return ExtractionType.threshold

    return primary

# ---------------------------------------------------------------------------
# Keyword patterns for selective agent routing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Negative screening: passages matching these patterns are EXCLUDED from
# specific agents because they are definitively irrelevant.  All other
# passages run ALL agents (recall-safe approach).
#
# Previous approach used positive keyword matching (require "shall|must" for
# obligation agent, etc.) which silently missed obligations phrased in
# non-standard ways ("developers are expected to", "it is the policy of",
# "no person may").  For audit-grade work where false negatives are
# unacceptable, we invert the logic: run everything by default, only skip
# when we're confident the passage can't contain relevant content.
# ---------------------------------------------------------------------------

# Passages that are purely structural / procedural boilerplate.
# These never contain substantive legal content for ANY agent.
_BOILERPLATE_PATTERN = re.compile(
    r"^\s*("
    r"table\s+of\s+contents"
    r"|chapter\s+\d+"
    r"|part\s+\d+\s*[-—]\s*$"
    r"|article\s+\d+\s*$"
    r"|_{5,}"  # separator lines
    r"|\.{5,}"  # dot leaders (TOC)
    r"|page\s+\d+"
    r")\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Enacting / procedural clauses that contain no obligations, definitions,
# thresholds, rights, or compliance mechanisms.
_ENACTING_CLAUSE_PATTERN = re.compile(
    r"^\s*(be\s+it\s+enacted|the\s+people\s+of\s+the\s+state\s+of"
    r"|this\s+act\s+(shall\s+be\s+known\s+as|may\s+be\s+cited\s+as)"
    r"|approved\s+(by\s+the\s+governor|on)"
    r"|signed\s+(by\s+the\s+governor|into\s+law)"
    r"|effective\s+immediately)\b",
    re.IGNORECASE,
)

# Passages that are purely definitional structure (section headers like
# "DEFINITIONS" or "As used in this section:") — these are relevant ONLY
# to the definition_actor agent, not to obligation/threshold/rights/compliance.
_DEFINITIONS_SECTION_HEADER = re.compile(
    r"^\s*(definitions|as\s+used\s+in\s+this\s+(act|section|chapter|article|part))\s*[:.]?\s*$",
    re.IGNORECASE,
)

# Legacy positive-match patterns kept for optional "hint" mode and tests.
# These are NO LONGER used for agent selection in the default pipeline.
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

_RIGHTS_PROTECTION_PATTERN = re.compile(
    r"\b(right\s+to|entitled|opt.?out|appeal|contest|human\s+review"
    r"|notif(y|ied|ication)|informed|disclos(e|ure)|consent"
    r"|complain|remedy|recourse|delete|erasure|withdraw"
    r"|request\s+(that|a\b|an\b|the\b|review|explanation))\b",
    re.IGNORECASE,
)

_COMPLIANCE_MECHANISM_PATTERN = re.compile(
    r"\b(impact\s+assessment|bias\s+audit|algorithmic\s+audit"
    r"|risk\s+assessment|audit|register|certif(y|ication|ied)"
    r"|record.?keeping|maintain\s+(records|logs|documentation)"
    r"|report(ing)?\s+(to|requirement|annually|quarterly)"
    r"|annual(ly)?\s+report|filing|retain|retention"
    r"|third.?party\s+(audit|review|assessment)|self.?certif)\b",
    re.IGNORECASE,
)


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
            "rights_protection": RightsProtectionAgent(),
            "compliance_mechanism": ComplianceMechanismAgent(),
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

    # Include legislative status so agents know if bill is active, pending, etc.
    if dv and dv.temporal_status:
        status_val = dv.temporal_status.value if hasattr(dv.temporal_status, "value") else str(dv.temporal_status)
        ctx["legislative_status"] = status_val
    if dv and dv.effective_date:
        ctx["effective_date"] = str(dv.effective_date)

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
        bill_id = df.metadata_.get("bill_id")
        if bill_id:
            ctx["bill_id"] = bill_id
        key_reqs = df.metadata_.get("key_requirements")
        if key_reqs:
            ctx["key_requirements"] = key_reqs
        enforcement = df.metadata_.get("enforcement")
        if enforcement:
            ctx["enforcement_summary"] = enforcement
        ai_scope = df.metadata_.get("ai_scope")
        if ai_scope:
            ctx["ai_scope"] = ai_scope
        # IAPP-sourced fields (populated by cross-reference or status checker)
        iapp_bill = df.metadata_.get("iapp_bill_number")
        if iapp_bill:
            ctx["iapp_bill_number"] = iapp_bill
        iapp_status = df.metadata_.get("iapp_status")
        if iapp_status:
            ctx["iapp_status"] = iapp_status
        iapp_topic = df.metadata_.get("iapp_ai_topic")
        if iapp_topic:
            ctx["iapp_ai_topic"] = iapp_topic

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
    """Select which agents to run via negative screening (recall-safe).

    Runs ALL agents by default.  Only excludes agents when the passage is
    definitively irrelevant — boilerplate, enacting clauses, or structural
    headers.  This prevents false negatives from obligations phrased in
    non-standard ways (e.g., "developers are expected to", "it is the
    policy of this state that", "no person may deploy").

    For audit-grade work where missing an obligation is worse than a
    false positive, this approach prioritises recall over cost savings.
    The LLM agents will abstain (return detected: false) on irrelevant
    passages, so false positives are handled downstream.
    """
    text_stripped = text.strip()

    # If the entire passage is boilerplate (TOC, page numbers, separators),
    # skip ALL agents — no substantive content to extract.
    if _BOILERPLATE_PATTERN.fullmatch(text_stripped):
        return {}

    # If it's a pure enacting/signing clause, skip all agents.
    # These are procedural and never contain obligations or definitions.
    if _ENACTING_CLAUSE_PATTERN.match(text_stripped) and len(text_stripped) < 300:
        return {}

    # Start with all agents selected (recall-safe default)
    selected = dict(all_agents)

    # Definitions section headers ("DEFINITIONS", "As used in this act:")
    # are only useful for the definition_actor agent.  Other agents won't
    # find obligations, thresholds, rights, or compliance mechanisms in a
    # bare header line.
    if _DEFINITIONS_SECTION_HEADER.fullmatch(text_stripped):
        return {k: v for k, v in selected.items() if k == "definition_actor"}

    return selected


def _wrap_passages(
    records: list[NormalizedSourceRecord],
) -> list[MergedPassage]:
    """Wrap each record into a single-record MergedPassage (no merging).

    Passage merging was a cost-optimization for cloud API calls.  Running
    locally on dedicated hardware (e.g. R9700 + LM Studio) makes merging
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
    groups run sequentially so LM Studio only loads one model at a time.
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
    tracker: FailureTracker | None = None,
) -> int:
    """Run selected agents against a passage.

    Returns extraction count. Agents are selected based on content signals.

    To avoid VRAM thrashing when using local models via LM Studio, agents are
    grouped by their model_override and each group runs sequentially.  Agents
    within the same model group still run concurrently.

    Deduplication is based on a content hash of (agent_name, passage_text).

    Args:
        tracker: Shared FailureTracker that monitors consecutive and total
            failure rates across the full extraction run.  Raises
            CircuitBreakerTripped when thresholds are exceeded.
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

    # Group agents by model to minimise LM Studio VRAM model swaps.
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

    # Import monitor for live event emission
    from src.core.extraction_monitor import get_monitor
    monitor = get_monitor()

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
            if tracker is not None:
                tracker.record_failure(
                    f"agent={name} record={record.id}: {result}"
                )
            monitor.record_agent_result(
                agent_name=name,
                record_id=record.id,
                error=str(result),
            )
            continue

        # Successful call — reset consecutive failure counter
        if tracker is not None:
            tracker.record_success()

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
            monitor.record_agent_result(
                agent_name=name,
                record_id=record.id,
                abstained=True,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            continue

        # Mark hash as seen
        if existing_hashes is not None:
            existing_hashes.add(content_hash)

        # Process each extraction from the multi-extraction result
        default_type = AGENT_EXTRACTION_TYPES[name][0]
        schema_class = EXTRACTION_TYPE_SCHEMAS.get(default_type.value)

        # Write extractions against all source records in the merged passage
        for source_record in passage.source_records:
            for item in result.extractions:
                try:
                    resolved_type = _discriminate_extraction_type(name, item)
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

                    extraction_meta: dict = {}
                    if result.truncated:
                        extraction_meta["truncated"] = True

                    extraction = Extraction(
                        source_record_id=source_record.id,
                        extraction_type=resolved_type,
                        payload=item,
                        evidence_spans=evidence,
                        confidence_score=confidence.total_score,
                        confidence_tier=ConfidenceTier(confidence.tier),
                        review_status=ReviewStatus.pending,
                        prompt_template_version=result.prompt_hash,
                        prompt_hash=result.prompt_hash,
                        template_version=result.template_version,
                        model_id=result.model_id,
                        extraction_job_id=extraction_job.id if extraction_job else None,
                        metadata_=extraction_meta if extraction_meta else {},
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

                    # Emit to live monitor
                    monitor.record_agent_result(
                        agent_name=name,
                        record_id=source_record.id,
                        success=True,
                        extraction_count=1,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        confidence_tier=confidence.tier,
                        truncated=result.truncated,
                    )

                except Exception as e:
                    logger.error(
                        "extraction_record_failed",
                        agent=name,
                        record_id=source_record.id,
                        error=str(e),
                    )

    # Record passage-level completion to monitor
    monitor.record_passage_complete(
        record_id=record.id,
        section_path=record.section_path,
        extraction_count=extractions_created,
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

    # Clear any stale cancellation from a previous run
    clear_cancel()

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

    # Start the live extraction monitor
    from src.core.extraction_monitor import get_monitor
    _monitor = get_monitor()
    _monitor.start_run(total_passages=len(records))

    # Filter out tiny passages
    original_count = len(records)
    records = [r for r in records if len(r.text_content) >= MIN_PASSAGE_LENGTH]
    skipped_short = original_count - len(records)
    summary["records_skipped_short"] = skipped_short
    token_usage.skipped_short = skipped_short

    if skipped_short:
        _log(f"Skipped {skipped_short} passages under {MIN_PASSAGE_LENGTH} chars")

    # ---- Section Triage (Step 2b) ----
    # Run AI-relevance filtering before sending to the full agent battery.
    # Decisions are stored in section_triage_results for human review.
    from src.agents.section_triage import triage_passage

    triage_relevant = 0
    triage_skipped = 0
    triage_uncertain = 0
    triaged_records: list[NormalizedSourceRecord] = []

    for record in records:
        # Skip if already triaged (re-run safety)
        existing_triage = db.scalars(
            select(SectionTriageResult).where(
                SectionTriageResult.source_record_id == record.id
            )
        ).first()
        if existing_triage:
            if existing_triage.decision == TriageDecision.not_relevant:
                triage_skipped += 1
                continue
            triaged_records.append(record)
            if existing_triage.decision == TriageDecision.relevant:
                triage_relevant += 1
            else:
                triage_uncertain += 1
            continue

        ctx = _build_context(db, record)
        result = triage_passage(record.text_content, ctx, llm_provider=None)

        # Store triage result
        triage_row = SectionTriageResult(
            source_record_id=record.id,
            decision=TriageDecision(result.decision),
            method=TriageMethod(result.method),
            confidence=result.confidence,
            matched_keywords=result.matched_keywords,
            orrick_terms_checked=result.orrick_terms_checked,
            llm_reasoning=result.llm_reasoning,
            pdf_quality_score=result.pdf_quality_score,
            quality_flags=result.quality_flags,
            model_id=result.model_id,
        )
        db.add(triage_row)

        if result.decision == "not_relevant":
            triage_skipped += 1
        else:
            triaged_records.append(record)
            if result.decision == "relevant":
                triage_relevant += 1
            else:
                triage_uncertain += 1

    # Commit triage results
    db.commit()

    summary["triage_relevant"] = triage_relevant
    summary["triage_skipped"] = triage_skipped
    summary["triage_uncertain"] = triage_uncertain

    _log(
        f"Triage: {triage_relevant} relevant, {triage_uncertain} uncertain, "
        f"{triage_skipped} skipped (not AI-relevant)"
    )

    records = triaged_records
    if not records:
        _log("No AI-relevant passages found after triage.")
        return summary

    # Group records by document_version for ExtractionJob tracking
    dv_records: dict[int, list[NormalizedSourceRecord]] = {}
    for record in records:
        dv_id = record.document_version_id
        dv_records.setdefault(dv_id, []).append(record)

    _log(f"Spanning {len(dv_records)} document versions")

    # Circuit breaker: shared tracker across all passages / agent calls
    tracker = FailureTracker(
        context="extraction pipeline (agent calls)",
        max_consecutive=CIRCUIT_BREAKER_THRESHOLD,
        max_failure_rate=0.8,
        min_items_for_rate=20,
    )

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
        _monitor.record_document_start(label, len(merged_passages))

        job_extractions = 0
        job_failures = 0

        for i, passage in enumerate(merged_passages):
            # Check for cancellation between passages
            if is_cancelled():
                _log(f"\nExtraction terminated by user after {summary['records_processed']} passages.")
                extraction_job.status = "cancelled"
                extraction_job.completed_at = datetime.utcnow()
                db.commit()
                summary["total_extractions"] += job_extractions
                summary["cancelled"] = True
                _monitor.stop_run(cancelled=True)
                return summary

            try:
                count = extract_single_record(
                    db, passage, agents, extraction_job, parse_quality,
                    token_usage, existing_hashes, tracker,
                )
                job_extractions += count
                extraction_job.records_processed += len(passage.source_records)
                summary["records_processed"] += len(passage.source_records)

                # Commit in batches of 10 to avoid holding huge transactions
                if (i + 1) % 10 == 0:
                    db.commit()
                    _log(f"  {i + 1}/{len(merged_passages)} passages processed...")

            except CircuitBreakerTripped as cb:
                # Commit what we have so far, then abort the entire run
                extraction_job.status = "failed"
                extraction_job.completed_at = datetime.utcnow()
                db.commit()
                summary["total_extractions"] += job_extractions
                summary["circuit_breaker_tripped"] = True
                summary["circuit_breaker_detail"] = str(cb)
                _log(f"\n{cb}")
                _monitor.record_circuit_breaker(str(cb))
                _monitor.stop_run()
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
        _monitor.record_document_complete(label, job_extractions, job_failures)
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
    _monitor.stop_run()
    return summary


# ---------------------------------------------------------------------------
# Dependency Graph Building (Phase 2 — post-extraction)
# ---------------------------------------------------------------------------


def run_dependency_graph(
    db,
    document_version_id: int | None = None,
    on_progress: callable | None = None,
) -> dict:
    """Build dependency graphs for documents that have extractions.

    This is a post-extraction step that identifies relationships between
    extractions within each document and writes edges to the
    ``obligation_dependencies`` table.

    Uses GPT (gpt-oss-20b) with 131k context to process entire documents
    at once, identifying cross-references between obligations, definitions,
    thresholds, exceptions, enforcement mechanisms, rights, and compliance
    mechanisms.

    Args:
        db: SQLAlchemy session
        document_version_id: Process a single document version (None = all
            document versions that have extractions but no dependency edges).
        on_progress: Optional callback(message: str) for status updates.

    Returns:
        Summary dict with counts.
    """
    from src.agents.dependency_builder import build_dependency_graph

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    if document_version_id:
        # Process a single document
        result = build_dependency_graph(db, document_version_id, on_progress)
        return {
            "documents_processed": 1,
            "total_edges": result["edges_created"],
            "results": [result],
        }

    # Find all document versions that have extractions but no dependency edges
    dv_ids_with_extractions = (
        db.execute(
            select(NormalizedSourceRecord.document_version_id)
            .join(Extraction)
            .group_by(NormalizedSourceRecord.document_version_id)
        ).scalars().all()
    )

    if not dv_ids_with_extractions:
        _log("No documents with extractions found.")
        return {"documents_processed": 0, "total_edges": 0, "results": []}

    # Filter to those without existing dependency edges
    dv_ids_with_deps = set(
        db.execute(
            select(NormalizedSourceRecord.document_version_id)
            .join(Extraction, Extraction.source_record_id == NormalizedSourceRecord.id)
            .join(
                ObligationDependency,
                ObligationDependency.parent_extraction_id == Extraction.id,
            )
            .group_by(NormalizedSourceRecord.document_version_id)
        ).scalars().all()
    )

    pending_ids = [
        dv_id for dv_id in dv_ids_with_extractions
        if dv_id not in dv_ids_with_deps
    ]

    if not pending_ids:
        _log("All documents already have dependency graphs.")
        return {"documents_processed": 0, "total_edges": 0, "results": []}

    _log(f"Building dependency graphs for {len(pending_ids)} documents...")

    results = []
    total_edges = 0

    for i, dv_id in enumerate(pending_ids):
        if is_cancelled():
            _log(f"Dependency graph building terminated after {i} documents.")
            return {
                "documents_processed": i,
                "total_edges": total_edges,
                "results": results,
                "cancelled": True,
            }

        try:
            result = build_dependency_graph(db, dv_id, on_progress)
            results.append(result)
            total_edges += result["edges_created"]
        except Exception as e:
            logger.error(
                "dependency_graph_failed",
                document_version_id=dv_id,
                error=str(e),
            )
            results.append({
                "document_version_id": dv_id,
                "edges_created": 0,
                "errors": 1,
                "error_message": str(e),
            })

    _log(
        f"\nDependency graph building complete: "
        f"{total_edges} edges across {len(pending_ids)} documents"
    )

    return {
        "documents_processed": len(pending_ids),
        "total_edges": total_edges,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Applicability Condition Parsing (Phase 3 — post-extraction)
# ---------------------------------------------------------------------------


def run_condition_parsing(
    db,
    document_version_id: int | None = None,
    on_progress: callable | None = None,
) -> dict:
    """Parse condition fields from extractions into structured expression trees.

    This is a post-extraction step that converts free-text condition strings
    (e.g. "if the system is high-risk and the deployer is in California")
    into AND/OR/NOT/LEAF boolean expression trees stored in the
    ``applicability_conditions`` table.

    Rule-based parser — no LLM call required.

    Args:
        db: SQLAlchemy session
        document_version_id: Process a single document (None = all pending).
        on_progress: Optional callback for status messages.

    Returns:
        Summary dict with counts.
    """
    from src.core.condition_parser import run_condition_parsing as _run_parsing

    return _run_parsing(db, document_version_id, on_progress)


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

    tracker = FailureTracker(
        context="recovery extraction (agent calls)",
        max_consecutive=CIRCUIT_BREAKER_THRESHOLD,
        max_failure_rate=0.8,
        min_items_for_rate=10,
    )

    try:
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
                    tracker.record_success()

                    if token_usage is not None:
                        token_usage.add(result.input_tokens, result.output_tokens)

                    if result.abstention is not None:
                        continue

                    existing_hashes.add(content_hash)
                    default_type = AGENT_EXTRACTION_TYPES[agent_name][0]
                    schema_class = EXTRACTION_TYPE_SCHEMAS.get(default_type.value)

                    for item in result.extractions:
                        try:
                            resolved_type = _discriminate_extraction_type(agent_name, item)
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
                                extraction_type=resolved_type,
                                payload=item,
                                evidence_spans=evidence,
                                confidence_score=confidence.total_score,
                                confidence_tier=ConfidenceTier(confidence.tier),
                                review_status=ReviewStatus.pending,
                                prompt_template_version=result.prompt_hash,
                                prompt_hash=result.prompt_hash,
                                template_version=result.template_version,
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

                except CircuitBreakerTripped:
                    raise  # Let it propagate
                except Exception as e:
                    tracker.record_failure(
                        f"agent={agent_name} record={record.id}: {e}"
                    )
                    errors += 1

            # Commit every 10 passages
            if (i + 1) % 10 == 0:
                db.commit()
                _log(f"  {i + 1}/{len(gaps)} passages processed...")

    except CircuitBreakerTripped as cb:
        db.commit()
        _log(f"\n{cb}")
        return {
            "total_checked": len(records_with_extractions),
            "gaps_found": len(gaps),
            "extractions_created": total_extractions,
            "errors": errors,
            "circuit_breaker_tripped": True,
            "circuit_breaker_detail": str(cb),
        }

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

    tracker = FailureTracker(
        context="batch result processing",
        max_consecutive=10,       # Higher threshold — batch errors are per-result, not per-call
        max_failure_rate=0.8,
        min_items_for_rate=20,
    )

    try:
      for entry in results_iter:
        results_processed += 1
        custom_id = entry.custom_id

        # --- Parse custom_id ---
        if "--" in custom_id:
            record_ids_str, _, agent_name = custom_id.partition("--")
        else:
            last_underscore = custom_id.rfind("_")
            if last_underscore == -1:
                logger.error("batch_invalid_custom_id", custom_id=custom_id)
                errors += 1
                tracker.record_failure(f"invalid custom_id: {custom_id}")
                continue
            record_ids_str = custom_id[:last_underscore]
            agent_name = custom_id[last_underscore + 1:]
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
            tracker.record_failure(f"unknown agent '{agent_name}' in {custom_id}")
            continue

        # Parse record IDs
        try:
            record_ids = [int(rid) for rid in record_ids_str.split("-") if rid]
        except ValueError:
            logger.error("batch_invalid_record_ids", custom_id=custom_id)
            errors += 1
            tracker.record_failure(f"unparseable record IDs in {custom_id}")
            continue

        # Check result type
        if entry.result.type == "errored":
            error_msg = str(entry.result.error)
            logger.error(
                "batch_result_error",
                custom_id=custom_id,
                error=error_msg,
            )
            errors += 1
            tracker.record_failure(f"API error for {custom_id}: {error_msg[:100]}")
            continue

        if entry.result.type != "succeeded":
            logger.warning(
                "batch_result_skipped",
                custom_id=custom_id,
                result_type=entry.result.type,
            )
            errors += 1
            tracker.record_failure(f"result type '{entry.result.type}' for {custom_id}")
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
            tracker.record_failure(f"empty response for {custom_id}")
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
            tracker.record_failure(f"JSON parse error for {custom_id}: {e}")
            continue

        # Check for abstention
        if parsed.get("detected") is False:
            logger.debug("batch_abstention", custom_id=custom_id)
            tracker.record_success()
            continue

        # Get agent and schema
        agent = agents.get(agent_name)
        if not agent:
            logger.error("batch_agent_not_found", agent_name=agent_name)
            errors += 1
            tracker.record_failure(f"agent '{agent_name}' not in registry")
            continue

        schema = agent.get_output_schema()
        default_type = AGENT_EXTRACTION_TYPES[agent_name][0]
        schema_class = EXTRACTION_TYPE_SCHEMAS.get(default_type.value)

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
            tracker.record_failure(f"records {record_ids} not found in DB")
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

        entry_failed = False
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
                resolved_type = _discriminate_extraction_type(agent_name, result_dict)
                for source_record in source_records:
                    extraction = Extraction(
                        source_record_id=source_record.id,
                        extraction_type=resolved_type,
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
                entry_failed = True

        if entry_failed:
            tracker.record_failure(f"validation error for {custom_id}")
        else:
            tracker.record_success()

        # Commit periodically
        if results_processed % 50 == 0:
            db.commit()
            _log(f"  {results_processed} results processed...")

    except CircuitBreakerTripped as cb:
        db.commit()
        _log(f"\n{cb}")
        return {
            "batch_id": batch_id,
            "status": "aborted",
            "results_processed": results_processed,
            "extractions_created": total_extractions,
            "errors": errors,
            "circuit_breaker_tripped": True,
            "circuit_breaker_detail": str(cb),
        }

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


# ---------------------------------------------------------------------------
# Completeness Manifest — per-document extraction coverage reporting
# ---------------------------------------------------------------------------


@dataclass
class PassageCoverage:
    """Extraction coverage for a single passage."""

    record_id: int
    section_path: str | None
    text_length: int
    skipped_short: bool
    skipped_boilerplate: bool
    agents_run: list[str]
    agents_with_extractions: list[str]
    agents_abstained: list[str]
    extraction_count: int


@dataclass
class DocumentCompleteness:
    """Completeness manifest for a single document version."""

    document_version_id: int
    document_label: str
    jurisdiction: str | None
    total_passages: int
    passages_processed: int
    passages_skipped_short: int
    passages_skipped_boilerplate: int
    passages_with_extractions: int
    passages_with_no_results: int
    coverage_percent: float
    agent_coverage: dict[str, dict[str, int]]  # agent -> {run, extracted, abstained}
    gaps: list[dict[str, Any]]  # passages with incomplete coverage
    is_complete: bool


def compute_completeness_manifest(
    db,
    document_version_id: int | None = None,
) -> list[DocumentCompleteness]:
    """Compute extraction completeness for documents.

    For each document version, reports:
    - Total passages vs. processed passages
    - Which agents ran on each passage and which produced results
    - Gaps where passages were skipped or agents didn't run
    - Overall coverage percentage

    This enables audit-grade completeness assurance: you can certify
    that every passage in a law has been processed and flag laws where
    extraction coverage is below 100%.

    Args:
        db: SQLAlchemy session
        document_version_id: Compute for a single doc (None = all with passages)

    Returns:
        List of DocumentCompleteness reports.
    """
    from sqlalchemy import distinct

    agents = _get_agents()
    agent_names = sorted(agents.keys())

    # Find document versions to check
    dv_query = select(DocumentVersion.id)
    if document_version_id:
        dv_query = dv_query.where(DocumentVersion.id == document_version_id)
    else:
        # Only check documents that have at least one passage
        dv_query = dv_query.where(
            DocumentVersion.id.in_(
                select(distinct(NormalizedSourceRecord.document_version_id))
            )
        )

    dv_ids = db.scalars(dv_query).all()
    results: list[DocumentCompleteness] = []

    for dv_id in dv_ids:
        dv = db.get(DocumentVersion, dv_id)
        if not dv:
            continue

        # Build document label
        label = "unknown"
        jurisdiction = None
        if dv.family:
            if dv.family.source:
                jurisdiction = dv.family.source.jurisdiction_code
            label = f"{jurisdiction or '??'} - {dv.family.short_cite or dv.family.canonical_title}"

        # Get all passages for this document
        records = db.scalars(
            select(NormalizedSourceRecord)
            .where(NormalizedSourceRecord.document_version_id == dv_id)
            .order_by(NormalizedSourceRecord.ordinal)
        ).all()

        if not records:
            continue

        # Get existing extractions grouped by source_record_id
        extraction_rows = db.execute(
            select(
                Extraction.source_record_id,
                Extraction.extraction_type,
                Extraction.model_id,
            ).where(
                Extraction.source_record_id.in_([r.id for r in records])
            )
        ).all()

        # Build lookup: record_id -> set of extraction types
        extractions_by_record: dict[int, set[str]] = {}
        for src_id, ext_type, _ in extraction_rows:
            ext_val = ext_type.value if hasattr(ext_type, "value") else str(ext_type)
            extractions_by_record.setdefault(src_id, set()).add(ext_val)

        # Analyze each passage
        total = len(records)
        processed = 0
        skipped_short = 0
        skipped_boilerplate = 0
        with_extractions = 0
        no_results = 0
        agent_stats: dict[str, dict[str, int]] = {
            name: {"run": 0, "extracted": 0, "abstained": 0}
            for name in agent_names
        }
        gaps: list[dict[str, Any]] = []

        for record in records:
            text = record.text_content
            text_len = len(text)

            # Check if passage was skipped as too short
            if text_len < MIN_PASSAGE_LENGTH:
                skipped_short += 1
                continue

            # Check if passage would be excluded by negative screening
            text_stripped = text.strip()
            if _BOILERPLATE_PATTERN.fullmatch(text_stripped):
                skipped_boilerplate += 1
                continue
            if _ENACTING_CLAUSE_PATTERN.match(text_stripped) and len(text_stripped) < 300:
                skipped_boilerplate += 1
                continue

            processed += 1

            # Determine which agents should have run
            selected = _select_agents_for_passage(text, agents)
            selected_names = sorted(selected.keys())

            # Check which agents produced extractions for this passage
            record_extractions = extractions_by_record.get(record.id, set())
            agents_with_results = []
            agents_abstained_list = []

            for agent_name in selected_names:
                agent_types = AGENT_EXTRACTION_TYPES[agent_name]
                type_vals = {t.value for t in agent_types}
                if record_extractions & type_vals:
                    agents_with_results.append(agent_name)
                    agent_stats[agent_name]["extracted"] += 1
                else:
                    agents_abstained_list.append(agent_name)
                    agent_stats[agent_name]["abstained"] += 1
                agent_stats[agent_name]["run"] += 1

            has_extractions = len(agents_with_results) > 0
            if has_extractions:
                with_extractions += 1
            elif record_extractions:
                # Has extractions but from agents not in selected set (edge case)
                with_extractions += 1
            else:
                no_results += 1

            # Flag as a gap if no extractions at all, or if passage hasn't been processed
            if not record_extractions:
                gaps.append({
                    "record_id": record.id,
                    "section_path": record.section_path,
                    "text_preview": text[:150].replace("\n", " "),
                    "text_length": text_len,
                    "expected_agents": selected_names,
                    "reason": "no_extractions",
                })

        # Compute coverage
        coverage = 0.0
        if processed > 0:
            coverage = round((with_extractions / processed) * 100, 1)

        results.append(DocumentCompleteness(
            document_version_id=dv_id,
            document_label=label,
            jurisdiction=jurisdiction,
            total_passages=total,
            passages_processed=processed,
            passages_skipped_short=skipped_short,
            passages_skipped_boilerplate=skipped_boilerplate,
            passages_with_extractions=with_extractions,
            passages_with_no_results=no_results,
            coverage_percent=coverage,
            agent_coverage=agent_stats,
            gaps=gaps,
            is_complete=(len(gaps) == 0 and processed > 0),
        ))

    return results


# ---------------------------------------------------------------------------
# Verification Pipeline — post-extraction accuracy layers
# ---------------------------------------------------------------------------


@dataclass
class VerificationResult:
    """Combined result from all verification agents for a document."""

    document_version_id: int
    document_label: str

    # Cross-validation results
    cross_validation_passages: int
    cross_validation_valid: int
    cross_validation_flagged: int
    cross_validation_avg_accuracy: float
    cross_validation_issues: list[dict[str, Any]]

    # Gap detection results
    gap_detection_passages: int
    gaps_found: int
    high_confidence_gaps: int
    gap_candidates: list[dict[str, Any]]

    # Citation verification results
    citations_checked: int
    citations_verified: int
    citations_unverified: int
    citation_issues: list[dict[str, Any]]

    # Token usage
    total_input_tokens: int
    total_output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


def run_verification_pass(
    db,
    document_version_id: int | None = None,
    skip_cross_validation: bool = False,
    skip_gap_detection: bool = False,
    skip_citation_verification: bool = False,
    on_progress: callable | None = None,
) -> list[VerificationResult]:
    """Run post-extraction verification agents on completed extractions.

    Three verification layers:
      1. Cross-Validation: Second LLM (different model) reviews each extraction
         against the source passage for hallucinations, contradictions, etc.
      2. Gap Detection: Second-pass agent identifies obligations the primary
         extraction missed.
      3. Citation Verification: Validates section_reference and cross_reference
         fields against the actual document structure.

    All three are independent and can be run selectively.

    Args:
        db: SQLAlchemy session
        document_version_id: Run on a single document (None = all with extractions)
        skip_cross_validation: Skip the cross-validation layer
        skip_gap_detection: Skip the gap detection layer
        skip_citation_verification: Skip citation verification
        on_progress: Optional callback for status messages

    Returns:
        List of VerificationResult, one per document version.
    """
    from sqlalchemy import distinct

    from src.agents.citation_verifier import verify_citations
    from src.agents.cross_validation import run_cross_validation
    from src.agents.gap_detector import run_gap_detection

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    # Find document versions to verify
    dv_query = select(DocumentVersion.id)
    if document_version_id:
        dv_query = dv_query.where(DocumentVersion.id == document_version_id)
    else:
        dv_query = dv_query.where(
            DocumentVersion.id.in_(
                select(distinct(NormalizedSourceRecord.document_version_id))
                .where(
                    NormalizedSourceRecord.id.in_(
                        select(distinct(Extraction.source_record_id))
                    )
                )
            )
        )

    dv_ids = db.scalars(dv_query).all()
    _log(f"Running verification on {len(dv_ids)} document version(s)...")

    results: list[VerificationResult] = []

    for dv_id in dv_ids:
        if is_cancelled():
            _log("Verification cancelled by user.")
            break

        dv = db.get(DocumentVersion, dv_id)
        if not dv:
            continue

        label = "unknown"
        if dv.family:
            jur = dv.family.source.jurisdiction_code if dv.family.source else "??"
            label = f"{jur} - {dv.family.short_cite or dv.family.canonical_title}"

        _log(f"\n[{label}] Starting verification pass...")

        total_input_tokens = 0
        total_output_tokens = 0

        # Get all passages + extractions for this document
        records = db.scalars(
            select(NormalizedSourceRecord)
            .where(NormalizedSourceRecord.document_version_id == dv_id)
            .order_by(NormalizedSourceRecord.ordinal)
        ).all()

        # --- Layer 1: Cross-Validation ---
        cv_passages = 0
        cv_valid = 0
        cv_flagged = 0
        cv_accuracy_sum = 0.0
        cv_issues: list[dict[str, Any]] = []

        if not skip_cross_validation:
            _log(f"  [1/3] Cross-validation...")
            for record in records:
                if len(record.text_content) < MIN_PASSAGE_LENGTH:
                    continue

                extractions = db.scalars(
                    select(Extraction).where(
                        Extraction.source_record_id == record.id
                    )
                ).all()

                if not extractions:
                    continue

                ext_payloads = [e.payload for e in extractions]
                ext_ids = [e.id for e in extractions]
                ctx = _build_context(db, record)

                cv_result = run_cross_validation(
                    passage_text=record.text_content,
                    extractions=ext_payloads,
                    passage_record_id=record.id,
                    extraction_ids=ext_ids,
                    context=ctx,
                )

                cv_passages += 1
                cv_valid += cv_result.extractions_valid
                cv_flagged += cv_result.extractions_flagged
                cv_accuracy_sum += cv_result.avg_accuracy_score
                total_input_tokens += cv_result.input_tokens
                total_output_tokens += cv_result.output_tokens

                # Store flagged issues
                for r in cv_result.results:
                    if not r.get("is_valid", True):
                        cv_issues.append({
                            "record_id": record.id,
                            "section_path": record.section_path,
                            **r,
                        })

                        # Update extraction metadata with cross-validation flag
                        ext_id = r.get("extraction_id")
                        if ext_id:
                            extraction = db.get(Extraction, ext_id)
                            if extraction:
                                meta = dict(extraction.metadata_ or {})
                                meta["cross_validation"] = {
                                    "is_valid": r.get("is_valid", True),
                                    "accuracy_score": r.get("accuracy_score", 1.0),
                                    "issues": r.get("issues", []),
                                }
                                extraction.metadata_ = meta

            cv_avg = cv_accuracy_sum / cv_passages if cv_passages > 0 else 1.0
            _log(
                f"    {cv_passages} passages checked, {cv_valid} valid, "
                f"{cv_flagged} flagged, avg accuracy: {cv_avg:.3f}"
            )
        else:
            cv_avg = 1.0

        # --- Layer 2: Gap Detection ---
        gd_passages = 0
        gd_gaps = 0
        gd_high = 0
        gd_candidates: list[dict[str, Any]] = []

        if not skip_gap_detection:
            _log(f"  [2/3] Gap detection...")
            for record in records:
                if len(record.text_content) < MIN_PASSAGE_LENGTH:
                    continue

                extractions = db.scalars(
                    select(Extraction).where(
                        Extraction.source_record_id == record.id
                    )
                ).all()

                ext_payloads = [e.payload for e in extractions]
                ctx = _build_context(db, record)

                gd_result = run_gap_detection(
                    passage_text=record.text_content,
                    existing_extractions=ext_payloads,
                    passage_record_id=record.id,
                    context=ctx,
                )

                gd_passages += 1
                gd_gaps += gd_result.gaps_found
                gd_high += gd_result.high_confidence_gaps
                total_input_tokens += gd_result.input_tokens
                total_output_tokens += gd_result.output_tokens

                for candidate in gd_result.candidates:
                    gd_candidates.append({
                        "record_id": record.id,
                        "section_path": record.section_path,
                        **candidate,
                    })

            _log(
                f"    {gd_passages} passages checked, "
                f"{gd_gaps} gaps found ({gd_high} high confidence)"
            )

        # --- Layer 3: Citation Verification ---
        cit_checked = 0
        cit_verified = 0
        cit_unverified = 0
        cit_issues: list[dict[str, Any]] = []

        if not skip_citation_verification:
            _log(f"  [3/3] Citation verification...")
            cit_result = verify_citations(db, dv_id)
            cit_checked = cit_result.total_citations_checked
            cit_verified = cit_result.citations_verified
            cit_unverified = cit_result.citations_unverified
            cit_issues = [
                {
                    "extraction_id": issue.extraction_id,
                    "field_name": issue.field_name,
                    "cited_value": issue.cited_value,
                    "issue_type": issue.issue_type,
                    "closest_match": issue.closest_match,
                }
                for issue in cit_result.issues
            ]
            _log(
                f"    {cit_checked} citations checked, "
                f"{cit_verified} verified, {cit_unverified} unverified"
            )

        db.commit()

        results.append(VerificationResult(
            document_version_id=dv_id,
            document_label=label,
            cross_validation_passages=cv_passages,
            cross_validation_valid=cv_valid,
            cross_validation_flagged=cv_flagged,
            cross_validation_avg_accuracy=round(cv_avg, 4),
            cross_validation_issues=cv_issues,
            gap_detection_passages=gd_passages,
            gaps_found=gd_gaps,
            high_confidence_gaps=gd_high,
            gap_candidates=gd_candidates,
            citations_checked=cit_checked,
            citations_verified=cit_verified,
            citations_unverified=cit_unverified,
            citation_issues=cit_issues,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
        ))

        _log(
            f"  Verification complete for [{label}]: "
            f"{total_input_tokens + total_output_tokens:,} tokens used"
        )

    _log(f"\nVerification pass complete: {len(results)} documents processed")
    return results
