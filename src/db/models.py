"""Consolidated database models — ~15 core tables per Recommendation #12.

Table inventory:
 1. sources                  – legislative sources / jurisdictions
 2. document_families        – logical document groups
 3. document_versions        – specific versions of a document
 4. ingestion_jobs           – combined fetch + parse tracking
 5. raw_artifacts            – immutable content-addressable blobs
 6. normalized_source_records – passage-level normalized text
 7. extractions              – unified extraction table (type discriminator + JSONB)
 8. extraction_jobs          – tracks extraction pipeline runs
 9. review_queue             – items awaiting human review
10. review_actions           – audit log of review decisions
11. legal_events             – append-only temporal event log
12. obligation_dependencies  – graph edges modeled in Postgres (Rec #4)
13. applicability_conditions – AND/OR/NOT expression tree (adjacency list)
14. api_keys                 – API authentication for /v1/
15. export_jobs              – async export task tracking
16. section_triage_results   – AI-relevance filtering per passage

Materialized views (not tables):
 - current_active_obligations
 - served_obligations
 - served_matrix_cells
"""

import enum

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, deferred, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class IngestionStatus(str, enum.Enum):
    pending = "pending"
    fetching = "fetching"
    fetched = "fetched"
    parsing = "parsing"
    parsed = "parsed"
    normalizing = "normalizing"
    completed = "completed"
    failed = "failed"
    requires_manual_review = "requires_manual_review"


class ExtractionType(str, enum.Enum):
    obligation = "obligation"
    definition = "definition"
    actor_mapping = "actor_mapping"
    threshold = "threshold"
    exception = "exception"
    enforcement = "enforcement"
    timeline = "timeline"
    framework_ref = "framework_ref"
    ambiguity = "ambiguity"
    rights_protection = "rights_protection"
    compliance_mechanism = "compliance_mechanism"
    preemption_signal = "preemption_signal"


class ConfidenceTier(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class ReviewStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    needs_revision = "needs_revision"


class TemporalStatus(str, enum.Enum):
    # Pre-enactment (bill still in legislature)
    introduced = "introduced"
    pending = "pending"
    passed_one_chamber = "passed_one_chamber"
    # Post-enactment
    enacted = "enacted"
    active = "active"
    future_effective = "future_effective"
    repealed = "repealed"
    stayed = "stayed"
    # Terminal / dead
    vetoed = "vetoed"
    dead = "dead"
    withdrawn = "withdrawn"


class LegalEventType(str, enum.Enum):
    enactment = "enactment"
    amendment = "amendment"
    repeal = "repeal"
    stay = "stay"
    effective = "effective"
    sunset = "sunset"
    introduction = "introduction"
    passage_one_chamber = "passage_one_chamber"
    veto = "veto"
    death = "death"
    withdrawal = "withdrawal"
    status_check = "status_check"


class DependencyType(str, enum.Enum):
    requires_definition = "requires_definition"
    modifies = "modifies"
    excepts = "excepts"
    enforces = "enforces"
    references = "references"
    supersedes = "supersedes"


class ConditionNodeType(str, enum.Enum):
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    LEAF = "LEAF"


class ExportFormat(str, enum.Enum):
    json = "json"
    csv = "csv"
    xlsx = "xlsx"


class ExportStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


# ---------------------------------------------------------------------------
# 1. Sources
# ---------------------------------------------------------------------------


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    jurisdiction_code = Column(String(20), nullable=False, index=True)
    jurisdiction_name = Column(String(200), nullable=False)
    source_type = Column(String(50), nullable=False)  # e.g. "state_statute", "federal_eo"
    base_url = Column(Text)
    connector_id = Column(String(100))  # which scraper connector handles this
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    document_families = relationship("DocumentFamily", back_populates="source")


# ---------------------------------------------------------------------------
# 2. Document Families
# ---------------------------------------------------------------------------


class DocumentFamily(Base):
    __tablename__ = "document_families"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False, index=True)
    canonical_title = Column(Text, nullable=False)
    short_cite = Column(String(200))
    subject_area = Column(String(200))
    primary_source_url = Column(Text)  # Direct .gov / legislature link to bill text
    orrick_reference_url = Column(Text)  # Orrick AI Law Center reference page
    iapp_reference_url = Column(Text)  # IAPP US AI Legislation Tracker PDF
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())

    source = relationship("Source", back_populates="document_families")
    versions = relationship("DocumentVersion", back_populates="family")


