"""Extraction pipeline — run AI agents against normalized passages.

All extraction uses local models via OpenAI-compatible API (LM Studio, etc.).

CLI: python -m src.scripts.seed_pipeline --mode extract

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
  - All models run locally (no cloud API costs)
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import update as sa_update

from src.agents.base import BaseExtractionAgent, ExtractionResult
from src.agents.compliance_mechanism import ComplianceMechanismAgent
from src.agents.definition_actor import DefinitionActorAgent
from src.agents.obligation import ObligationAgent
from src.agents.preemption import PreemptionAgent
from src.agents.rights_protection import RightsProtectionAgent
from src.agents.threshold_exception import ThresholdExceptionAgent
from src.core.circuit_breaker import CircuitBreakerTripped, FailureTracker
from src.core.confidence import cap_at_tier_c, compute_confidence
from src.core.config import settings
from src.core.jurisdiction_check import (
    validate_extraction_jurisdiction,
)
from src.core.orrick_validation import validate_extraction_against_orrick
from src.db.models import (
    ApplicabilityCondition,
    ConfidenceTier,
    Extraction,
    ExtractionAttempt,
    ExtractionJob,
    ExtractionType,
    FailedExtractionAttempt,
    IngestionJob,
    NormalizedSourceRecord,
    ObligationDependency,
    PipelineEvent,
    ReviewAction,
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

# Global pause event — cleared to pause the loop, set to run.
_pause_event = threading.Event()
_pause_event.set()  # running by default

# Heartbeat: updated each time a passage loop iteration starts.
_last_passage_at: float = 0.0


def request_cancel() -> None:
    """Signal the running extraction pipeline to stop after the current passage."""
    _cancel_event.set()
    _pause_event.set()  # wake the loop so it sees the cancel immediately


def is_cancelled() -> bool:
    """Check whether cancellation has been requested."""
    return _cancel_event.is_set()


def clear_cancel() -> None:
    """Reset the cancellation flag (called at extraction start)."""
    _cancel_event.clear()


def request_pause() -> None:
    """Pause the extraction loop between passages."""
    _pause_event.clear()


def request_resume() -> None:
    """Resume a paused extraction loop."""
    _pause_event.set()


def is_paused() -> bool:
    """Return True when the extraction loop is requested to pause."""
    return not _pause_event.is_set()


def clear_pause() -> None:
    """Reset pause state (called at extraction start)."""
    _pause_event.set()


def seconds_since_last_passage() -> float:
    """Seconds elapsed since the last passage loop iteration.

    Returns 0.0 when no extraction is actively running, so a stale heartbeat
    left over from a finished or dead run is never mistaken for a stuck
    passage by the dashboard health endpoint.
    """
    from src.core.extraction_monitor import get_monitor

    if not get_monitor().is_running:
        return 0.0
    if _last_passage_at == 0.0:
        return 0.0
    return time.monotonic() - _last_passage_at


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Passages shorter than this are legislative boilerplate (headers, stubs, etc.)
MIN_PASSAGE_LENGTH = 150

# Set at module-load or first extraction run — True once migration adds column
_payload_hash_available: bool | None = None
_token_columns_available: bool | None = None
_run_id_available: bool | None = None
_model_agreement_available: bool | None = None

# Circuit breaker: abort extraction if this many consecutive agent calls fail.
# Prevents silently skipping data when LM Studio/GPU is down.
CIRCUIT_BREAKER_THRESHOLD = 10

# Bill-level agents to run after per-passage extraction for each document version.
# Import lazily to avoid circular imports.
_BILL_LEVEL_AGENT_CLASSES: list[str] = [
    "src.agents.enforcement_agent.EnforcementAgent",
    "src.agents.applicability_agent.ApplicabilityAgent",
    "src.agents.compliance_timeline_agent.ComplianceTimelineAgent",
]

def _classify_llm_error(exc: Exception | str) -> str:
    """Map an extraction exception to a coarse error_type for the UI.

    Distinguishes provider-level failures (auth, quota) from model/output
    failures so the dashboard can color-code and filter them.  Returns one of:
    "auth_error", "quota_error", "validation_error", "timeout_error", "llm_error".
    """
    # Prefer the HTTP status code when present (NVIDIA / OpenAI-compatible APIs).
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403):
        return "auth_error"
    if status == 429:
        return "quota_error"

    msg = str(exc).lower()
    if "429" in msg or "rate limit" in msg or "quota" in msg:
        return "quota_error"
    if "401" in msg or "403" in msg or "auth" in msg or "entitlement" in msg or "api key" in msg:
        return "auth_error"
    if "timeout" in msg or "timed out" in msg or "connecterror" in msg:
        return "timeout_error"
    if (
        "json" in msg
        or "validation" in msg
        or "schema" in msg
        or "parse" in msg
        or "expecting value" in msg
    ):
        return "validation_error"
    return "llm_error"


def _record_failed_attempt(
    db,
    source_record_id: int,
    agent_name: str,
    error_type: str,
    error_message: str,
    extraction_job_id: int | None = None,
    run_id: int | None = None,
) -> None:
    """Record a failed extraction attempt for later retry."""
    try:
        db.add(FailedExtractionAttempt(
            source_record_id=source_record_id,
            agent_name=agent_name,
            error_type=error_type,
            error_message=str(error_message)[:2000],
            extraction_job_id=extraction_job_id,
            run_id=run_id,
        ))
        db.flush()
    except Exception as e:
        # Don't let failure tracking itself block the pipeline
        logger.warning("failed_attempt_recording_error", error=str(e))


def _begin_attempt(
    db,
    source_record_id: int,
    agent_name: str,
    run_id: int | None = None,
    input_text_hash: str | None = None,
) -> int | None:
    """Insert an ExtractionAttempt row with status='running'. Returns row id."""
    try:
        from datetime import datetime
        row = ExtractionAttempt(
            source_record_id=source_record_id,
            agent_name=agent_name,
            run_id=run_id,
            status="running",
            started_at=datetime.utcnow(),
            input_text_hash=input_text_hash,
        )
        db.add(row)
        db.flush()
        return row.id
    except Exception as e:
        logger.warning("attempt_begin_error", agent=agent_name, error=str(e))
        return None


def _finish_attempt(
    db,
    attempt_id: int | None,
    status: str,
    extractions_produced: int = 0,
    error_message: str | None = None,
) -> None:
    """Update an ExtractionAttempt row to a terminal status."""
    if attempt_id is None:
        return
    try:
        from datetime import datetime
        row = db.get(ExtractionAttempt, attempt_id)
        if row:
            row.status = status
            row.completed_at = datetime.utcnow()
            row.extractions_produced = extractions_produced
            if error_message:
                row.error_message = error_message[:2000]
            db.flush()
    except Exception as e:
        logger.warning("attempt_finish_error", attempt_id=attempt_id, error=str(e))


def _skip_attempt(
    db,
    source_record_id: int,
    agent_name: str,
    run_id: int | None = None,
    input_text_hash: str | None = None,
) -> None:
    """Insert an ExtractionAttempt row with status='skipped' (no agent call made)."""
    try:
        from datetime import datetime
        now = datetime.utcnow()
        db.add(ExtractionAttempt(
            source_record_id=source_record_id,
            agent_name=agent_name,
            run_id=run_id,
            status="skipped",
            started_at=now,
            completed_at=now,
            input_text_hash=input_text_hash,
        ))
        db.flush()
    except Exception as e:
        logger.warning("attempt_skip_error", agent=agent_name, error=str(e))


def _persist_pipeline_event(
    db,
    event_type: str,
    *,
    run_id: int | None = None,
    source_record_id: int | None = None,
    agent_name: str | None = None,
    extraction_count: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    duration_ms: int | None = None,
    confidence_tier: str | None = None,
    error_message: str | None = None,
    model_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Write a PipelineEvent row to the DB (RR6a — durable event log).

    Fires best-effort: exceptions are logged and swallowed so an event
    persistence failure never disrupts the extraction pipeline.

    ``model_id`` defaults to the active extraction provider's model_id (e.g.
    ``…-nvidia`` / ``…-local``) so every event carries backend attribution.
    """
    if model_id is None:
        try:
            from src.core.llm_provider import get_extraction_provider
            model_id = get_extraction_provider().model_id
        except Exception:
            model_id = None  # never let attribution lookup break event logging
    # Use a savepoint so a write failure (e.g. table missing) rolls back only
    # this insert, never the surrounding extraction transaction.
    try:
        with db.begin_nested():
            db.add(PipelineEvent(
                run_id=run_id,
                source_record_id=source_record_id,
                event_type=event_type,
                agent_name=agent_name,
                extraction_count=extraction_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                confidence_tier=confidence_tier,
                error_message=error_message[:2000] if error_message else None,
                model_id=model_id,
                details=details,
            ))
    except Exception as e:
        logger.warning("pipeline_event_persist_error", event_type=event_type, error=str(e))


