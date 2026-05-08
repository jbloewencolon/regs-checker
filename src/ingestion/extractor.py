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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import func, inspect as sa_inspect, select

from src.agents.base import BaseExtractionAgent, ExtractionResult
from src.agents.compliance_mechanism import ComplianceMechanismAgent
from src.agents.definition_actor import DefinitionActorAgent
from src.agents.obligation import ObligationAgent
from src.agents.preemption import PreemptionAgent
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
    ApplicabilityCondition,
    ConfidenceTier,
    DocumentVersion,
    Extraction,
    ExtractionJob,
    ExtractionType,
    FailedExtractionAttempt,
    IngestionJob,
    NormalizedSourceRecord,
    ObligationDependency,
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

# Set at module-load or first extraction run — True once migration adds column
_payload_hash_available: bool | None = None

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

def _ensure_extraction_enums(db, _log=None) -> None:
    """Ensure all ExtractionType enum values exist in the local Postgres enum.

    The preemption_signal, rights_protection, and compliance_mechanism values
    were added after the initial schema. If the Alembic migration hasn't been
    applied to the local database, this adds them idempotently.

    IMPORTANT: ALTER TYPE ... ADD VALUE cannot run inside a transaction in
    PostgreSQL (it auto-commits). We use a raw psycopg2 connection with
    autocommit=True to execute these statements outside any transaction.
    """
    from sqlalchemy import text

    new_values = [
        "rights_protection",
        "compliance_mechanism",
        "preemption_signal",
    ]

    bind = db.get_bind()

    # First, check if the enum type exists at all
    with bind.connect() as conn:
        result = conn.execute(text(
            "SELECT enumlabel FROM pg_enum "
            "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
            "WHERE pg_type.typname = 'extractiontype'"
        ))
        existing = {row[0] for row in result}

    if not existing:
        # Enum type doesn't exist — Alembic migrations haven't run.
        # Don't try to ALTER a non-existent type; the migration will create it.
        if _log:
            _log("extractiontype enum not found — run Alembic migrations first.")
        return

    missing = [v for v in new_values if v not in existing]
    if not missing:
        return

    # ALTER TYPE ... ADD VALUE must run outside a transaction block.
    # Get the raw DBAPI connection and set autocommit mode.
    raw_conn = bind.raw_connection()
    try:
        raw_conn.autocommit = True
        cursor = raw_conn.cursor()
        for val in missing:
            if _log:
                _log(f"Adding '{val}' to extractiontype enum...")
            cursor.execute(
                f"ALTER TYPE extractiontype ADD VALUE IF NOT EXISTS '{val}'"
            )
        cursor.close()
    finally:
        raw_conn.autocommit = False
        raw_conn.close()


def _ensure_failed_attempts_table(db, _log=None) -> None:
    """Create the failed_extraction_attempts table if it doesn't exist."""
    from sqlalchemy import inspect as sa_inspect, text

    bind = db.get_bind()
    inspector = sa_inspect(bind)
    if inspector.has_table("failed_extraction_attempts"):
        return

    if _log:
        _log("Creating failed_extraction_attempts table...")

    with bind.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS failed_extraction_attempts (
                id SERIAL PRIMARY KEY,
                source_record_id INTEGER NOT NULL REFERENCES normalized_source_records(id),
                agent_name VARCHAR(100) NOT NULL,
                error_type VARCHAR(50) NOT NULL,
                error_message TEXT NOT NULL,
                extraction_job_id INTEGER REFERENCES extraction_jobs(id),
                retried BOOLEAN NOT NULL DEFAULT FALSE,
                retry_succeeded BOOLEAN,
                created_at TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_failed_attempts_source "
            "ON failed_extraction_attempts (source_record_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_failed_attempts_retry "
            "ON failed_extraction_attempts (retried, agent_name)"
        ))

    if _log:
        _log("Table created.")


def _record_failed_attempt(
    db,
    source_record_id: int,
    agent_name: str,
    error_type: str,
    error_message: str,
    extraction_job_id: int | None = None,
) -> None:
    """Record a failed extraction attempt for later retry."""
    try:
        db.add(FailedExtractionAttempt(
            source_record_id=source_record_id,
            agent_name=agent_name,
            error_type=error_type,
            error_message=str(error_message)[:2000],
            extraction_job_id=extraction_job_id,
        ))
        db.flush()
    except Exception as e:
        # Don't let failure tracking itself block the pipeline
        logger.warning("failed_attempt_recording_error", error=str(e))