# ---------------------------------------------------------------------------
# 3. Document Versions
# ---------------------------------------------------------------------------


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    family_id = Column(Integer, ForeignKey("document_families.id"), nullable=False, index=True)
    version_label = Column(String(100), nullable=False)
    predecessor_id = Column(Integer, ForeignKey("document_versions.id"), nullable=True)
    effective_date = Column(Date, nullable=True)
    sunset_date = Column(Date, nullable=True)
    temporal_status = Column(
        Enum(TemporalStatus), nullable=False, default=TemporalStatus.enacted
    )
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    # RR7b — source provenance fields (added by migration v8w4x0y2z023)
    session_year = Column(Integer, nullable=True)       # state legislative session year
    bill_number = Column(String(50), nullable=True)     # e.g. "SB 205", "HB 1234"
    retrieved_at = Column(DateTime, nullable=True)      # when this version was fetched
    source_hash = Column(String(64), nullable=True)     # SHA-256 of the source content

    family = relationship("DocumentFamily", back_populates="versions")
    predecessor = relationship("DocumentVersion", remote_side=[id])
    ingestion_jobs = relationship("IngestionJob", back_populates="document_version")
    raw_artifacts = relationship("RawArtifact", back_populates="document_version")
    normalized_records = relationship(
        "NormalizedSourceRecord", back_populates="document_version"
    )


# ---------------------------------------------------------------------------
# 4. Ingestion Jobs (merged fetch_jobs + source_discovery_events + parse_jobs)
# ---------------------------------------------------------------------------


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_version_id = Column(
        Integer, ForeignKey("document_versions.id"), nullable=False, index=True
    )
    status = Column(Enum(IngestionStatus), nullable=False, default=IngestionStatus.pending)
    fetch_url = Column(Text)
    fetch_started_at = Column(DateTime)
    fetch_completed_at = Column(DateTime)
    parse_started_at = Column(DateTime)
    parse_completed_at = Column(DateTime)
    parse_quality_score = Column(Float)
    error_message = Column(Text)
    ai_suggested_url = Column(Text)  # Set by VerificationAgent when fetch_url is stale
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    document_version = relationship("DocumentVersion", back_populates="ingestion_jobs")

    __table_args__ = (
        Index(
            "uq_ingestion_job_version_url",
            "document_version_id", "fetch_url",
            unique=True,
            postgresql_where=text("fetch_url IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# 5. Raw Artifacts (immutable, content-addressable)
#
# RR4e: split into two tables:
#   ContentBlob — globally deduplicated by SHA-256 (the actual file store)
#   RawArtifact — per-document-version link to a ContentBlob
#
# Before RR4e the sha256_hash UNIQUE constraint was on raw_artifacts itself,
# which prevented two document versions from sharing the same PDF blob.
# Now ContentBlob owns the unique constraint; RawArtifact links DV→Blob.
# ---------------------------------------------------------------------------


class ContentBlob(Base):
    """Globally deduplicated content store keyed by SHA-256 hash (RR4e)."""

    __tablename__ = "content_blobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sha256_hash = Column(String(64), nullable=False, unique=True)
    s3_key = Column(Text, nullable=False)
    content_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    raw_artifacts = relationship("RawArtifact", back_populates="content_blob")


class RawArtifact(Base):
    __tablename__ = "raw_artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_version_id = Column(
        Integer, ForeignKey("document_versions.id"), nullable=False, index=True
    )
    # sha256_hash kept for backwards compat; unique constraint moved to content_blobs.
    sha256_hash = Column(String(64), nullable=False, index=True)
    s3_key = Column(Text, nullable=False)
    content_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    is_primary = Column(Boolean, default=True)
    # RR4e: FK to the canonical blob row (nullable for rows pre-dating the migration)
    content_blob_id = Column(
        Integer, ForeignKey("content_blobs.id"), nullable=True, index=True
    )
    created_at = Column(DateTime, server_default=func.now())

    document_version = relationship("DocumentVersion", back_populates="raw_artifacts")
    content_blob = relationship("ContentBlob", back_populates="raw_artifacts")


# ---------------------------------------------------------------------------
# 6. Normalized Source Records (passage-level text)
# ---------------------------------------------------------------------------


class NormalizedSourceRecord(Base):
    __tablename__ = "normalized_source_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_version_id = Column(
        Integer, ForeignKey("document_versions.id"), nullable=False, index=True
    )
    section_path = Column(Text)  # e.g. "Part 1 > Section 3 > (a)(2)"
    ordinal = Column(Integer, nullable=False)
    text_content = Column(Text, nullable=False)
    text_hash = Column(String(64), nullable=False)
    char_offset_start = Column(Integer)
    char_offset_end = Column(Integer)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())

    document_version = relationship("DocumentVersion", back_populates="normalized_records")
    extractions = relationship("Extraction", back_populates="source_record")

    __table_args__ = (
        Index("ix_nsr_version_ordinal", "document_version_id", "ordinal"),
        Index(
            "uq_nsr_version_ordinal",
            "document_version_id", "ordinal",
            unique=True,
        ),
    )