# Agent registry — 7 extraction agents
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
    # "ambiguity" retired — findings now embedded as interpretation_risks on obligation/rights payloads
    "rights_protection": [ExtractionType.rights_protection],
    "compliance_mechanism": [ExtractionType.compliance_mechanism],
    "preemption": [ExtractionType.preemption_signal],
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

        # Check if the "obligation" is really just an enforcement provision
        # disguised as an obligation. When the subject is a judicial or
        # enforcement authority (court, AG, commission) AND the action is
        # about imposing penalties/fines, this is an enforcement extraction,
        # not a compliance obligation on a regulated entity.
        if has_core_obligation and has_enforcement:
            subject_lower = (payload.get("subject_normalized") or payload.get("subject") or "").lower()
            action_lower = (payload.get("action") or "").lower()
            _enforcement_subjects = {
                "court", "judge", "judicial_authority", "attorney_general",
                "ag", "commission", "prosecutor", "district_attorney",
                "secretary", "commissioner", "regulator", "agency",
            }
            _penalty_verbs = {"fine", "penalt", "sanction", "forfeit", "assess", "impose", "award"}
            is_enforcement_subject = any(s in subject_lower for s in _enforcement_subjects)
            is_penalty_action = any(v in action_lower for v in _penalty_verbs)

            if is_enforcement_subject and is_penalty_action:
                return ExtractionType.enforcement

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
        sub_type = payload.get("threshold_sub_type") or ""
        has_threshold = bool(
            payload.get("threshold_type") or payload.get("threshold_value")
            or payload.get("threshold_condition")
        )
        exceptions = payload.get("exceptions") or []

        # Use structured sub_type when available (new extractions)
        if sub_type == "exemption":
            return ExtractionType.exception
        if sub_type in ("scope", "temporal", "other"):
            return ExtractionType.threshold

        # Fall back to legacy heuristic for old extractions (sub_type absent)
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
    """Aggregate token usage and invocation counts for one extraction run.

    Token buckets (non-error calls only; adaptive retries inside agent.extract()
    are excluded — see agent_stats.json for all-attempt cost):
      clause_level_*  — 6 clause-level agents (success + abstentions)
      bill_level_*    — 3 bill-level agents
      total_*         — aggregate (clause + bill combined, kept for backward compat)
    """

    # Named token buckets
    clause_level_input_tokens: int = 0
    clause_level_output_tokens: int = 0
    bill_level_input_tokens: int = 0
    bill_level_output_tokens: int = 0

    # Aggregate totals (kept for backward compat; updated by all add_* methods)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_calls: int = 0  # non-error LLM dispatches (success + abstentions)

    # Invocation counters
    abstention_count: int = 0
    error_count: int = 0
    extraction_item_count: int = 0

    # Efficiency counters
    skipped_short: int = 0
    merged_passages: int = 0
    agents_skipped: int = 0

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def llm_call_count(self) -> int:
        """Non-error LLM dispatches including abstentions (excludes internal retries)."""
        return self.total_calls

    def add(self, input_tokens: int, output_tokens: int) -> None:
        """Legacy method — routes to add_clause() for backward compatibility."""
        self.add_clause(input_tokens, output_tokens)

    def add_clause(self, input_tokens: int, output_tokens: int) -> None:
        """Record one clause-level agent result (non-error; success or abstention)."""
        self.clause_level_input_tokens += input_tokens
        self.clause_level_output_tokens += output_tokens
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_calls += 1

    def add_bill_level(self, input_tokens: int, output_tokens: int) -> None:
        """Record one bill-level agent result (non-error)."""
        self.bill_level_input_tokens += input_tokens
        self.bill_level_output_tokens += output_tokens
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
    """Lazy-init agents (avoids Anthropic client creation at import time).

    Applies per-agent model/token/temperature overrides from
    ``config/agent_models.json`` (editable via the dashboard Models page).
    """
    global AGENTS
    if not AGENTS:
        from src.core.model_config import get_config

        AGENTS = {
            "obligation": ObligationAgent(),
            "definition_actor": DefinitionActorAgent(),
            "threshold_exception": ThresholdExceptionAgent(),
            "rights_protection": RightsProtectionAgent(),
            "compliance_mechanism": ComplianceMechanismAgent(),
            "preemption": PreemptionAgent(),
        }
        # Apply runtime overrides from config file
        cfg = get_config()
        for name, agent in AGENTS.items():
            acfg = cfg.get(name)
            if acfg.model:
                agent.model_override = acfg.model
            if acfg.max_tokens:
                agent.max_tokens_override = acfg.max_tokens
            if acfg.temperature is not None:
                agent.temperature_override = acfg.temperature
    return AGENTS


def reload_agents() -> dict[str, BaseExtractionAgent]:
    """Force re-create agents with fresh config (called after UI config save)."""
    global AGENTS
    AGENTS = {}
    return _get_agents()


def _confidence_to_priority(tier: str) -> int:
    """Map confidence tier to review priority (higher = more urgent)."""
    return {"A": 0, "B": 1, "C": 2, "D": 3}.get(tier, 1)


def _apply_numeric_grounding(item: dict, evidence: list[dict], extraction_meta: dict) -> bool:
    """Run EA2-1 deterministic numeric-field cross-check; returns True on any mismatch.

    Evidence-span verification only confirms a quoted STRING appears in the
    passage; it never checked whether a field's typed NUMBER (penalty
    amount, cure period, retention window, etc.) actually matches what the
    evidence text says. This attaches the per-field grounding result to
    extraction_meta["numeric_grounding"] (informational — does not change
    confidence_score, which is EA3 territory) and returns whether the
    extraction should be treated as high-priority for review.
    """
    from src.core.numeric_grounding import check_numeric_grounding, has_numeric_mismatch

    numeric_results = check_numeric_grounding(item, evidence)
    if numeric_results:
        extraction_meta["numeric_grounding"] = {
            field: {
                "status": r.status,
                "payload_value": r.payload_value,
                "candidates_found": r.candidates_found,
            }
            for field, r in numeric_results.items()
        }
    return has_numeric_mismatch(numeric_results)


