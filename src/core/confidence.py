"""Simplified confidence scoring model — Recommendation #8.

4-component weighted score replacing the original 7-component model.
Self-check and cross-agent components are eliminated since those validation
paths are removed per Recommendations #2 and #3.

Components:
  - Schema validity (0.25): Pydantic validation pass/fail (binary)
  - Evidence grounding (0.35): Proportion of fields with verified evidence spans
  - Completeness (0.20): Proportion of non-null optional fields
  - Source quality (0.20): Phase 1 parse quality score

Tiers:
  A: >= 0.85 (auto-approve candidates)
  B: >= 0.70 (standard review)
  C: >= 0.50 (detailed review required)
  D: < 0.50  (likely extraction failure)
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass
class ConfidenceBreakdown:
    """Detailed breakdown of confidence score components."""

    schema_validity: float
    evidence_grounding: float
    completeness: float
    source_quality: float
    total_score: float
    tier: str


# Component weights
WEIGHT_SCHEMA_VALIDITY = 0.25
WEIGHT_EVIDENCE_GROUNDING = 0.35
WEIGHT_COMPLETENESS = 0.20
WEIGHT_SOURCE_QUALITY = 0.20

# Tier thresholds
TIER_A_THRESHOLD = 0.85
TIER_B_THRESHOLD = 0.70
TIER_C_THRESHOLD = 0.50


def compute_confidence(
    schema_valid: bool,
    evidence_spans: list[dict],
    extraction_payload: dict,
    schema_class: type[BaseModel],
    parse_quality_score: float | None = None,
) -> ConfidenceBreakdown:
    """Compute the confidence score for an extraction.

    Args:
        schema_valid: Whether Pydantic validation passed (binary).
        evidence_spans: List of evidence span dicts with 'verified' field.
        extraction_payload: The raw extraction payload dict.
        schema_class: The Pydantic model class for computing completeness.
        parse_quality_score: Phase 1 parse quality score (0.0-1.0).

    Returns:
        ConfidenceBreakdown with component scores and final tier.
    """
    # 1. Schema validity (binary)
    schema_score = 1.0 if schema_valid else 0.0

    # 2. Evidence grounding — proportion of spans that are verified
    if evidence_spans:
        verified_count = sum(1 for s in evidence_spans if s.get("verified", False))
        evidence_score = verified_count / len(evidence_spans)
    else:
        evidence_score = 0.0

    # 3. Completeness — proportion of non-null optional fields
    completeness_score = _compute_completeness(extraction_payload, schema_class)

    # 4. Source quality — from ingestion pipeline
    source_score = parse_quality_score if parse_quality_score is not None else 0.5

    # Weighted total
    total = (
        WEIGHT_SCHEMA_VALIDITY * schema_score
        + WEIGHT_EVIDENCE_GROUNDING * evidence_score
        + WEIGHT_COMPLETENESS * completeness_score
        + WEIGHT_SOURCE_QUALITY * source_score
    )

    tier = _score_to_tier(total)

    return ConfidenceBreakdown(
        schema_validity=schema_score,
        evidence_grounding=evidence_score,
        completeness=completeness_score,
        source_quality=source_score,
        total_score=round(total, 4),
        tier=tier,
    )


def _compute_completeness(payload: dict, schema_class: type[BaseModel]) -> float:
    """Compute the proportion of optional fields that have non-null values."""
    fields = schema_class.model_fields
    optional_fields = [
        name for name, field in fields.items()
        if not field.is_required()
    ]

    if not optional_fields:
        return 1.0

    filled = sum(
        1 for name in optional_fields
        if payload.get(name) is not None
    )

    return filled / len(optional_fields)


def _score_to_tier(score: float) -> str:
    """Map a confidence score to a tier letter."""
    if score >= TIER_A_THRESHOLD:
        return "A"
    elif score >= TIER_B_THRESHOLD:
        return "B"
    elif score >= TIER_C_THRESHOLD:
        return "C"
    else:
        return "D"