# ---------------------------------------------------------------------------
# 7. Extractions (unified — Recommendation #12)
#    Uses type discriminator + JSONB payload instead of 8 separate tables.
#    Generated columns for frequently queried fields.
# ---------------------------------------------------------------------------


class Extraction(Base):
    __tablename__ = "extractions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_record_id = Column(
        Integer, ForeignKey("normalized_source_records.id"), nullable=False, index=True
    )
    extraction_type = Column(Enum(ExtractionType), nullable=False, index=True)
    payload = Column(JSONB, nullable=False)
    evidence_spans = Column(JSONB, nullable=False, default=list)
    confidence_score = Column(Float, nullable=False)
    confidence_tier = Column(Enum(ConfidenceTier), nullable=False)
    review_status = Column(Enum(ReviewStatus), nullable=False, default=ReviewStatus.pending)
    prompt_template_version = Column(String(40))  # git SHA of prompt used
    prompt_hash = Column(String(24))  # SHA-256 prefix of rendered prompt
    template_version = Column(String(50))  # version from YAML template
    model_id = Column(String(100))
    input_tokens = deferred(Column(Integer, default=0))
    output_tokens = deferred(Column(Integer, default=0))
    duration_ms = deferred(Column(Integer, nullable=True))
    extraction_job_id = Column(Integer, ForeignKey("extraction_jobs.id"), index=True)
    run_id = Column(Integer, ForeignKey("extraction_runs.id"), nullable=True, index=True)
    payload_hash = Column(String(64), nullable=True, index=True)  # SHA-256 of normalized payload
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    source_record = relationship("NormalizedSourceRecord", back_populates="extractions")
    extraction_job = relationship("ExtractionJob", back_populates="extractions")
    extraction_run = relationship("ExtractionRun", back_populates="extractions")
    review_queue_items = relationship("ReviewQueueItem", back_populates="extraction")

    __table_args__ = (
        Index("ix_extractions_type_status", "extraction_type", "review_status"),
        Index("ix_extractions_payload", "payload", postgresql_using="gin"),
        Index(
            "uq_extractions_dedup",
            "source_record_id", "extraction_type", "payload_hash",
            unique=True,
        ),
    )


# ---------------------------------------------------------------------------
# 8a. Extraction Runs — version-controlled run records (Phase 1b)
# ---------------------------------------------------------------------------


class ExtractionRun(Base):
    """One record per logical extraction run (one press of 'Extract All').

    Captures the full versioning context (git SHA, prompt versions, model
    config) so every extraction can be traced to the exact code and prompts
    that produced it.  run_id FK on extractions / bill_level_extractions
    links rows to the run that created them.

    is_serving=True marks the run whose extractions power live queries.
    Only one run should be serving at a time; a new full run demotes the
    previous serving run before promoting itself.
    """

    __tablename__ = "extraction_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_type = Column(String(50), nullable=False, default="extract")
    status = Column(String(20), nullable=False, default="running")
    is_serving = Column(Boolean, nullable=False, default=False)

    # Reproducibility pinning
    git_sha = Column(String(40))
    model_config = Column(JSONB, default=dict)    # model IDs and token limits per agent
    prompt_versions = Column(JSONB, default=dict) # template_version per agent
    source_snapshot_hash = Column(String(64))     # SHA of law corpus at run time

    # Counts (filled in on finalize)
    law_count = Column(Integer, default=0)
    passage_count = Column(Integer, default=0)
    extraction_count = Column(Integer, default=0)

    # Full run summary JSON written by RunArchiver.finalize()
    summary = Column(JSONB, default=dict)

    started_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())

    extractions = relationship("Extraction", back_populates="extraction_run")
    bill_level_extractions = relationship(
        "BillLevelExtraction", back_populates="extraction_run"
    )


# ---------------------------------------------------------------------------
# 8b. Extraction Jobs
# ---------------------------------------------------------------------------


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_version_id = Column(
        Integer, ForeignKey("document_versions.id"), nullable=False, index=True
    )
    agent_name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    records_total = Column(Integer, default=0)
    records_processed = Column(Integer, default=0)
    records_failed = Column(Integer, default=0)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error_message = Column(Text)
    created_at = Column(DateTime, server_default=func.now())

    extractions = relationship("Extraction", back_populates="extraction_job")


