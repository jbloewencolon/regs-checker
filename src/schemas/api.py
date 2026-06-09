"""Pydantic v2 schemas for API request/response models.

Shared between /internal/ review routes and /v1/ product API routes
(Recommendation #6).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared response models
# ---------------------------------------------------------------------------


class ConfidenceBreakdownResponse(BaseModel):
    """Detailed confidence score component breakdown.

    Active signals (weighted into total_score):
      orrick_alignment, evidence_grounding, citation quality (via section_ref_quality).

    Diagnostic signals (computed, not weighted):
      schema_validity, completeness, source_quality.

    Orthogonal dimensions (RR5b — separate axes, not rolled into total_score):
      source_grounding_score: evidence + citation quality (how well grounded in text)
      tracker_alignment_score: Orrick/IAPP alignment (how well confirmed by trackers)
      schema_completeness_score: structural validity + field completeness
    """

    schema_validity: float = 0.0
    evidence_grounding: float = 0.0
    completeness: float = 0.0
    source_quality: float = 0.0
    orrick_alignment: float = 0.0
    orrick_matched_tokens: list[str] = Field(default_factory=list)
    # RR5b — orthogonal dimensions
    source_grounding_score: float = 0.0
    tracker_alignment_score: float = 0.0
    schema_completeness_score: float = 0.0


class ExtractionResponse(BaseModel):
    """Standard extraction response used by both internal and external APIs."""

    id: int
    extraction_type: str
    payload: dict
    evidence_spans: list[dict]
    confidence_score: float
    confidence_tier: str
    confidence_breakdown: ConfidenceBreakdownResponse | None = None
    review_status: str
    model_id: str | None = None
    source_text: str | None = None
    section_path: str | None = None
    document_title: str | None = None
    jurisdiction_code: str | None = None
    jurisdiction_name: str | None = None
    effective_date: date | None = None
    temporal_status: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class PaginatedResponse(BaseModel):
    """Paginated list wrapper."""

    items: list[ExtractionResponse]
    total: int
    page: int
    per_page: int
    pages: int


# ---------------------------------------------------------------------------
# /internal/ review models
# ---------------------------------------------------------------------------


class ReviewDecision(BaseModel):
    """Review action submitted by a human reviewer."""

    action: str = Field(description="approved / rejected / needs_revision")
    reviewer: str
    comment: str | None = None
    corrections: dict | None = None


class ReviewQueueResponse(BaseModel):
    """Review queue item with extraction details."""

    queue_id: int
    extraction: ExtractionResponse
    priority: int
    assigned_to: str | None = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# /v1/ product API models
# ---------------------------------------------------------------------------


class ObligationQuery(BaseModel):
    """Query parameters for obligation lookup."""

    jurisdiction: str | None = None
    subject: str | None = None
    modality: str | None = None
    active_only: bool = True
    min_confidence: str = "B"
    page: int = 1
    per_page: int = 25


class ComplianceMatrixCell(BaseModel):
    """A single cell in the compliance matrix."""

    jurisdiction_code: str
    jurisdiction_name: str
    subject_area: str | None = None
    modality: str | None = None
    subject_normalized: str | None = None
    obligation_count: int
    avg_confidence: float

    model_config = {"from_attributes": True}


class ComplianceMatrixResponse(BaseModel):
    """Full compliance matrix response."""

    cells: list[ComplianceMatrixCell]
    jurisdictions: list[str]
    last_refreshed: datetime | None = None


class DependencyNode(BaseModel):
    """A node in the obligation dependency tree."""

    extraction_id: int
    extraction_type: str
    payload: dict
    confidence_tier: str
    depth: int
    dependency_type: str


class DependencyTreeResponse(BaseModel):
    """Dependency tree rooted at a given obligation."""

    root_extraction_id: int
    dependencies: list[DependencyNode]
    max_depth: int


class ChangeFeedItem(BaseModel):
    """An item in the change intelligence feed."""

    event_type: str
    event_date: date
    document_title: str
    jurisdiction_code: str
    description: str | None = None
    affected_extraction_ids: list[int] = Field(default_factory=list)


class ChangeFeedResponse(BaseModel):
    """Change intelligence feed response."""

    items: list[ChangeFeedItem]
    total: int
    since: date | None = None
