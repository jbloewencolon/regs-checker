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
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    document_version = relationship("DocumentVersion", back_populates="ingestion_jobs")


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
    model_id = Column(String(100))
    extraction_job_id = Column(Integer, ForeignKey("extraction_jobs.id"), index=True)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    source_record = relationship("NormalizedSourceRecord", back_populates="extractions")
    extraction_job = relationship("ExtractionJob", back_populates="extractions")
    review_queue_items = relationship("ReviewQueueItem", back_populates="extraction")

    __table_args__ = (
        Index("ix_extractions_type_status", "extraction_type", "review_status"),
        Index("ix_extractions_payload", "payload", postgresql_using="gin"),
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