# ---------------------------------------------------------------------------
# 8c. Extraction Attempts — per-agent run-state tracking (RR1c)
# ---------------------------------------------------------------------------


class ExtractionAttempt(Base):
    """One row per (source_record, agent_name) per extraction run.

    Tracks the full lifecycle of each agent call so interrupted runs can be
    detected and resumed.  On each re-run, new rows are inserted (not updated
    in-place) so the history of retries is preserved.

    Status lifecycle:
      running   → succeeded   (agent returned extractions or abstained cleanly)
      running   → failed      (LLM call or DB write raised an exception)
      (none)    → skipped     (agent excluded by signal routing or dedup guard)
    """

    __tablename__ = "extraction_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_record_id = Column(
        Integer, ForeignKey("normalized_source_records.id"), nullable=False, index=True
    )
    agent_name = Column(String(100), nullable=False)
    run_id = Column(Integer, ForeignKey("extraction_runs.id"), nullable=True, index=True)
    status = Column(String(20), nullable=False)  # running|succeeded|failed|skipped
    extractions_produced = Column(Integer, default=0, nullable=False)
    input_text_hash = Column(String(24), nullable=True)  # sha256[:24] of passage text
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    source_record = relationship("NormalizedSourceRecord")

    __table_args__ = (
        Index("ix_extraction_attempts_record_agent", "source_record_id", "agent_name"),
        Index("ix_extraction_attempts_run_status", "run_id", "status"),
        Index("ix_extraction_attempts_succeeded", "source_record_id", "agent_name",
              "input_text_hash", postgresql_where="status = 'succeeded'"),
    )


# ---------------------------------------------------------------------------
# 9. Review Queue
# ---------------------------------------------------------------------------


class ReviewQueueItem(Base):
    __tablename__ = "review_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    extraction_id = Column(
        Integer, ForeignKey("extractions.id"), nullable=False, unique=True
    )
    priority = Column(Integer, default=0)
    assigned_to = Column(String(200))
    status = Column(Enum(ReviewStatus), nullable=False, default=ReviewStatus.pending)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    extraction = relationship("Extraction", back_populates="review_queue_items")
    actions = relationship("ReviewAction", back_populates="queue_item")


# ---------------------------------------------------------------------------
# 10. Review Actions (audit log)
# ---------------------------------------------------------------------------


class ReviewAction(Base):
    __tablename__ = "review_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    queue_item_id = Column(
        Integer, ForeignKey("review_queue.id"), nullable=False, index=True
    )
    action = Column(Enum(ReviewStatus), nullable=False)
    reviewer = Column(String(200), nullable=False)
    comment = Column(Text)
    corrections = Column(JSONB)  # optional payload corrections
    created_at = Column(DateTime, server_default=func.now())

    queue_item = relationship("ReviewQueueItem", back_populates="actions")


# ---------------------------------------------------------------------------
# 11. Legal Events (append-only — simple temporal model per Rec #5)
# ---------------------------------------------------------------------------


class LegalEvent(Base):
    __tablename__ = "legal_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_version_id = Column(
        Integer, ForeignKey("document_versions.id"), nullable=False, index=True
    )
    event_type = Column(Enum(LegalEventType), nullable=False)
    event_date = Column(Date, nullable=False)
    description = Column(Text)
    authority = Column(Text)  # e.g. court name, legislative body
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_legal_events_version_date", "document_version_id", "event_date"),
    )


# ---------------------------------------------------------------------------
# 12. Obligation Dependencies (graph edges in Postgres — Rec #4)
# ---------------------------------------------------------------------------


class ObligationDependency(Base):
    __tablename__ = "obligation_dependencies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_extraction_id = Column(
        Integer, ForeignKey("extractions.id"), nullable=False, index=True
    )
    child_extraction_id = Column(
        Integer, ForeignKey("extractions.id"), nullable=False, index=True
    )
    dependency_type = Column(Enum(DependencyType), nullable=False)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "parent_extraction_id", "child_extraction_id", "dependency_type",
            name="uq_obligation_dep"
        ),
    )


# ---------------------------------------------------------------------------
# 13. Applicability Conditions (AND/OR/NOT expression tree)
# ---------------------------------------------------------------------------