def _build_context(
    db,
    record: NormalizedSourceRecord,
    bill_context: dict[str, Any] | None = None,
) -> dict:
    """Build context dict for an extraction agent.

    Includes Orrick key_requirements and enforcement metadata when available,
    giving the model richer signal about what the passage is about.

    If bill_context is provided (from bill_context.get_or_build_bill_context),
    injects bill-level definitions, scope, structure, and defined terms so
    agents can resolve cross-references and understand actor terminology.
    """
    dv = record.document_version
    df = dv.family if dv else None
    s = df.source if df else None
    ctx: dict[str, Any] = {
        "document_title": df.canonical_title if df else None,
        "jurisdiction": s.jurisdiction_code if s else None,
        "jurisdiction_name": s.jurisdiction_name if s else None,
        "short_cite": df.short_cite if df else None,
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

    # Inject Orrick tracker metadata as context when available.
    # orrick_summary is a combined field written by local_ingest/enrich_orrick
    # that captures whichever of key_requirements / enforcement_penalties was
    # non-empty in the source CSV.  Use it as a fallback so no Orrick text is
    # silently discarded when only one column was populated.
    if df and df.metadata_:
        bill_id = df.metadata_.get("bill_id")
        if bill_id:
            ctx["bill_id"] = bill_id
        orrick_summary = (df.metadata_.get("orrick_summary") or "").strip()
        key_reqs = (df.metadata_.get("key_requirements") or "").strip()
        enforcement = (df.metadata_.get("enforcement_penalties") or "").strip()
        # Fall back to combined summary when the individual column is empty
        if not key_reqs:
            key_reqs = orrick_summary
        if key_reqs:
            ctx["key_requirements"] = key_reqs
        if enforcement:
            ctx["enforcement_summary"] = enforcement
        ai_scope = df.metadata_.get("ai_scope_summary")
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

    # Inject bill-level context (definitions, scope, enforcement, structure, defined terms)
    if bill_context:
        if bill_context.get("definitions"):
            ctx["bill_definitions"] = bill_context["definitions"]
        if bill_context.get("scope"):
            ctx["bill_scope"] = bill_context["scope"]
        if bill_context.get("enforcement"):
            ctx["bill_enforcement"] = bill_context["enforcement"]
        if bill_context.get("structure"):
            ctx["bill_structure"] = bill_context["structure"]
        if bill_context.get("defined_terms"):
            ctx["defined_terms"] = bill_context["defined_terms"]

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


def _text_hash(text: str) -> str:
    """Compute sha256[:24] of passage text for attempt-state deduplication."""
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def _payload_hash(payload: dict) -> str:
    """Compute a stable SHA-256 hash of an extraction payload for dedup.

    Strips internal meta keys (``_prompt_hash``, ``_model_id``, etc.) and
    ``evidence_spans`` so that the hash reflects only the substantive content.
    """
    clean = {
        k: v for k, v in sorted(payload.items())
        if not k.startswith("_") and k != "evidence_spans"
    }
    return hashlib.sha256(
        json.dumps(clean, sort_keys=True, default=str).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Agent routing — delegated to routing.py (pure, testable functions)
# ---------------------------------------------------------------------------

from src.ingestion.routing import (  # noqa: E402
    route_by_signal,
    select_agent_names,
)


def _select_agents_for_passage(
    text: str,
    all_agents: dict[str, BaseExtractionAgent],
    triage_result=None,
) -> dict[str, BaseExtractionAgent]:
    """Select which agents to run for a passage (thin wrapper around routing.py).

    Delegates all routing logic to select_agent_names() which is pure and
    unit-testable independently of agent objects.
    """
    selected_names = select_agent_names(
        text,
        set(all_agents.keys()),
        triage_result=triage_result,
        recall_sample_rate=settings.triage_recall_sample_rate,
    )
    if not selected_names:
        return {}
    return {k: v for k, v in all_agents.items() if k in selected_names}


def _route_agents_by_signal(
    text_lower: str,
    all_agents: dict[str, BaseExtractionAgent],
    triage_result,
) -> dict[str, BaseExtractionAgent] | None:
    """Thin wrapper for backward compatibility — delegates to routing.route_by_signal."""
    result = route_by_signal(text_lower, set(all_agents.keys()), triage_result)
    if result is None:
        return None
    return {k: v for k, v in all_agents.items() if k in result}


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


def _scale_tokens_for_passage(passage_len: int, configured_max: int) -> int:
    """Scale the agent token budget down for short passages.

    LLM inference time is roughly linear with max_tokens (the model streams
    until it hits the budget or produces EOS).  Short legislative passages
    will never fill 8 k output tokens — scaling down avoids wasted GPU time.

    Scaling tiers (based on passage length in characters):
      < 500  chars → 50 % of budget  (single sub-clause, header, citation)
      500-1500     → 75 %            (typical paragraph / dense section)
      ≥ 1500       → 100 %           (long provision; full budget needed)

    Floor of 2048 tokens.  Reasoning models (Gemma, DeepSeek-R1, Qwen3)
    have their budget doubled in llm_provider.py, so the effective minimum
    is 4096 — enough for a ~2 k think block plus JSON output.
    """
    if passage_len < 500:
        scale = 0.50
    elif passage_len < 1500:
        scale = 0.75
    else:
        scale = 1.0
    return max(2048, int(configured_max * scale))


def _run_agent(
    agent_name: str,
    agent: BaseExtractionAgent,
    passage: str,
    context: dict,
    call_max_tokens: int | None = None,
) -> tuple[str, ExtractionResult | Exception, int]:
    """Run a single agent (designed for ThreadPoolExecutor).

    Returns (agent_name, result_or_exception, duration_ms).
    call_max_tokens: pre-scaled token budget for this specific passage.
    """
    t0 = time.perf_counter()
    try:
        result = agent.extract(passage, context, call_max_tokens=call_max_tokens)
        return agent_name, result, int((time.perf_counter() - t0) * 1000)
    except Exception as e:
        return agent_name, e, int((time.perf_counter() - t0) * 1000)


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


# EA6-3: obligation and rights_protection each populate interpretation_risks
# inline during their own primary extraction pass (the retired standalone
# AmbiguityAgent's replacement) — neither agent sees the other's output, so
# both independently flagging the same ambiguous term on the same passage
# (e.g. both notice "reasonable" is a vague_term) is common, not a bug in
# either agent. Fixed precedence order (not thread-completion order, which
# `agent_results` is populated in via as_completed()) so the same passage
# dedupes the same way on every run.
_INTERPRETATION_RISK_AGENTS = ("obligation", "rights_protection")


def _dedupe_interpretation_risks(
    agent_results: list[tuple[str, str, int, ExtractionResult | Exception, int]],
) -> None:
    """Cross-agent, in-place dedup of interpretation_risks for one passage.

    Mutates each affected item's `interpretation_risks` list directly on the
    ExtractionResult objects in `agent_results`, before the persistence loop
    below turns them into Extraction rows — a passage merged across multiple
    source_records would otherwise re-derive (and re-count) the same
    duplicate for every source_record if this ran later per-row instead.
    """
    by_name = {
        name: result
        for name, _content_hash, _attempt_id, result, _duration_ms in agent_results
        if name in _INTERPRETATION_RISK_AGENTS
    }
    if len(by_name) < 2:
        return  # need both agents present on this passage for a cross-agent dup

    seen: set[tuple[str, str]] = set()
    for agent_name in _INTERPRETATION_RISK_AGENTS:
        result = by_name.get(agent_name)
        if result is None or isinstance(result, Exception):
            continue
        for item in result.extractions:
            risks = item.get("interpretation_risks")
            if not risks:
                continue
            deduped = []
            for risk in risks:
                key = (str(risk.get("term", "")).strip().lower(), risk.get("risk_type"))
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(risk)
            item["interpretation_risks"] = deduped


def extract_single_record(
    db,
    passage: MergedPassage,
    agents: dict[str, BaseExtractionAgent],
    extraction_job: ExtractionJob | None = None,
    parse_quality: float | None = None,
    token_usage: TokenUsageSummary | None = None,
    succeeded_attempts: dict[tuple[int, str], set[str]] | None = None,
    tracker: FailureTracker | None = None,
    bill_context: dict[str, Any] | None = None,
    run_id: int | None = None,
) -> int:
    """Run selected agents against a passage.

    Returns extraction count. Agents are selected based on content signals.

    To avoid VRAM thrashing when using local models via LM Studio, agents are
    grouped by their model_override and each group runs sequentially.  Agents
    within the same model group still run concurrently.

    Deduplication is driven by ExtractionAttempt state: an agent is skipped
    when there is a prior 'succeeded' attempt for the same (source_record_id,
    agent_name) with a matching passage text hash.  This correctly handles
    agents that abstained (produced 0 extractions) — they were previously
    inferred as un-run because no Extraction row existed.

    Args:
        succeeded_attempts: Preloaded mapping of
            {(source_record_id, agent_name): set[input_text_hash]} for all
            prior succeeded attempts.  None disables deduplication.
        tracker: Shared FailureTracker that monitors consecutive and total
            failure rates across the full extraction run.  Raises
            CircuitBreakerTripped when thresholds are exceeded.
        bill_context: Pre-built bill-level context (definitions, scope,
            structure, defined_terms) from bill_context.get_or_build_bill_context.
    """
    record = passage.primary_record
    extractions_created = 0

    # Compute passage text hash once for all agent checks.
    passage_text_hash = _text_hash(passage.text)

    # Fast-path: if every agent has a prior succeeded attempt with the same
    # text hash, this passage was fully processed — skip entirely.
    if succeeded_attempts is not None and all(
        passage_text_hash in succeeded_attempts.get((record.id, name), set())
        for name in agents
    ):
        logger.debug("passage_fully_deduped", record_id=record.id)
        return 0

    ctx = _build_context(db, record, bill_context=bill_context)

    # Import monitor for live event emission (used by the jurisdiction-skip
    # path below as well as result processing).
    from src.core.extraction_monitor import get_monitor
    monitor = get_monitor()

    # Jurisdiction cross-check: skip if document state doesn't match law state.
    # Return -1 (not 0) so the caller can distinguish a jurisdiction skip from
    # a legitimate zero-extraction passage and surface it in run_summary.
    if not _check_jurisdiction(db, record, passage.text):
        monitor.record_passage_complete(
            record_id=record.id,
            section_path=record.section_path,
            extraction_count=0,
        )
        return -1

    # Select agents based on passage content + triage signals
    triage = getattr(record, "triage_result", None)
    # triage_result is a list-like backref; grab first if present
    if isinstance(triage, list):
        triage = triage[0] if triage else None
    selected_agents = _select_agents_for_passage(passage.text, agents, triage_result=triage)

    if token_usage is not None:
        token_usage.agents_skipped += len(agents) - len(selected_agents)

    if not selected_agents:
        logger.debug("all_agents_skipped", record_id=record.id)
        return 0

    # Record skipped attempts for excluded agents (RR1c)
    excluded_agent_names = set(agents.keys()) - set(selected_agents.keys())
    for excluded_name in excluded_agent_names:
        _skip_attempt(db, record.id, excluded_name, run_id=run_id,
                      input_text_hash=passage_text_hash)

    # Calculate per-passage token budget.  Short passages never produce
    # large outputs — scaling down avoids wasted GPU time waiting for tokens
    # the model would never fill.  The base budget comes from each agent's
    # max_tokens_override (pre-doubling for reasoning models).
    passage_len = len(passage.text)

    # Group agents by model to minimise LM Studio VRAM model swaps.
    # Each group runs sequentially; agents within a group run concurrently.
    model_groups = _group_agents_by_model(selected_agents)
    agent_results: list[tuple[str, str, ExtractionResult | Exception, int]] = []

    for group in model_groups:
        # RR6b: cap concurrency to avoid VRAM thrashing on single-GPU LM Studio
        concurrency = min(len(group), settings.max_concurrent_agents_per_model)
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {}
            for agent_name, agent in group.items():
                # Attempt-state dedup: skip when a prior run succeeded on the
                # same passage text.  This correctly handles abstentions (0
                # extractions) which left no Extraction row and were therefore
                # invisible to the old hash-set approach.
                if succeeded_attempts is not None and (
                    passage_text_hash in succeeded_attempts.get((record.id, agent_name), set())
                ):
                    logger.debug(
                        "extraction_deduplicated",
                        agent=agent_name,
                        record_id=record.id,
                    )
                    _skip_attempt(db, record.id, agent_name, run_id=run_id,
                                  input_text_hash=passage_text_hash)
                    continue

                # Scale token budget to passage length so short passages don't
                # consume GPU time waiting for tokens they'll never produce.
                base_tokens = agent.max_tokens_override or settings.extraction_max_tokens
                scaled_tokens = _scale_tokens_for_passage(passage_len, base_tokens)

                attempt_id = _begin_attempt(db, record.id, agent_name, run_id=run_id,
                                            input_text_hash=passage_text_hash)
                future = executor.submit(
                    _run_agent, agent_name, agent, passage.text, ctx,
                    call_max_tokens=scaled_tokens,
                )
                futures[future] = (agent_name, passage_text_hash, attempt_id)

            # Collect results for this model group
            for future in as_completed(futures):
                agent_name, content_hash, attempt_id = futures[future]
                name, result, duration_ms = future.result()
                agent_results.append((name, content_hash, attempt_id, result, duration_ms))

    _dedupe_interpretation_risks(agent_results)

    # Process results (back on main thread for DB writes)
    for name, content_hash, attempt_id, result, duration_ms in agent_results:
        if isinstance(result, Exception):
            logger.error(
                "agent_extraction_failed",
                agent=name,
                record_id=record.id,
                error=str(result),
                section_path=record.section_path,
            )
            error_type = _classify_llm_error(result)
            _finish_attempt(db, attempt_id, "failed", error_message=str(result))
            _persist_pipeline_event(
                db, "agent_error",
                run_id=run_id,
                source_record_id=record.id,
                agent_name=name,
                error_message=str(result),
                details={"error_type": error_type},
            )
            # Record for retry, tagged with the classified error type so the
            # dashboard can distinguish auth/quota failures from model errors.
            _record_failed_attempt(
                db, record.id, name, error_type, str(result),
                extraction_job_id=extraction_job.id if extraction_job else None,
                run_id=run_id,
            )
            if tracker is not None:
                tracker.record_failure(
                    f"agent={name} record={record.id}: {result}"
                )
            if token_usage is not None:
                token_usage.error_count += 1
            monitor.record_agent_result(
                agent_name=name,
                record_id=record.id,
                error=str(result),
            )
            continue

        # Successful call — reset consecutive failure counter
        if tracker is not None:
            tracker.record_success()

        # Track token usage (clause-level; bill-level tracked in _run_bill_level_agents)
        if token_usage is not None:
            token_usage.add_clause(result.input_tokens, result.output_tokens)

        # Log structured result
        logger.info(
            "agent_extraction_completed",
            agent=name,
            record_id=record.id,
            extraction_count=len(result.extractions),
            abstained=result.abstention is not None,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_ms=duration_ms,
            template_version=result.template_version,
        )

        if result.abstention is not None:
            if token_usage is not None:
                token_usage.abstention_count += 1
            monitor.record_agent_result(
                agent_name=name,
                record_id=record.id,
                abstained=True,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                duration_ms=duration_ms,
            )
            _finish_attempt(db, attempt_id, "succeeded", extractions_produced=0)
            continue

        # Process each extraction from the multi-extraction result
        default_type = AGENT_EXTRACTION_TYPES[name][0]
        schema_class = EXTRACTION_TYPE_SCHEMAS.get(default_type.value)

        # Write extractions against all source records in the merged passage
        agent_extractions_before = extractions_created
        for source_record in passage.source_records:
            for item in result.extractions:
                # Use a savepoint so a single failed INSERT (e.g. missing
                # enum value) doesn't roll back the entire transaction
                # and destroy the ExtractionJob row + prior extractions.
                sp = db.begin_nested()
                try:
                    resolved_type = _discriminate_extraction_type(name, item)

                    # --- Payload-level deduplication (indexed hash lookup) ---
                    p_hash = _payload_hash(item)
                    if _payload_hash_available:
                        existing_dup = db.scalars(
                            select(Extraction.id).where(
                                Extraction.source_record_id == source_record.id,
                                Extraction.extraction_type == resolved_type,
                                Extraction.payload_hash == p_hash,
                            ).limit(1)
                        ).first()
                        if existing_dup:
                            logger.debug(
                                "extraction_payload_duplicate_skipped",
                                agent=name,
                                record_id=source_record.id,
                                extraction_type=resolved_type.value,
                            )
                            if _model_agreement_available:
                                db.execute(
                                    text(
                                        "UPDATE extractions "
                                        "SET model_agreement_count = model_agreement_count + 1 "
                                        "WHERE id = :eid"
                                    ),
                                    {"eid": existing_dup},
                                )
                            continue

                    # Inject provenance into each evidence span so spans carry
                    # the source URL and section path without extra joins.
                    _src_url = ctx.get("primary_source_url")
                    _sec_anchor = ctx.get("section_path")
                    evidence = [
                        {**s, "source_url": _src_url, "section_anchor": _sec_anchor}
                        if isinstance(s, dict) else s
                        for s in item.get("evidence_spans", [])
                    ]
                    orrick_sim = validate_extraction_against_orrick(item, ctx)
                    confidence = compute_confidence(
                        schema_valid=True,
                        evidence_spans=evidence,
                        extraction_payload=item,
                        schema_class=schema_class,
                        parse_quality_score=parse_quality,
                        orrick_similarity=orrick_sim,
                        passage_text=passage.text,
                        iapp_has_data=_iapp_has_data_for_ctx(ctx),
                    )
                    # EA2-3: a truncated or heavily-repaired raw response may
                    # be missing content regardless of how well the fields it
                    # DID produce score — cap the tier so that defect can't
                    # be hidden behind an otherwise-good confidence score.
                    if result.truncated or result.was_repaired:
                        confidence.total_score, confidence.tier = cap_at_tier_c(
                            confidence.total_score, confidence.tier,
                        )

                    extraction_meta: dict = {}
                    if result.truncated:
                        extraction_meta["truncated"] = True
                    if result.was_repaired:
                        extraction_meta["was_repaired"] = True
                    if result.model_reasoning:
                        extraction_meta["model_reasoning"] = result.model_reasoning[:2000]
                    extraction_meta["confidence_breakdown"] = {
                        "schema_validity": confidence.schema_validity,
                        "evidence_grounding": confidence.evidence_grounding,
                        "completeness": confidence.completeness,
                        "source_quality": confidence.source_quality,
                        "orrick_alignment": confidence.orrick_alignment,
                        "cross_validation": confidence.cross_validation,
                        "orrick_gated": confidence.orrick_gated,
                        "source_grounding_score": confidence.source_grounding_score,
                        "tracker_alignment_score": confidence.tracker_alignment_score,
                        "schema_completeness_score": confidence.schema_completeness_score,
                    }
                    numeric_mismatch = _apply_numeric_grounding(
                        item, evidence, extraction_meta,
                    )

                    # Generate plain-English summary from the verified payload
                    try:
                        from src.core.summary_generator import generate_summary
                        ext_type_str = resolved_type.value if hasattr(resolved_type, "value") else str(resolved_type)
                        extraction_meta["plain_summary"] = generate_summary(
                            ext_type_str, item, ctx.get("jurisdiction"),
                        )
                    except Exception:
                        pass  # Summary is presentation-only; don't block extraction

                    extraction_kwargs: dict[str, Any] = dict(
                        source_record_id=source_record.id,
                        extraction_type=resolved_type,
                        agent_name=name,
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
                    if _token_columns_available:
                        extraction_kwargs["input_tokens"] = result.input_tokens
                        extraction_kwargs["output_tokens"] = result.output_tokens
                        extraction_kwargs["duration_ms"] = duration_ms
                    if _payload_hash_available:
                        extraction_kwargs["payload_hash"] = p_hash
                    if _run_id_available and run_id is not None:
                        extraction_kwargs["run_id"] = run_id
                    extraction = Extraction(**extraction_kwargs)
                    db.add(extraction)
                    db.flush()

                    review_priority = _confidence_to_priority(confidence.tier)
                    if numeric_mismatch or result.truncated or result.was_repaired:
                        # Tier-C alone is still auto-publish-eligible under the
                        # confidence-only sync gate (P3) — force max-urgency
                        # review so a truncated/repaired/numerically-mismatched
                        # extraction actually gets a human look, not just a
                        # lower (but still passable) tier.
                        review_priority = max(review_priority, 3)
                    db.add(ReviewQueueItem(
                        extraction_id=extraction.id,
                        priority=review_priority,
                        status=ReviewStatus.pending,
                    ))
                    extractions_created += 1
                    if token_usage is not None:
                        token_usage.extraction_item_count += 1

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

                    # Commit the savepoint so this extraction persists
                    # even if a later extraction in this batch fails.
                    sp.commit()

                    # Emit to live monitor
                    monitor.record_agent_result(
                        agent_name=name,
                        record_id=source_record.id,
                        success=True,
                        extraction_count=1,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        duration_ms=duration_ms,
                        confidence_tier=confidence.tier,
                        truncated=result.truncated,
                    )

                except Exception as e:
                    sp.rollback()
                    logger.error(
                        "extraction_record_failed",
                        agent=name,
                        record_id=source_record.id,
                        error=str(e),
                    )
                    # Record for retry and feed the circuit breaker
                    _record_failed_attempt(
                        db, source_record.id, name, "db_error", str(e),
                        extraction_job_id=extraction_job.id if extraction_job else None,
                        run_id=run_id,
                    )
                    if tracker is not None:
                        tracker.record_failure(
                            f"db_insert agent={name} record={source_record.id}: {e}"
                        )

        # Mark attempt as succeeded with the number of extractions produced
        agent_produced = extractions_created - agent_extractions_before
        _finish_attempt(db, attempt_id, "succeeded", extractions_produced=agent_produced)
        _persist_pipeline_event(
            db, "agent_success",
            run_id=run_id,
            source_record_id=record.id,
            agent_name=name,
            extraction_count=agent_produced,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_ms=duration_ms,
        )

    # Record passage-level completion to monitor
    monitor.record_passage_complete(
        record_id=record.id,
        section_path=record.section_path,
        extraction_count=extractions_created,
    )

    return extractions_created


def _get_neighbor_texts(
    record,
    siblings: list,
    window: int = 1,
) -> list[str]:
    """Return text from neighboring passages (before/after) in the same document.

    Gives the triage model surrounding context so it can tell whether a generic
    section lives inside an AI-specific part of the bill.
    """
    if not siblings or len(siblings) <= 1:
        return []

    # Find index of this record in the sibling list (ordered by ordinal)
    idx = None
    for j, r in enumerate(siblings):
        if r.id == record.id:
            idx = j
            break
    if idx is None:
        return []

    texts: list[str] = []
    for offset in range(-window, window + 1):
        if offset == 0:
            continue
        neighbor_idx = idx + offset
        if 0 <= neighbor_idx < len(siblings):
            txt = siblings[neighbor_idx].text_content or ""
            if txt.strip():
                texts.append(txt)
    return texts


def run_triage(
    db,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Run section triage on all untriaged passages.

    Triages passages from completed ingestion jobs that don't yet have
    a SectionTriageResult. This is the same logic as the inline triage
    in run_extraction(), extracted into a standalone function so it can
    be triggered independently from the dashboard.

    Returns:
        Summary dict with relevant/uncertain/skipped/total counts.
    """
    from src.agents.section_triage import triage_passage

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    # Find all passages from completed ingestion jobs that haven't been triaged
    triaged_ids = select(SectionTriageResult.source_record_id)
    records = db.scalars(
        select(NormalizedSourceRecord)
        .where(
            NormalizedSourceRecord.id.notin_(triaged_ids),
            NormalizedSourceRecord.document_version_id.in_(
                select(IngestionJob.document_version_id).where(
                    IngestionJob.status.in_(["completed", "fetched"])
                )
            ),
        )
    ).all()

    # Filter out tiny passages
    records = [r for r in records if len(r.text_content or "") >= MIN_PASSAGE_LENGTH]

    summary = {
        "total": len(records),
        "relevant": 0,
        "uncertain": 0,
        "skipped": 0,
    }

    if not records:
        _log("No untriaged passages found.")
        return summary

    # Get LLM provider for Layer 2/3 triage (keyword-only passages are free).
    # Must use the EXTRACTION provider (driven by the dashboard backend toggle),
    # not the discovery provider: section_triage reads the triage model name from
    # get_config().get("triage") — the active backend's config block. If the
    # provider here came from a different toggle (e.g. local LM Studio) while the
    # model name came from the NVIDIA block, the local server would be called with
    # an NVIDIA model name and fail with "No models loaded".
    llm_provider = None
    try:
        from src.core.llm_provider import get_extraction_provider
        llm_provider = get_extraction_provider()
        _log(f"Triaging {len(records)} passages with LLM fallback ({llm_provider.model_id})...")
    except Exception as e:
        _log(f"Triaging {len(records)} passages (keyword-only, no LLM: {e})...")

    # Pre-build bill-level context per document_version so every passage in
    # the same bill shares definitions/scope/structure context.
    from itertools import groupby
    from operator import attrgetter

    from src.core.bill_context import get_or_build_bill_context

    # Group records by document_version_id so we build context once per bill
    records_sorted = sorted(records, key=attrgetter("document_version_id", "ordinal"))
    _bill_ctx_cache: dict[int, dict] = {}
    _dv_records: dict[int, list] = {}
    for dv_id, grp in groupby(records_sorted, key=attrgetter("document_version_id")):
        _dv_records[dv_id] = list(grp)
        try:
            _bill_ctx_cache[dv_id] = get_or_build_bill_context(db, dv_id, records=_dv_records[dv_id])
        except Exception:
            logger.debug("bill_context_build_failed", dv_id=dv_id, exc_info=True)
            _bill_ctx_cache[dv_id] = {}

    for i, record in enumerate(records):
        try:
            ctx = _build_context(db, record)
            # Inject bill-level context (definitions, scope, enforcement, structure)
            bill_ctx = _bill_ctx_cache.get(record.document_version_id, {})
            if bill_ctx:
                if bill_ctx.get("definitions"):
                    ctx["bill_definitions"] = bill_ctx["definitions"]
                if bill_ctx.get("scope"):
                    ctx["bill_scope"] = bill_ctx["scope"]
                if bill_ctx.get("enforcement"):
                    ctx["bill_enforcement"] = bill_ctx["enforcement"]
                if bill_ctx.get("structure"):
                    ctx["bill_structure"] = bill_ctx["structure"]
                if bill_ctx.get("defined_terms"):
                    ctx["defined_terms"] = bill_ctx["defined_terms"]

            # Gather neighboring passage texts for surrounding context
            siblings = _dv_records.get(record.document_version_id, [])
            neighbors = _get_neighbor_texts(record, siblings)

            result = triage_passage(
                record.text_content, ctx, llm_provider=llm_provider, neighbors=neighbors,
                record_id=record.id,
            )

            triage_row = SectionTriageResult(
                source_record_id=record.id,
                decision=TriageDecision(result.decision),
                method=TriageMethod(result.method),
                confidence=result.confidence,
                matched_keywords=result.matched_keywords,
                orrick_terms_checked=result.orrick_terms_checked,
                llm_reasoning=result.llm_reasoning,
                ai_signals=result.ai_signals,
                pdf_quality_score=result.pdf_quality_score,
                quality_flags=result.quality_flags,
                model_id=result.model_id,
            )
            db.add(triage_row)

            if result.decision == "not_relevant":
                summary["skipped"] += 1
            elif result.decision == "relevant":
                summary["relevant"] += 1
            else:
                summary["uncertain"] += 1
        except Exception as exc:
            logger.error("triage_passage_failed", record_id=record.id, exc_info=True)
            from src.agents.section_triage import _log_triage_warning
            _log_triage_warning(
                "passage_exception", f"Unhandled exception: {exc}",
                record_id=record.id,
            )
            db.rollback()
            # Record as uncertain/passthrough so it doesn't block extraction
            try:
                triage_row = SectionTriageResult(
                    source_record_id=record.id,
                    decision=TriageDecision.uncertain,
                    method=TriageMethod.passthrough,
                    confidence=0.0,
                    quality_flags=["triage_error"],
                )
                db.add(triage_row)
                db.commit()
                summary["uncertain"] += 1
            except Exception:
                logger.error("triage_error_record_failed", record_id=record.id, exc_info=True)
                db.rollback()

        # Commit in batches of 10 for progress visibility in the dashboard
        if (i + 1) % 10 == 0:
            db.commit()
            _log(
                f"Triaged {i + 1}/{len(records)}: "
                f"{summary['relevant']} relevant, "
                f"{summary['uncertain']} uncertain, "
                f"{summary['skipped']} skipped"
            )

    db.commit()
    _log(
        f"Triage complete: {summary['relevant']} relevant, "
        f"{summary['uncertain']} uncertain, {summary['skipped']} skipped "
        f"out of {summary['total']} passages"
    )
    return summary


def run_retry_failed_triage(
    db,
    on_progress=None,
) -> dict:
    """Delete triage_error rows and re-run triage only for those passages.

    Returns a dict with keys: cleared, total, relevant, uncertain, skipped.
    """
    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    # Find all triage rows where the LLM call itself failed (rate-limit, timeout,
    # connection error). These are recorded with quality_flags containing "llm_error"
    # by triage_passage()'s except block, and method=passthrough.
    # ("triage_error" is the outer run_triage fallback — rarely hit.)
    error_ids: list[int] = list(db.scalars(
        select(SectionTriageResult.source_record_id)
        .where(
            SectionTriageResult.method == "passthrough",
            SectionTriageResult.quality_flags.contains(["llm_error"]),
        )
    ).all())

    cleared = len(error_ids)
    if cleared == 0:
        _log("No failed triage rows to retry.")
        return {"cleared": 0, "total": 0, "relevant": 0, "uncertain": 0, "skipped": 0}

    _log(f"Clearing {cleared} llm_error triage rows so they can be re-triaged...")
    from sqlalchemy import delete as sa_delete
    db.execute(
        sa_delete(SectionTriageResult)
        .where(SectionTriageResult.source_record_id.in_(error_ids))
    )
    db.commit()

    # run_triage picks up all passages that no longer have a triage row — including
    # these freshly cleared ones and any that were never triaged.
    summary = run_triage(db, on_progress=on_progress)
    summary["cleared"] = cleared
    return summary


def _get_bill_level_agents():
    """Lazily import and instantiate available bill-level agents.

    Skips any that fail to import (e.g. not yet implemented).
    """
    import importlib
    agents = []
    for dotted_path in _BILL_LEVEL_AGENT_CLASSES:
        try:
            module_path, class_name = dotted_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            agents.append(cls())
        except (ImportError, AttributeError):
            pass  # Agent not yet implemented — skip silently
    return agents


def _run_bill_level_agents(
    db,
    document_version_id: int,
    passages: list,
    bill_context: dict,
    _log=None,
    token_usage: TokenUsageSummary | None = None,
    run_id: int | None = None,
) -> int:
    """Run all bill-level agents for one document version.

    Assembles full bill text from sorted passages, runs each agent,
    upserts results to bill_level_extractions.  Returns count of agents
    that produced a non-error payload.

    Option B — Orrick efficiency: before running any LLM call, parse
    structured facts from the Orrick text in bill_context.  Agents whose
    domain is already covered by Orrick are skipped; a synthetic
    BillLevelExtraction is written with model_id="orrick_facts_parser".
    """
    from src.agents.bill_level_base import BillLevelResult
    from src.db.models import BillLevelExtraction, ReviewStatus
    from src.ingestion.orrick_facts_parser import parse_orrick_facts

    if _log is None:
        _log = lambda msg: None

    agents = _get_bill_level_agents()
    if not agents:
        return 0

    # Assemble full bill text in document order
    sorted_passages = sorted(passages, key=lambda r: r.ordinal)
    full_text = "\n\n".join(
        p.text_content for p in sorted_passages if p.text_content
    )

    if not full_text.strip():
        return 0

    # Parse Orrick facts once for all agents
    orrick = parse_orrick_facts(bill_context)

    # Map agent_name → (orrick_payload, covered_flag)
    _ORRICK_COVERAGE: dict[str, tuple[dict, bool]] = {
        "enforcement_agent": (orrick.enforcement, orrick.enforcement_covered),
        "applicability_agent": (orrick.applicability, orrick.applicability_covered),
        "compliance_timeline_agent": (orrick.timeline, orrick.timeline_covered),
    }

    succeeded = 0
    for agent in agents:
        try:
            orrick_payload, is_covered = _ORRICK_COVERAGE.get(
                agent.agent_name, ({}, False)
            )

            if is_covered:
                # Orrick has enough data — skip the LLM call
                result = BillLevelResult(
                    payload=orrick_payload,
                    model_id="orrick_facts_parser",
                    input_tokens=0,
                    output_tokens=0,
                    raw_output="",
                    truncated=False,
                )
                _log(f"  [bill-level] {agent.agent_name}: skipped (Orrick covered)")
                logger.info(
                    "bill_level_orrick_skip",
                    agent=agent.agent_name,
                    document_version_id=document_version_id,
                )
            else:
                result = agent.extract_bill(full_text, context=bill_context)

            # Upsert: one row per (document_version_id, agent_name)
            from sqlalchemy import select as sa_select
            existing = db.scalars(
                sa_select(BillLevelExtraction).where(
                    BillLevelExtraction.document_version_id == document_version_id,
                    BillLevelExtraction.agent_name == agent.agent_name,
                )
            ).first()

            has_error = "_error" in result.payload
            if existing:
                existing.payload = result.payload
                existing.model_id = result.model_id
                existing.input_tokens = result.input_tokens
                existing.output_tokens = result.output_tokens
                existing.truncated = result.truncated
                existing.review_status = ReviewStatus.pending
                if _run_id_available and run_id is not None:
                    existing.run_id = run_id
            else:
                _bill_kwargs: dict = dict(
                    document_version_id=document_version_id,
                    agent_name=agent.agent_name,
                    payload=result.payload,
                    model_id=result.model_id,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    truncated=result.truncated,
                    review_status=ReviewStatus.pending,
                )
                if _run_id_available and run_id is not None:
                    _bill_kwargs["run_id"] = run_id
                db.add(BillLevelExtraction(**_bill_kwargs))

            # Track bill-level tokens in the shared usage summary so run_summary
            # reflects the full run cost (passage-level + bill-level).
            if token_usage is not None and result.model_id != "orrick_facts_parser":
                token_usage.add_bill_level(result.input_tokens, result.output_tokens)

            if not has_error:
                succeeded += 1
                if result.model_id != "orrick_facts_parser":
                    _log(f"  [bill-level] {agent.agent_name}: OK")
            else:
                _log(f"  [bill-level] {agent.agent_name}: failed — {result.payload.get('_error', '')[:100]}")

        except Exception as e:
            logger.error(
                "bill_level_agent_error",
                agent=agent.agent_name,
                document_version_id=document_version_id,
                error=str(e),
            )
            _log(f"  [bill-level] {agent.agent_name}: error — {e}")

    db.flush()
    return succeeded


def run_extraction(
    db,
    limit: int | None = None,
    on_progress: Callable[[str], None] | None = None,
    batch_mode: bool = False,
    purge: bool = False,
) -> dict:
    """Run extraction agents against all unprocessed passages.

    Idempotent: passages already fully extracted are skipped via per-agent
    content-hash deduplication.  Partially-extracted passages (where only
    some agents have run) have the missing agents filled in.

    Args:
        db: SQLAlchemy session
        limit: Max passages to process (None = all unprocessed)
        on_progress: Optional callback(message: str) for status updates
        batch_mode: Deprecated, ignored. Batch API has been archived.
        purge: Explicitly wipe all existing extractions before running.
            Must be set to True intentionally — never triggered automatically.

    Returns:
        Summary dict with counts and token usage.
    """
    # Clear any stale cancellation/pause from a previous run
    clear_cancel()
    clear_pause()
    global _last_passage_at
    _last_passage_at = 0.0

    # --- Explicit purge (opt-in only) ---
    # Never runs automatically. The caller must pass purge=True to wipe
    # existing extractions. Idempotent runs rely on per-agent dedup instead.
    from sqlalchemy import delete as sa_delete
    if purge:
        old_ext_count = db.scalar(select(func.count()).select_from(Extraction)) or 0
        if old_ext_count > 0:
            if on_progress:
                on_progress(f"Purging {old_ext_count} extractions (explicit purge requested)...")
            # Delete in FK order
            db.execute(sa_delete(ApplicabilityCondition))
            db.execute(sa_delete(ObligationDependency))
            db.execute(sa_delete(ReviewAction))
            db.execute(sa_delete(ReviewQueueItem))
            try:
                db.execute(sa_delete(FailedExtractionAttempt))
            except Exception:
                pass
            db.execute(sa_delete(Extraction))
            db.execute(sa_delete(ExtractionJob))
            db.commit()
            if on_progress:
                on_progress(f"Purged {old_ext_count} old extractions. Starting fresh run.")

    # Open (or create) the active run folder.
    # Full runs purge all extractions first and start a fresh session.
    # Batch runs (with limit) accumulate into the existing active folder.
    from src.core.run_archiver import RunArchiver
    archiver = RunArchiver.start("extract", is_fresh_run=purge)

    agents = _get_agents()
    token_usage = TokenUsageSummary()

    # Create an ExtractionRun version record (Phase 1b).
    # Captures the git SHA, prompt versions, and model config for this run.
    # Skipped gracefully if the migration hasn't been applied yet.
    current_run_id: int | None = None
    try:
        import subprocess

        from src.db.models import ExtractionRun
        _git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, timeout=5
        ).strip()
    except Exception:
        _git_sha = None

    _run_type = "extract" if limit is None else "partial_extract"
    try:
        from src.db.models import ExtractionRun
        # Collect per-agent model and prompt version metadata
        _model_cfg: dict = {}
        _prompt_vers: dict = {}
        for _name, _agent in agents.items():
            _model_cfg[_name] = {
                "model": _agent.model_override,
                "max_tokens": _agent.max_tokens_override,
            }
            from src.agents.prompt_loader import get_template_version
            _prompt_vers[_name] = get_template_version(_name)

        _run = ExtractionRun(
            run_type=_run_type,
            status="running",
            is_serving=False,
            git_sha=_git_sha,
            model_config=_model_cfg,
            prompt_versions=_prompt_vers,
        )
        db.add(_run)
        db.flush()
        current_run_id = _run.id
        logger.info("extraction_run_created", run_id=current_run_id, git_sha=_git_sha)
    except Exception as _e:
        logger.warning("extraction_run_create_skipped", reason=str(_e))

    # Check whether optional columns exist (migrations may not have run yet)
    global _payload_hash_available, _token_columns_available, _run_id_available, _model_agreement_available
    try:
        _existing_cols = {
            c["name"] for c in sa_inspect(db.bind).get_columns("extractions")
        }
        _payload_hash_available = "payload_hash" in _existing_cols
        _token_columns_available = "duration_ms" in _existing_cols
        _run_id_available = "run_id" in _existing_cols
        _model_agreement_available = "model_agreement_count" in _existing_cols
    except Exception:
        _payload_hash_available = False
        _token_columns_available = False
        _run_id_available = False
        _model_agreement_available = False

    # Build attempt-state dedup table from ExtractionAttempt.
    #
    # Key:   (source_record_id, agent_name)
    # Value: set of input_text_hash values from prior succeeded attempts
    #
    # An agent is skipped on the current run when the passage text hash
    # matches a prior succeeded attempt — regardless of whether that attempt
    # produced extractions (abstentions are now correctly tracked).
    #
    # This replaces the old existing_hashes approach that inferred agent state
    # from Extraction rows, which missed abstaining agents entirely.
    succeeded_attempts: dict[tuple[int, str], set[str]] = {}
    _attempt_rows = db.execute(
        select(
            ExtractionAttempt.source_record_id,
            ExtractionAttempt.agent_name,
            ExtractionAttempt.input_text_hash,
        )
        .where(ExtractionAttempt.status == "succeeded")
        .where(ExtractionAttempt.input_text_hash.isnot(None))
        .distinct()
    ).all()
    for src_id, agent_name, text_hash in _attempt_rows:
        succeeded_attempts.setdefault((src_id, agent_name), set()).add(text_hash)
    if succeeded_attempts:
        logger.info(
            "attempt_state_loaded",
            unique_agent_passage_pairs=len(succeeded_attempts),
            total_attempt_rows=len(_attempt_rows),
        )
    del _attempt_rows

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    # Find passages that have been triaged as relevant or uncertain.
    # We include ALL such passages (not just those with zero extractions) so
    # that partially-extracted passages — where only some agents have run —
    # can have their missing agents filled in.  Per-agent dedup via
    # existing_hashes prevents re-running agents that already completed.
    # If triage hasn't been run yet, passages without any triage result are
    # excluded — the user must run "Triage Passages" first.
    triaged_relevant_ids = (
        select(SectionTriageResult.source_record_id)
        .where(SectionTriageResult.decision.in_([
            TriageDecision.relevant,
            TriageDecision.uncertain,
        ]))
    )
    query = (
        select(NormalizedSourceRecord)
        .where(
            NormalizedSourceRecord.id.in_(triaged_relevant_ids),
        )
        .distinct()
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
        "records_skipped_jurisdiction": 0,
        "passages_merged": 0,
        "agents_skipped_by_signal": 0,
        "token_usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_calls": 0,
            "scope": "passage-level + bill-level agents",
        },
    }

    if not records:
        total_passages = db.scalar(
            select(func.count()).select_from(NormalizedSourceRecord)
        ) or 0
        total_triaged = db.scalar(
            select(func.count()).select_from(SectionTriageResult)
        ) or 0
        if total_passages > 0 and total_triaged == 0:
            _log(
                f"No triaged passages found ({total_passages} passages exist). "
                f"Run 'Triage Passages' before extracting."
            )
        else:
            _log("No triaged-relevant passages found.")
        archiver.finalize(db, summary, run_id=current_run_id)
        return summary

    _log(f"Found {len(records)} triaged-relevant passages to extract from")

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

        # Build bill-level context (definitions, scope, structure) once per bill
        from src.core.bill_context import get_or_build_bill_context

        bill_ctx = get_or_build_bill_context(db, dv_id, records=dv_group)
        if bill_ctx.get("stats"):
            stats = bill_ctx["stats"]
            _log(
                f"  Bill context: {stats.get('definition_passages', 0)} definition sections, "
                f"{stats.get('scope_passages', 0)} scope sections, "
                f"{stats.get('defined_terms_count', 0)} defined terms"
            )

        job_extractions = 0
        job_failures = 0

        for i, passage in enumerate(merged_passages):
            # Update heartbeat so the dashboard can detect stuck runs.
            _last_passage_at = time.monotonic()

            # Check for cancellation between passages
            if is_cancelled():
                _log(f"\nExtraction terminated by user after {summary['records_processed']} passages.")
                extraction_job.status = "cancelled"
                extraction_job.completed_at = datetime.utcnow()
                db.commit()
                summary["total_extractions"] += job_extractions
                summary["cancelled"] = True
                _monitor.stop_run(cancelled=True)
                archiver.finalize(db, summary, run_id=current_run_id)
                return summary

            # Pause loop: sleep in short increments so cancel is still responsive.
            while is_paused() and not is_cancelled():
                time.sleep(0.25)

            try:
                count = extract_single_record(
                    db, passage, agents, extraction_job, parse_quality,
                    token_usage, succeeded_attempts, tracker,
                    bill_context=bill_ctx,
                    run_id=current_run_id,
                )
                if count == -1:
                    # Jurisdiction cross-check failed — passage was skipped.
                    # Count as processed (not failed) but surface it separately.
                    summary["records_skipped_jurisdiction"] += 1
                    extraction_job.records_processed += len(passage.source_records)
                    summary["records_processed"] += len(passage.source_records)
                    continue
                job_extractions += count
                extraction_job.records_processed += len(passage.source_records)
                summary["records_processed"] += len(passage.source_records)

                # Reset consecutive failure counter between passages.
                # One bad passage may fail multiple agents, but that shouldn't
                # cascade into tripping the breaker on the next passage.
                if tracker is not None and tracker.consecutive_failures > 0:
                    tracker._consecutive = 0

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
                archiver.finalize(db, summary, run_id=current_run_id)
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

        # Run bill-level agents for this document version (once per law)
        bill_level_count = _run_bill_level_agents(
            db, dv_id, dv_group, bill_ctx, _log=_log, token_usage=token_usage,
            run_id=current_run_id,
        )
        if bill_level_count:
            summary.setdefault("bill_level_extractions", 0)
            summary["bill_level_extractions"] += bill_level_count

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

    # Finalize token usage — named buckets + scope annotation.
    # Note: these are result tokens only; adaptive retries inside agent.extract()
    # are not counted here. See agent_stats.json for all-attempt cost.
    summary["token_usage"] = {
        "scope": "result_tokens_only__internal_retries_excluded",
        "clause_level_input_tokens": token_usage.clause_level_input_tokens,
        "clause_level_output_tokens": token_usage.clause_level_output_tokens,
        "bill_level_input_tokens": token_usage.bill_level_input_tokens,
        "bill_level_output_tokens": token_usage.bill_level_output_tokens,
        "total_input_tokens": token_usage.total_input_tokens,
        "total_output_tokens": token_usage.total_output_tokens,
        "total_tokens": token_usage.total_tokens,
        "llm_call_count": token_usage.llm_call_count,
    }
    summary["agent_invocations"] = {
        "scope": "outer_dispatch_count__internal_retries_excluded",
        "llm_call_count": token_usage.llm_call_count,
        "abstention_count": token_usage.abstention_count,
        "error_count": token_usage.error_count,
        "extraction_item_count": token_usage.extraction_item_count,
        "agents_skipped_by_signal": token_usage.agents_skipped,
    }

    _log(f"\nExtraction complete: {summary['total_extractions']} total extractions "
         f"({token_usage.extraction_item_count} items, "
         f"{token_usage.abstention_count} abstentions, {token_usage.error_count} errors)")
    _log(
        f"Token usage: {token_usage.total_input_tokens:,} in + "
        f"{token_usage.total_output_tokens:,} out = "
        f"{token_usage.total_tokens:,} total across {token_usage.llm_call_count} LLM calls"
    )
    _log(
        f"  clause-level: {token_usage.clause_level_input_tokens:,}+{token_usage.clause_level_output_tokens:,} | "
        f"bill-level: {token_usage.bill_level_input_tokens:,}+{token_usage.bill_level_output_tokens:,}"
    )
    _log(
        f"Savings: {token_usage.skipped_short} short passages skipped, "
        f"{token_usage.merged_passages} passages merged, "
        f"{token_usage.agents_skipped} agent calls avoided by signal filtering"
    )
    _monitor.stop_run()
    archiver.finalize(db, summary, run_id=current_run_id)

    # Finalize the ExtractionRun record: mark as serving, write summary (Phase 1b)
    if current_run_id is not None:
        try:
            from src.db.models import ExtractionRun
            _run_rec = db.get(ExtractionRun, current_run_id)
            if _run_rec:
                # Demote the previous serving run
                db.execute(
                    sa_update(ExtractionRun)
                    .where(ExtractionRun.is_serving.is_(True), ExtractionRun.id != current_run_id)
                    .values(is_serving=False)
                )
                _run_rec.status = "completed"
                _run_rec.is_serving = True
                _run_rec.completed_at = datetime.utcnow()
                _run_rec.extraction_count = summary.get("total_extractions", 0)
                _run_rec.passage_count = summary.get("records_processed", 0)
                _run_rec.summary = summary
                db.commit()
                logger.info("extraction_run_finalized", run_id=current_run_id)
        except Exception as _e:
            logger.warning("extraction_run_finalize_failed", reason=str(_e))

    return summary


# ---------------------------------------------------------------------------
# Retry Failed Extractions
# ---------------------------------------------------------------------------


def run_retry_failed(
    db,
    on_progress: Callable[[str], None] | None = None,
    limit: int | None = None,
) -> dict:
    """Retry extraction for passages+agents that previously failed.

    Reads from the failed_extraction_attempts table, groups by passage,
    and re-runs only the specific agents that failed. Marks attempts as
    retried and records success/failure.

    Args:
        db: SQLAlchemy session
        on_progress: Optional callback for status updates
        limit: Max failed attempts to retry (None = all)

    Returns:
        Summary dict with retry counts.
    """
    from src.core.run_archiver import RunArchiver

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    # Find un-retried failures
    query = (
        select(FailedExtractionAttempt)
        .where(FailedExtractionAttempt.retried == False)  # noqa: E712
        .order_by(FailedExtractionAttempt.source_record_id)
    )
    if limit:
        query = query.limit(limit)

    failed_attempts = db.scalars(query).all()

    if not failed_attempts:
        _log("No failed extraction attempts to retry.")
        return {"total": 0, "retried": 0, "succeeded": 0, "failed_again": 0}

    _log(f"Found {len(failed_attempts)} failed attempts to retry")

    # Group by source_record_id so we build context once per passage
    from itertools import groupby
    from operator import attrgetter

    # Attach retried extractions to the current serving ExtractionRun so they
    # are grouped with the run they are completing rather than floating at run_id=NULL.
    retry_run_id: int | None = None
    try:
        from src.db.models import ExtractionRun
        _serving = db.scalars(
            select(ExtractionRun).where(ExtractionRun.is_serving.is_(True))
        ).first()
        if _serving is not None:
            retry_run_id = _serving.id
            _log(f"Attaching retried extractions to serving run {retry_run_id}")
    except Exception as _e:
        logger.warning("retry_run_id_lookup_failed", error=str(_e))

    all_agents = _get_agents()
    archiver = RunArchiver.start("retry")
    token_usage = TokenUsageSummary()
    tracker = FailureTracker(
        context="retry failed extractions",
        max_consecutive=CIRCUIT_BREAKER_THRESHOLD,
        max_failure_rate=0.8,
        min_items_for_rate=10,
    )

    retried = 0
    succeeded = 0
    failed_again = 0

    sorted_attempts = sorted(failed_attempts, key=attrgetter("source_record_id"))
    for record_id, group in groupby(sorted_attempts, key=attrgetter("source_record_id")):
        attempts = list(group)
        record = db.get(NormalizedSourceRecord, record_id)
        if not record:
            _log(f"  Record {record_id} not found — skipping")
            for att in attempts:
                att.retried = True
                att.retry_succeeded = False
            continue

        ctx = _build_context(db, record)

        for attempt in attempts:
            agent_name = attempt.agent_name
            agent = all_agents.get(agent_name)
            if not agent:
                _log(f"  Agent '{agent_name}' not found — skipping")
                attempt.retried = True
                attempt.retry_succeeded = False
                failed_again += 1
                continue

            _log(f"  Retrying {agent_name} on record {record_id}...")
            attempt.retried = True
            retried += 1

            try:
                result = agent.extract(record.text_content, ctx)
                if result.abstention is not None:
                    attempt.retry_succeeded = True
                    succeeded += 1
                    tracker.record_success()
                    continue

                # Process extractions
                types = AGENT_EXTRACTION_TYPES.get(agent_name, [])
                default_type = types[0] if types else ExtractionType.obligation
                schema_class = EXTRACTION_TYPE_SCHEMAS.get(default_type.value)

                for item in result.extractions:
                    sp = db.begin_nested()
                    try:
                        resolved_type = _discriminate_extraction_type(agent_name, item)
                        evidence = item.get("evidence_spans", [])
                        orrick_sim = validate_extraction_against_orrick(item, ctx)
                        confidence = compute_confidence(
                            schema_valid=True,
                            evidence_spans=evidence,
                            extraction_payload=item,
                            schema_class=schema_class,
                            orrick_similarity=orrick_sim,
                            passage_text=record.text_content,
                            iapp_has_data=_iapp_has_data_for_ctx(ctx),
                        )
                        if result.truncated or result.was_repaired:
                            confidence.total_score, confidence.tier = cap_at_tier_c(
                                confidence.total_score, confidence.tier,
                            )

                        ext_type_str = resolved_type.value if hasattr(resolved_type, "value") else str(resolved_type)
                        extraction_meta: dict = {}
                        if result.truncated:
                            extraction_meta["truncated"] = True
                        if result.was_repaired:
                            extraction_meta["was_repaired"] = True
                        extraction_meta["confidence_breakdown"] = {
                            "schema_validity": confidence.schema_validity,
                            "evidence_grounding": confidence.evidence_grounding,
                            "completeness": confidence.completeness,
                            "source_quality": confidence.source_quality,
                            "orrick_alignment": confidence.orrick_alignment,
                            "cross_validation": confidence.cross_validation,
                            "orrick_gated": confidence.orrick_gated,
                            "source_grounding_score": confidence.source_grounding_score,
                            "tracker_alignment_score": confidence.tracker_alignment_score,
                            "schema_completeness_score": confidence.schema_completeness_score,
                        }
                        numeric_mismatch = _apply_numeric_grounding(
                            item, evidence, extraction_meta,
                        )
                        extraction_meta["retried_from"] = attempt.id
                        try:
                            from src.core.summary_generator import generate_summary
                            extraction_meta["plain_summary"] = generate_summary(
                                ext_type_str, item, ctx.get("jurisdiction"),
                            )
                        except Exception:
                            pass

                        extraction_kwargs: dict = dict(
                            source_record_id=record_id,
                            extraction_type=resolved_type,
                            agent_name=agent_name,
                            payload=item,
                            evidence_spans=evidence,
                            confidence_score=confidence.total_score,
                            confidence_tier=ConfidenceTier(confidence.tier),
                            review_status=ReviewStatus.pending,
                            prompt_hash=result.prompt_hash,
                            model_id=result.model_id,
                            metadata_=extraction_meta,
                        )
                        if _run_id_available and retry_run_id is not None:
                            extraction_kwargs["run_id"] = retry_run_id
                        extraction = Extraction(**extraction_kwargs)
                        db.add(extraction)
                        db.flush()

                        review_priority = _confidence_to_priority(confidence.tier)
                        if numeric_mismatch or result.truncated or result.was_repaired:
                            review_priority = max(review_priority, 3)
                        db.add(ReviewQueueItem(
                            extraction_id=extraction.id,
                            priority=review_priority,
                            status=ReviewStatus.pending,
                        ))
                        sp.commit()

                    except Exception as e:
                        sp.rollback()
                        logger.error("retry_insert_failed", agent=agent_name, error=str(e))

                attempt.retry_succeeded = True
                succeeded += 1
                tracker.record_success()
                token_usage.add(result.input_tokens, result.output_tokens)

            except CircuitBreakerTripped:
                _log("Circuit breaker tripped during retry — aborting.")
                break
            except Exception as e:
                attempt.retry_succeeded = False
                failed_again += 1
                tracker.record_failure(f"retry agent={agent_name}: {e}")
                logger.error("retry_agent_failed", agent=agent_name, error=str(e))

        db.commit()

    summary = {
        "total": len(failed_attempts),
        "retried": retried,
        "succeeded": succeeded,
        "failed_again": failed_again,
    }
    _log(
        f"\nRetry complete: {succeeded}/{retried} succeeded, "
        f"{failed_again} failed again"
    )
    archiver.finalize(db, summary, run_id=retry_run_id)
    return summary


# ---------------------------------------------------------------------------
# Dependency Graph Building (Phase 2 — post-extraction)
# ---------------------------------------------------------------------------


def run_dependency_graph(
    db,
    document_version_id: int | None = None,
    on_progress: Callable[[str], None] | None = None,
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
    on_progress: Callable[[str], None] | None = None,
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


def run_recovery_extraction(
    db,
    limit: int | None = None,
    on_progress: Callable[[str], None] | None = None,
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
    from sqlalchemy import distinct
    from sqlalchemy import func as sqlfunc

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
    from src.core.bill_context import get_or_build_bill_context

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

            bill_ctx = get_or_build_bill_context(db, record.document_version_id)
            ctx = _build_context(db, record, bill_context=bill_ctx)
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
                                passage_text=passage.text,
                                iapp_has_data=_iapp_has_data_for_ctx(ctx),
                            )
                            if result.truncated or result.was_repaired:
                                confidence.total_score, confidence.tier = cap_at_tier_c(
                                    confidence.total_score, confidence.tier,
                                )

                            recovery_meta: dict = {}
                            if result.truncated:
                                recovery_meta["truncated"] = True
                            if result.was_repaired:
                                recovery_meta["was_repaired"] = True
                            numeric_mismatch = _apply_numeric_grounding(
                                item, evidence, recovery_meta,
                            )

                            extraction = Extraction(
                                source_record_id=record.id,
                                extraction_type=resolved_type,
                                agent_name=agent_name,
                                payload=item,
                                evidence_spans=evidence,
                                confidence_score=confidence.total_score,
                                confidence_tier=ConfidenceTier(confidence.tier),
                                review_status=ReviewStatus.pending,
                                prompt_template_version=result.prompt_hash,
                                prompt_hash=result.prompt_hash,
                                template_version=result.template_version,
                                model_id=result.model_id,
                                metadata_=recovery_meta if recovery_meta else {},
                            )
                            db.add(extraction)
                            db.flush()

                            review_priority = _confidence_to_priority(confidence.tier)
                            if numeric_mismatch or result.truncated or result.was_repaired:
                                review_priority = max(review_priority, 3)
                            db.add(ReviewQueueItem(
                                extraction_id=extraction.id,
                                priority=review_priority,
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


# ---------------------------------------------------------------------------
# Completeness Manifest — re-exported from completeness.py (RR7a)
# ---------------------------------------------------------------------------

from src.ingestion.completeness import (  # noqa: E402
    DocumentCompleteness,
)
from src.ingestion.completeness import (
    compute_completeness_manifest as _completeness_manifest_impl,
)


def compute_completeness_manifest(
    db,
    document_version_id: int | None = None,
) -> list[DocumentCompleteness]:
    """Delegate to completeness.py (RR7a split)."""
    return _completeness_manifest_impl(db, document_version_id)


# ---------------------------------------------------------------------------
# Verification Pipeline — re-exported from verification_runner.py (RR7a)
# ---------------------------------------------------------------------------

from src.ingestion.verification_runner import (  # noqa: E402
    _iapp_has_data_for_ctx,
)
