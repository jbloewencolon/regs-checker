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
from datetime import date, datetime

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
from sqlalchemy.orm import DeclarativeBase, relationship


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
# ---------------------------------------------------------------------------


class RawArtifact(Base):
    __tablename__ = "raw_artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_version_id = Column(
        Integer, ForeignKey("document_versions.id"), nullable=False, index=True
    )
    sha256_hash = Column(String(64), nullable=False, unique=True)
    s3_key = Column(Text, nullable=False)
    content_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    is_primary = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

    document_version = relationship("DocumentVersion", back_populates="raw_artifacts")


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
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    duration_ms = Column(Integer, nullable=True)  # wall-clock LLM call time in milliseconds
    extraction_job_id = Column(Integer, ForeignKey("extraction_jobs.id"), index=True)
    payload_hash = Column(String(64), nullable=True, index=True)  # SHA-256 of normalized payload
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    source_record = relationship("NormalizedSourceRecord", back_populates="extractions")
    extraction_job = relationship("ExtractionJob", back_populates="extractions")
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
# 8. Extraction Jobs
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
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    truncated = Column(Boolean, default=False)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    document_version = relationship("DocumentVersion")

    __table_args__ = (
        # One row per law per agent — re-runs upsert rather than duplicate
        Index(
            "uq_bill_level_extractions",
            "document_version_id", "agent_name",
            unique=True,
        ),
    )