class ApplicabilityCondition(Base):
    __tablename__ = "applicability_conditions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    extraction_id = Column(
        Integer, ForeignKey("extractions.id"), nullable=False, index=True
    )
    parent_id = Column(
        Integer, ForeignKey("applicability_conditions.id"), nullable=True
    )
    node_type = Column(Enum(ConditionNodeType), nullable=False)
    ordinal = Column(Integer, nullable=False, default=0)
    condition_text = Column(Text)
    metadata_ = Column("metadata", JSONB, default=dict)

    parent = relationship("ApplicabilityCondition", remote_side=[id])


# ---------------------------------------------------------------------------
# 14. API Keys
# ---------------------------------------------------------------------------


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_hash = Column(String(64), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    scopes = Column(JSONB, default=list)
    is_active = Column(Boolean, default=True)
    rate_limit_rpm = Column(Integer, default=60)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# 15. Export Jobs
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 16. Section Triage Results — AI-relevance filtering per passage
# ---------------------------------------------------------------------------


class TriageDecision(str, enum.Enum):
    relevant = "relevant"
    not_relevant = "not_relevant"
    uncertain = "uncertain"  # Passed to extraction as precaution


class TriageMethod(str, enum.Enum):
    keyword = "keyword"              # Matched AI keywords / Orrick terms
    orrick_cross_check = "orrick_cross_check"  # LLM cross-check against Orrick metadata
    llm_generic = "llm_generic"      # LLM generic AI-relevance (no Orrick data)
    quality_fail = "quality_fail"    # PDF quality too low to triage
    passthrough = "passthrough"      # No triage run (e.g., all-agents fallback)
    manual_review = "manual_review"  # Human override from triage UI


class SectionTriageResult(Base):
    __tablename__ = "section_triage_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_record_id = Column(
        Integer, ForeignKey("normalized_source_records.id"),
        nullable=False, unique=True, index=True,
    )
    decision = Column(Enum(TriageDecision), nullable=False)
    method = Column(Enum(TriageMethod), nullable=False)
    confidence = Column(Float, nullable=False, default=0.0)

    # What matched (keyword hits, Orrick terms, LLM reasoning)
    matched_keywords = Column(JSONB, default=list)    # ["artificial intelligence", "deployer"]
    orrick_terms_checked = Column(JSONB, default=list)  # terms extracted from Orrick metadata
    llm_reasoning = Column(Text)                       # LLM explanation (if LLM was used)
    ai_signals = Column(Text)                           # Why the passage might be AI-related

    # PDF quality metrics for this passage
    pdf_quality_score = Column(Float)  # 0.0-1.0; None if not a PDF
    quality_flags = Column(JSONB, default=list)  # ["ocr_noise", "garbled_chars", ...]

    model_id = Column(String(100))     # which model triaged (null if keyword-only)
    created_at = Column(DateTime, server_default=func.now())

    source_record = relationship("NormalizedSourceRecord", backref="triage_result")

    __table_args__ = (
        Index("ix_triage_decision", "decision"),
    )