def _ensure_triage_table(db, _log=None) -> None:
    """Create the section_triage_results table if it doesn't exist.

    Handles the case where the alembic migration hasn't been applied
    to the local database. Creates enum types and the table idempotently.
    """
    from sqlalchemy import inspect as sa_inspect, text

    bind = db.get_bind()
    inspector = sa_inspect(bind)
    if inspector.has_table("section_triage_results"):
        return

    if _log:
        _log("Creating section_triage_results table (migration not applied)...")

    with bind.begin() as conn:
        conn.execute(text("""
            DO $$ BEGIN
                CREATE TYPE triagedecision AS ENUM ('relevant', 'not_relevant', 'uncertain');
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        conn.execute(text("""
            DO $$ BEGIN
                CREATE TYPE triagemethod AS ENUM ('keyword', 'orrick_cross_check', 'llm_generic', 'quality_fail', 'passthrough', 'manual_review');
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS section_triage_results (
                id SERIAL PRIMARY KEY,
                source_record_id INTEGER NOT NULL UNIQUE REFERENCES normalized_source_records(id),
                decision triagedecision NOT NULL,
                method triagemethod NOT NULL,
                confidence FLOAT NOT NULL DEFAULT 0.0,
                matched_keywords JSONB DEFAULT '[]'::jsonb,
                orrick_terms_checked JSONB DEFAULT '[]'::jsonb,
                llm_reasoning TEXT,
                pdf_quality_score FLOAT,
                quality_flags JSONB DEFAULT '[]'::jsonb,
                model_id VARCHAR(100),
                created_at TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_triage_source_record ON section_triage_results (source_record_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_triage_decision ON section_triage_results (decision)"
        ))

    if _log:
        _log("Table created.")


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
        enforcement = df.metadata_.get("enforcement_penalties")
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


def _select_agents_for_passage(
    text: str,
    all_agents: dict[str, BaseExtractionAgent],
    triage_result=None,
) -> dict[str, BaseExtractionAgent]:
    """Select which agents to run based on passage content and triage signals.

    Uses a two-layer approach:
      1. Text heuristics: skip boilerplate, enacting clauses, bare headers.
      2. Triage signal routing: use matched_keywords, ai_signals, and
         llm_reasoning from the triage step to pick only the agents likely
         to find something.  Falls back to all agents when signals are
         ambiguous or missing.
    """
    text_stripped = text.strip()
    text_lower = text_stripped.lower()

    # If the entire passage is boilerplate (TOC, page numbers, separators),
    # skip ALL agents — no substantive content to extract.
    if _BOILERPLATE_PATTERN.fullmatch(text_stripped):
        return {}

    # If it's a pure enacting/signing clause, skip all agents.
    if _ENACTING_CLAUSE_PATTERN.match(text_stripped) and len(text_stripped) < 300:
        return {}

    # Start with all agents selected (recall-safe default)
    selected = dict(all_agents)

    # Definitions section headers → definition_actor only
    if _DEFINITIONS_SECTION_HEADER.fullmatch(text_stripped):
        return {k: v for k, v in selected.items() if k == "definition_actor"}

    # --- Signal-based routing (layer 2) ---
    # Build a combined signal string from triage data + passage text
    routed = _route_agents_by_signal(text_lower, selected, triage_result)
    if routed is not None:
        return routed

    return selected


# Patterns for signal-based agent routing.  Each maps a set of text
# signals to the subset of agents that should run.
_DEFINITION_SIGNALS = re.compile(
    r'\b(?:defin(?:e[sd]?|ition|ing)|means\b|as used in|for (?:the )?purposes? of)\b',
    re.IGNORECASE,
)
_OBLIGATION_SIGNALS = re.compile(
    r'\b(?:shall|must|require[sd]?|obligat|mandate[sd]?|prohibit|may not|'
    r'responsible for|duty to|ensure that)\b',
    re.IGNORECASE,
)
_RIGHTS_SIGNALS = re.compile(
    r'\b(?:right to|entitled|opt[- ]?out|notice to|consent|'
    r'appeal|recourse|due process|grievance|redress)\b',
    re.IGNORECASE,
)
_THRESHOLD_SIGNALS = re.compile(
    r'\b(?:threshold[s]?|exception[s]?|exempt(?:ion[s]?|ed)?|exclusion[s]?|waiver[s]?|'
    r'does not apply|not subject to|carve[- ]?out[s]?|'
    r'fewer than|more than|exceed[s]?|minimum|maximum)\b',
    re.IGNORECASE,
)
_COMPLIANCE_SIGNALS = re.compile(
    r'\b(?:enforc\w*|penalt\w*|fine[sd]?|violation[s]?|compliance|audit[s]?|'
    r'inspection[s]?|reporting|register|certif\w*|oversight|'
    r'attorney general|commission|agency)\b',
    re.IGNORECASE,
)
_PREEMPTION_SIGNALS = re.compile(
    r'\b(?:preempt|pre-empt|supersede|federal|supremacy|'
    r'state law|local (?:law|ordinance)|uniform|'
    r'notwithstanding any (?:other|state|local))\b',
    re.IGNORECASE,
)
_SIGNAL_MAP: list[tuple[re.Pattern, list[str]]] = [
    (_DEFINITION_SIGNALS,  ["definition_actor"]),
    (_OBLIGATION_SIGNALS,  ["obligation"]),
    (_RIGHTS_SIGNALS,      ["rights_protection"]),
    (_THRESHOLD_SIGNALS,   ["threshold_exception"]),
    (_COMPLIANCE_SIGNALS,  ["compliance_mechanism"]),
    (_PREEMPTION_SIGNALS,  ["preemption"]),
    # _AMBIGUITY_SIGNALS removed — ambiguity agent retired; findings embedded as interpretation_risks
]


def _route_agents_by_signal(
    text_lower: str,
    all_agents: dict[str, BaseExtractionAgent],
    triage_result,
) -> dict[str, BaseExtractionAgent] | None:
    """Use passage text + triage signals to select a subset of agents.

    Returns None if routing is inconclusive (caller should run all agents).
    """
    # Combine passage text with triage signals for richer matching
    signal_text = text_lower
    if triage_result is not None:
        if triage_result.ai_signals:
            signal_text += " " + triage_result.ai_signals.lower()
        if triage_result.llm_reasoning:
            signal_text += " " + triage_result.llm_reasoning.lower()

    # Collect which agents are signaled
    signaled: set[str] = set()
    for pattern, agent_names in _SIGNAL_MAP:
        if pattern.search(signal_text):
            signaled.update(agent_names)

    # If no signals matched at all, don't filter — run everything
    if not signaled:
        return None

    # Always include definition_actor when definitions are present — other
    # agents need definition context.  And always include obligation since
    # it's the most common extraction type in AI laws.
    signaled.add("definition_actor")
    signaled.add("obligation")

    # If fewer than 3 signals matched, the passage is focused — use the
    # subset.  If 3+ matched, the passage is rich/complex — run all agents.
    if len(signaled) >= len(all_agents) - 1:
        return None  # Nearly all agents signaled — just run everything

    return {k: v for k, v in all_agents.items() if k in signaled}


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
    bill_context: dict[str, Any] | None = None,
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
        bill_context: Pre-built bill-level context (definitions, scope,
            structure, defined_terms) from bill_context.get_or_build_bill_context.
    """
    record = passage.primary_record
    ctx = _build_context(db, record, bill_context=bill_context)
    extractions_created = 0

    # Jurisdiction cross-check: skip if document state doesn't match law state
    if not _check_jurisdiction(db, record, passage.text):
        return 0

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
            # Record for retry
            _record_failed_attempt(
                db, record.id, name, "llm_error", str(result),
                extraction_job_id=extraction_job.id if extraction_job else None,
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
                            continue

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
                    )

                    extraction_meta: dict = {}
                    if result.truncated:
                        extraction_meta["truncated"] = True
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
                    }

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
                    if _payload_hash_available:
                        extraction_kwargs["payload_hash"] = p_hash
                    extraction = Extraction(**extraction_kwargs)
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
                    )
                    if tracker is not None:
                        tracker.record_failure(
                            f"db_insert agent={name} record={source_record.id}: {e}"
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

    # Ensure section_triage_results table exists (may not if migration
    # hasn't been applied to the local database yet).
    _ensure_triage_table(db, _log)

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

    # Get LLM provider for Layer 2/3 triage (keyword-only passages are free)
    llm_provider = None
    try:
        from src.core.llm_provider import get_discovery_provider
        llm_provider = get_discovery_provider()
        _log(f"Triaging {len(records)} passages with LLM fallback ({llm_provider.model_id})...")
    except Exception as e:
        _log(f"Triaging {len(records)} passages (keyword-only, no LLM: {e})...")

    # Pre-build bill-level context per document_version so every passage in
    # the same bill shares definitions/scope/structure context.
    from src.core.bill_context import get_or_build_bill_context
    from itertools import groupby
    from operator import attrgetter

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
) -> int:
    """Run all bill-level agents for one document version.

    Assembles full bill text from sorted passages, runs each agent,
    upserts results to bill_level_extractions.  Returns count of agents
    that produced a non-error payload.
    """
    from src.db.models import BillLevelExtraction, ReviewStatus

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

    succeeded = 0
    for agent in agents:
        try:
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
            else:
                db.add(BillLevelExtraction(
                    document_version_id=document_version_id,
                    agent_name=agent.agent_name,
                    payload=result.payload,
                    model_id=result.model_id,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    truncated=result.truncated,
                    review_status=ReviewStatus.pending,
                ))

            if not has_error:
                succeeded += 1
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
) -> dict:
    """Run extraction agents against all unprocessed passages.

    Args:
        db: SQLAlchemy session
        limit: Max passages to process (None = all unprocessed)
        on_progress: Optional callback(message: str) for status updates
        batch_mode: Deprecated, ignored. Batch API has been archived.

    Returns:
        Summary dict with counts and token usage.
    """
    # Clear any stale cancellation from a previous run
    clear_cancel()

    # Ensure all extraction type enum values exist in local Postgres
    _ensure_extraction_enums(db, on_progress)

    # Ensure failed_extraction_attempts table exists for error tracking
    _ensure_failed_attempts_table(db, on_progress)

    # --- Auto-purge previous extraction run ---
    # Each extraction run replaces the prior run entirely. This ensures the
    # review queue and sync pipeline only ever contain data from the latest
    # run. The run archiver (below) preserves a CSV snapshot of the old data
    # before deletion.
    from sqlalchemy import delete as sa_delete
    old_ext_count = db.scalar(select(func.count()).select_from(Extraction)) or 0
    if old_ext_count > 0:
        if on_progress:
            on_progress(f"Purging {old_ext_count} extractions from previous run...")
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

    # Create a dated output folder for this run
    from src.core.run_archiver import RunArchiver
    archiver = RunArchiver.start("extract")

    agents = _get_agents()
    token_usage = TokenUsageSummary()

    # Check whether payload_hash column exists (migration may not have run yet)
    global _payload_hash_available
    try:
        _payload_hash_available = "payload_hash" in {
            c["name"] for c in sa_inspect(db.bind).get_columns("extractions")
        }
    except Exception:
        _payload_hash_available = False

    # Build set of existing content hashes for deduplication.
    # Pre-populate from DB so re-runs don't re-call agents on passages that
    # already have extractions (e.g. after an interrupted run or re-run).
    existing_hashes: set[str] = set()
    _already_extracted = db.execute(
        select(NormalizedSourceRecord.text_content)
        .join(Extraction, Extraction.source_record_id == NormalizedSourceRecord.id)
        .distinct()
    ).all()
    for (text_content,) in _already_extracted:
        for agent_name in agents:
            existing_hashes.add(_content_hash(agent_name, text_content))
    if existing_hashes:
        logger.info(
            "dedup_hashes_loaded",
            passages_with_extractions=len(_already_extracted),
            total_hashes=len(existing_hashes),
        )
    del _already_extracted

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    # Find passages that:
    #   1. Have no extractions yet, AND
    #   2. Have been triaged as relevant or uncertain (not "not_relevant")
    # If triage hasn't been run yet, passages without any triage result are
    # also excluded — the user must run "Triage Passages" first.
    triaged_relevant_ids = (
        select(SectionTriageResult.source_record_id)
        .where(SectionTriageResult.decision.in_([
            TriageDecision.relevant,
            TriageDecision.uncertain,
        ]))
    )
    query = (
        select(NormalizedSourceRecord)
        .outerjoin(Extraction)
        .where(
            Extraction.id.is_(None),
            NormalizedSourceRecord.id.in_(triaged_relevant_ids),
        )
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
        # Distinguish between "everything already extracted" vs "triage not run"
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
            _log("No unprocessed passages found — all triaged-relevant passages already extracted.")
        archiver.finalize(db, summary)
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
            # Check for cancellation between passages
            if is_cancelled():
                _log(f"\nExtraction terminated by user after {summary['records_processed']} passages.")
                extraction_job.status = "cancelled"
                extraction_job.completed_at = datetime.utcnow()
                db.commit()
                summary["total_extractions"] += job_extractions
                summary["cancelled"] = True
                _monitor.stop_run(cancelled=True)
                archiver.finalize(db, summary)
                return summary

            try:
                count = extract_single_record(
                    db, passage, agents, extraction_job, parse_quality,
                    token_usage, existing_hashes, tracker,
                    bill_context=bill_ctx,
                )
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
                archiver.finalize(db, summary)
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
            db, dv_id, dv_group, bill_ctx, _log=_log
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
    archiver.finalize(db, summary)
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

    _ensure_extraction_enums(db, on_progress)
    _ensure_failed_attempts_table(db, on_progress)

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
                        )

                        ext_type_str = resolved_type.value if hasattr(resolved_type, "value") else str(resolved_type)
                        extraction_meta: dict = {}
                        extraction_meta["confidence_breakdown"] = {
                            "schema_validity": confidence.schema_validity,
                            "evidence_grounding": confidence.evidence_grounding,
                            "completeness": confidence.completeness,
                            "source_quality": confidence.source_quality,
                            "orrick_alignment": confidence.orrick_alignment,
                            "cross_validation": confidence.cross_validation,
                            "orrick_gated": confidence.orrick_gated,
                        }
                        extraction_meta["retried_from"] = attempt.id
                        try:
                            from src.core.summary_generator import generate_summary
                            extraction_meta["plain_summary"] = generate_summary(
                                ext_type_str, item, ctx.get("jurisdiction"),
                            )
                        except Exception:
                            pass

                        extraction = Extraction(
                            source_record_id=record_id,
                            extraction_type=resolved_type,
                            payload=item,
                            evidence_spans=evidence,
                            confidence_score=confidence.total_score,
                            confidence_tier=ConfidenceTier(confidence.tier),
                            review_status=ReviewStatus.pending,
                            prompt_hash=result.prompt_hash,
                            model_id=result.model_id,
                            metadata_=extraction_meta,
                        )
                        db.add(extraction)
                        db.flush()

                        db.add(ReviewQueueItem(
                            extraction_id=extraction.id,
                            priority=_confidence_to_priority(confidence.tier),
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
    archiver.finalize(db, summary)
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
    on_progress: Callable[[str], None] | None = None,
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
    from src.core.bill_context import get_or_build_bill_context

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

        # Get triaged-relevant passages for this document (not ALL passages).
        # Cross-validation and gap detection only need to check passages that
        # were triaged as relevant/uncertain — irrelevant passages were already
        # filtered out before extraction and contain no useful content.
        records = db.scalars(
            select(NormalizedSourceRecord)
            .where(NormalizedSourceRecord.document_version_id == dv_id)
            .where(
                NormalizedSourceRecord.id.in_(
                    select(SectionTriageResult.source_record_id)
                    .where(SectionTriageResult.decision.in_([
                        TriageDecision.relevant,
                        TriageDecision.uncertain,
                    ]))
                )
            )
            .order_by(NormalizedSourceRecord.ordinal)
        ).all()

        # Also load ALL records (including irrelevant) for bill context building,
        # since definitions and scope sections may have been triaged as not_relevant
        # but are still needed for context.
        all_records = db.scalars(
            select(NormalizedSourceRecord)
            .where(NormalizedSourceRecord.document_version_id == dv_id)
            .order_by(NormalizedSourceRecord.ordinal)
        ).all()

        bill_ctx = get_or_build_bill_context(db, dv_id, records=all_records)

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
                ctx = _build_context(db, record, bill_context=bill_ctx)

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
                ctx = _build_context(db, record, bill_context=bill_ctx)

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