class FailedExtractionAttempt(Base):
    """Tracks individual extraction failures for retry.

    When an agent call or DB insert fails during extraction, a row is written
    here so the passage+agent pair can be retried later without re-running
    the entire extraction pipeline.
    """
    __tablename__ = "failed_extraction_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_record_id = Column(
        Integer, ForeignKey("normalized_source_records.id"),
        nullable=False, index=True,
    )
    agent_name = Column(String(100), nullable=False)
    error_type = Column(String(50), nullable=False)  # "llm_error", "validation_error", "db_error"
    error_message = Column(Text, nullable=False)
    extraction_job_id = Column(Integer, ForeignKey("extraction_jobs.id"), nullable=True)
    retried = Column(Boolean, default=False, nullable=False, index=True)
    retry_succeeded = Column(Boolean, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    source_record = relationship("NormalizedSourceRecord")
    extraction_job = relationship("ExtractionJob")

    __table_args__ = (
        Index("ix_failed_attempts_retry", "retried", "agent_name"),
    )


class VocabReviewQueueItem(Base):
    """B4 — Tracks extraction field values that did not match any canonical code.

    Populated by vocab_loader.normalize() (via flush_unrecognized()) when a
    raw value has no entry in the ratified alias tables.  A provisonal_code is
    recorded so product output can still serve a fallback; the item stays in
    this queue until RPR/LKA assigns a canonical mapping.
    """

    __tablename__ = "vocab_review_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dimension = Column(String(50), nullable=False)   # actor, law_domain, obligation_family, …
    raw_term = Column(String(500), nullable=False)
    source = Column(String(100), nullable=True)      # extraction / orrick / iapp
    extraction_id = Column(
        Integer, ForeignKey("extractions.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    law_id = Column(Integer, nullable=True)
    provisional_code = Column(String(50), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    resolved_at = Column(DateTime, nullable=True)
    resolution = Column(String(50), nullable=True)

    extraction = relationship("Extraction", foreign_keys=[extraction_id])

    __table_args__ = (
        Index("ix_vocab_review_dimension_term", "dimension", "raw_term"),
        Index("ix_vocab_review_unresolved", "resolved_at"),
    )


class ExportJob(Base):
    __tablename__ = "export_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    requested_by = Column(String(200), nullable=False)
    export_format = Column(Enum(ExportFormat), nullable=False)
    filters = Column(JSONB, default=dict)
    status = Column(Enum(ExportStatus), nullable=False, default=ExportStatus.pending)
    s3_key = Column(Text)
    record_count = Column(Integer)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())


# ---------------------------------------------------------------------------
# Bill-Level Extractions
# ---------------------------------------------------------------------------


class BillLevelExtraction(Base):
    """One structured record per law per bill-level agent.

    Unlike Extraction (which is passage-scoped), BillLevelExtraction runs
    once per DocumentVersion with the full bill text as input.  This lets
    agents resolve cross-section references (e.g. penalty in §X referenced
    by obligation in §Y) that per-passage agents cannot see.

    Agents: enforcement_agent, applicability_agent, compliance_timeline_agent.
    Each writes exactly one row per law; re-runs overwrite via upsert.
    """

    __tablename__ = "bill_level_extractions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_version_id = Column(
        Integer, ForeignKey("document_versions.id"), nullable=False, index=True
    )
    agent_name = Column(String(100), nullable=False)
    payload = Column(JSONB, nullable=False, default=dict)
    confidence_score = Column(Float, nullable=True)
    review_status = Column(
        Enum(ReviewStatus), nullable=False, default=ReviewStatus.pending
    )
    model_id = Column(String(100))
    run_id = Column(Integer, ForeignKey("extraction_runs.id"), nullable=True, index=True)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    truncated = Column(Boolean, default=False)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    document_version = relationship("DocumentVersion")
    extraction_run = relationship("ExtractionRun", back_populates="bill_level_extractions")

    __table_args__ = (
        # One row per law per agent — re-runs upsert rather than duplicate
        Index(
            "uq_bill_level_extractions",
            "document_version_id", "agent_name",
            unique=True,
        ),
    )


# ---------------------------------------------------------------------------
# Phase 4a — Verification Results Persistence
# ---------------------------------------------------------------------------


class VerificationRunSummary(Base):
    """One row per document-version per verification pass.

    Captures the document-level aggregates from run_verification_pass():
    cross-validation stats, gap detection summary, citation check summary,
    and token usage.  The gap candidates and citation issues are stored as
    JSONB so they remain queryable without a separate join.
    """

    __tablename__ = "verification_run_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_version_id = Column(
        Integer, ForeignKey("document_versions.id"), nullable=False, index=True
    )
    run_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Cross-validation aggregates
    cv_passages_checked = Column(Integer, default=0, nullable=False)
    # Passages whose CV call failed (fail-closed — excluded from cv_avg_accuracy)
    cv_passages_failed = Column(Integer, default=0, nullable=False)
    cv_extractions_valid = Column(Integer, default=0, nullable=False)
    cv_extractions_flagged = Column(Integer, default=0, nullable=False)
    cv_avg_accuracy = Column(Float, nullable=True)

    # Gap detection aggregates
    gd_passages_checked = Column(Integer, default=0, nullable=False)
    # Passages whose gap detection failed (fail-closed — not counted as "no gaps")
    gd_passages_failed = Column(Integer, default=0, nullable=False)
    gd_gaps_found = Column(Integer, default=0, nullable=False)
    gd_high_confidence = Column(Integer, default=0, nullable=False)
    gap_candidates = Column(JSONB, default=list)

    # Citation verification aggregates
    citations_checked = Column(Integer, default=0, nullable=False)
    citations_verified = Column(Integer, default=0, nullable=False)
    citations_unverified = Column(Integer, default=0, nullable=False)
    citation_issues = Column(JSONB, default=list)

    # Token usage for this verification run
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)

    document_version = relationship("DocumentVersion")


class ExtractionVerificationStatus(Base):
    """Per-extraction verification state — persists what was ephemeral in metadata_.

    Phase 4a: captures CV score, confidence before/after recompute, and Orrick
    grounding status.  Rows are upserted on each verify run (one active row per
    extraction).

    Phase 4b will populate iapp_status and refine grounding_status once IAPP
    ingestion is complete.
    """

    __tablename__ = "extraction_verification_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    extraction_id = Column(
        Integer, ForeignKey("extractions.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    verification_run_id = Column(
        Integer,
        ForeignKey("verification_run_summaries.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    document_version_id = Column(Integer, nullable=False, index=True)

    # Cross-validation result
    cv_score = Column(Float, nullable=True)          # accuracy_score (0.0–1.0)
    cv_is_valid = Column(Boolean, nullable=True)     # True → passed CV
    cv_flagged = Column(Boolean, default=False, nullable=False)

    # Confidence tracking across recompute
    confidence_before = Column(Float, nullable=True)
    confidence_after = Column(Float, nullable=True)
    tier_before = Column(String(1), nullable=True)   # "A"/"B"/"C"/"D"
    tier_after = Column(String(1), nullable=True)
    tier_changed = Column(Boolean, default=False, nullable=False)

    # Orrick grounding (three-state: aligned / silent / gated)
    # "aligned"        — Orrick data present, combined_score > 0.0
    # "tracker_silent" — no Orrick data for this law (IAPP-only or unmapped)
    # "gated"          — Orrick gate fired; forced Tier D regardless of other signals
    orrick_status = Column(String(30), nullable=True)
    orrick_score = Column(Float, nullable=True)
    orrick_gated = Column(Boolean, default=False, nullable=False)

    # IAPP alignment — populated by Phase 4b
    # "aligned" / "conflict" / "tracker_silent" / NULL (not yet checked)
    iapp_status = Column(String(30), nullable=True)

    # Combined grounding status
    # "orrick_grounded"  — Orrick confirms this extraction
    # "tracker_silent"   — no tracker data; can't confirm or deny
    # "tracker_conflict" — extraction contradicts a tracker field (Phase 4b)
    # "unverified"       — verification not yet run
    grounding_status = Column(String(30), default="unverified", nullable=False)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    extraction = relationship("Extraction", foreign_keys=[extraction_id])
    verification_run = relationship("VerificationRunSummary")


# ---------------------------------------------------------------------------
# Phase 5 — Compliance-concept layer (the product bridge)
# ---------------------------------------------------------------------------


class ConceptReviewStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    flagged = "flagged"          # tracker conflict / D-tier member — needs analyst
    rejected = "rejected"


class ComplianceConcept(Base):
    """A business-facing compliance requirement grouped from normalized fragments.

    The product unit is a concept, not a raw extraction row.  A concept bundles
    several normalized extractions (an obligation + its deadline + exceptions +
    enforcement + tracker refs + evidence) into one requirement that a compliance
    team can act on.  Concepts are the hand-off unit to the (deferred) law-card
    builder (§7 of the unified plan).

    Grouping is deterministic: concepts are keyed on
    (document_version_id, concept_type, regulated_actor_family).
    """

    __tablename__ = "compliance_concepts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_version_id = Column(
        Integer, ForeignKey("document_versions.id"), nullable=False, index=True
    )

    # Deterministic grouping key
    concept_type = Column(String(80), nullable=False, index=True)
    regulated_actor_family = Column(String(50), nullable=True, index=True)
    right_holder_family = Column(String(50), nullable=True)
    covered_system_type = Column(String(80), nullable=True)

    # Human-facing summary fields
    title = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    trigger_condition = Column(Text, nullable=True)
    required_action = Column(Text, nullable=True)
    deadline = Column(Text, nullable=True)

    # Structured aggregates (JSONB)
    exceptions = Column(JSONB, default=list)        # [{extraction_id, text}]
    enforcement_refs = Column(JSONB, default=list)  # [{extraction_id, penalty_type, enforcing_body}]
    source_extraction_ids = Column(JSONB, default=list)
    tracker_ref_ids = Column(JSONB, default=list)   # ["orrick:CO/SB 205", "iapp:CO/SB 205"]

    # Scoring + review
    confidence_score = Column(Float, nullable=True)
    confidence_tier = Column(String(1), nullable=True)   # "A"/"B"/"C"/"D"
    grounding_status = Column(String(30), default="ungrounded", nullable=False)
    # "tracker_grounded" / "tracker_conflict" / "ungrounded"
    review_status = Column(
        Enum(ConceptReviewStatus), nullable=False, default=ConceptReviewStatus.pending
    )
    member_count = Column(Integer, default=0, nullable=False)

    run_id = Column(Integer, ForeignKey("extraction_runs.id"), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    document_version = relationship("DocumentVersion")
    extraction_links = relationship(
        "ConceptExtractionLink", back_populates="concept",
        cascade="all, delete-orphan",
    )
    tracker_links = relationship(
        "ConceptTrackerLink", back_populates="concept",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "uq_compliance_concept_key",
            "document_version_id", "concept_type", "regulated_actor_family",
            unique=True,
        ),
        Index("ix_compliance_concept_review", "review_status"),
        Index("ix_compliance_concept_grounding", "grounding_status"),
    )


class ConceptExtractionLink(Base):
    """Links a compliance concept to one of its member extractions.

    role distinguishes the structural part each extraction plays:
      "anchor"      — obligation / right / mechanism that defines the requirement
      "enforcement" — a penalty or enforcing-body extraction (law-wide)
      "exception"   — a carve-out / exemption threshold (law-wide)
      "supporting"  — definition, actor map, or other context
    """

    __tablename__ = "concept_extraction_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    concept_id = Column(
        Integer, ForeignKey("compliance_concepts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    extraction_id = Column(
        Integer, ForeignKey("extractions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role = Column(String(20), nullable=False, default="anchor")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    concept = relationship("ComplianceConcept", back_populates="extraction_links")
    extraction = relationship("Extraction")

    __table_args__ = (
        Index(
            "uq_concept_extraction_link",
            "concept_id", "extraction_id",
            unique=True,
        ),
    )


class ConceptTrackerLink(Base):
    """Links a compliance concept to a tracker reference (Orrick / IAPP).

    match_status records the three-state trust check (§7 principle 6):
      "tracker_grounded" — tracker confirms the concept
      "tracker_conflict" — concept contradicts the tracker
      "tracker_silent"   — tracker has no value for this dimension
    """

    __tablename__ = "concept_tracker_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    concept_id = Column(
        Integer, ForeignKey("compliance_concepts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    tracker_source = Column(String(20), nullable=False)   # "orrick" / "iapp"
    tracker_ref = Column(String(120), nullable=False)     # "CO/SB 205"
    match_status = Column(String(30), nullable=False, default="tracker_grounded")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    concept = relationship("ComplianceConcept", back_populates="tracker_links")

    __table_args__ = (
        Index(
            "uq_concept_tracker_link",
            "concept_id", "tracker_source", "tracker_ref",
            unique=True,
        ),
    )


# ---------------------------------------------------------------------------
# RR6a — Durable pipeline events (replaces in-memory ring buffer)
# ---------------------------------------------------------------------------


class PipelineEvent(Base):
    """Durable record of pipeline state transitions (RR6a).

    Persists the same events that the ExtractionMonitor holds in memory,
    so run history survives server restarts.  One row per agent call result,
    passage completion, circuit-breaker trip, etc.

    event_type values mirror EventCategory in extraction_monitor.py:
      agent_success / agent_error / agent_abstention /
      passage_complete / run_start / run_complete /
      circuit_breaker / deduplication / validation_error
    """

    __tablename__ = "pipeline_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        Integer, ForeignKey("extraction_runs.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    source_record_id = Column(Integer, nullable=True, index=True)
    event_type = Column(String(50), nullable=False)
    agent_name = Column(String(100), nullable=True)
    extraction_count = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    confidence_tier = Column(String(1), nullable=True)
    error_message = Column(Text, nullable=True)
    # Provider/model attribution — e.g. "openai-gpt-oss-120b-nvidia" or
    # "google-gemma-4-26b-a4b-local". The "-nvidia"/"-local" suffix lets the
    # dashboard attribute failures to the active backend in mixed-provider runs.
    model_id = Column(String(100), nullable=True)
    details = Column(JSONB, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_pipeline_events_run_type", "run_id", "event_type"),
        Index("ix_pipeline_events_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# RR6e — Sync cursors (durable per-table sync position)
# ---------------------------------------------------------------------------


class SyncCursor(Base):
    """Tracks the last successfully synced row ID per table (RR6e).

    Enables ID-window pagination so incremental syncs skip already-synced rows
    instead of full-table re-POSTing.  One row per destination (table_name,
    destination) pair.
    """

    __tablename__ = "sync_cursors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    table_name = Column(String(100), nullable=False)
    destination = Column(String(50), nullable=False, default="supabase")
    last_synced_id = Column(Integer, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    rows_synced = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("uq_sync_cursor_table_dest", "table_name", "destination", unique=True),
    )


# ---------------------------------------------------------------------------
# RR7b — DocumentVersion versioning columns (added via migration)
# ---------------------------------------------------------------------------
# The columns below (session_year, retrieved_at, bill_number, source_hash)
# were added by migration v8w4x0y2z023.  They live on the existing
# DocumentVersion model; the model definition is extended by the migration.
