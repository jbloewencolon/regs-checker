"""Confidence scoring model with 6 components — Orrick-gated.

6-component weighted score with a hard gate: extractions without Orrick
validation data are automatically Tier D regardless of other metrics.
This ensures only law-firm-validated extractions can reach production
tiers (A/B/C).

Components:
  - Schema validity (0.10): Pydantic validation pass/fail (binary)
  - Evidence grounding (0.20): Proportion of fields with verified evidence spans
  - Completeness (0.10): Proportion of non-null optional fields
  - Source quality (0.05): Phase 1 parse quality score
  - Orrick alignment (0.30): Token similarity vs Orrick key_requirements/enforcement
    REQUIRED: extractions without Orrick data are auto-Tier D.
  - Cross-validation (0.25): Accuracy score from post-extraction verification
    When not yet verified, this component is excluded and its weight is
    redistributed to the remaining active components.

Tiers:
  A: >= 0.85 (auto-approve candidates)
  B: >= 0.70 (standard review)
  C: >= 0.50 (detailed review required)
  D: < 0.50 OR no Orrick validation data (requires human review)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass
class ConfidenceBreakdown:
    """Detailed breakdown of confidence score components."""

    schema_validity: float
    evidence_grounding: float
    completeness: float
    source_quality: float
    orrick_alignment: float
    cross_validation: float
    total_score: float
    tier: str
    orrick_matched_tokens: list[str] = field(default_factory=list)
    orrick_gated: bool = False  # True when tier was forced to D due to missing Orrick data


# Component weights (sum to 1.0 when all components are active)
# Orrick alignment is now the heaviest single signal — law-firm validation
# is the most reliable indicator of extraction accuracy.
WEIGHT_SCHEMA_VALIDITY = 0.10
WEIGHT_EVIDENCE_GROUNDING = 0.20
WEIGHT_COMPLETENESS = 0.10
WEIGHT_SOURCE_QUALITY = 0.05
WEIGHT_ORRICK_ALIGNMENT = 0.30
WEIGHT_CROSS_VALIDATION = 0.25

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
    orrick_similarity: "OrrickSimilarityResult | None" = None,
    cross_validation_score: float | None = None,
) -> ConfidenceBreakdown:
    """Compute the confidence score for an extraction.

    Args:
        schema_valid: Whether Pydantic validation passed (binary).
        evidence_spans: List of evidence span dicts with 'verified' field.
        extraction_payload: The raw extraction payload dict.
        schema_class: The Pydantic model class for computing completeness.
        parse_quality_score: Phase 1 parse quality score (0.0-1.0).
        orrick_similarity: Optional Orrick similarity result for alignment scoring.
        cross_validation_score: Optional accuracy score from cross-validation
            agent (0.0-1.0). None means not yet verified.

    Returns:
        ConfidenceBreakdown with component scores and final tier.
    """
    from src.core.orrick_validation import OrrickSimilarityResult

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

    # 5. Orrick alignment — token similarity with Orrick metadata
    # HARD GATE: no Orrick data → automatic Tier D
    has_orrick = (
        orrick_similarity is not None and orrick_similarity.has_orrick_data
    )
    orrick_score = 0.0
    matched_tokens: list[str] = []
    if has_orrick:
        cs = orrick_similarity.combined_score
        if cs >= 0.25:
            orrick_score = 1.0
        elif cs >= 0.10:
            orrick_score = 0.5 + (cs - 0.10) / (0.25 - 0.10) * 0.5
        else:
            orrick_score = 0.3
        matched_tokens = orrick_similarity.matched_tokens

    # 6. Cross-validation — accuracy score from verification agent
    has_cv = cross_validation_score is not None
    cv_score = cross_validation_score if has_cv else 0.0

    # If no Orrick data, force Tier D immediately.
    # Still compute component scores for the breakdown (diagnostic value),
    # but the tier and total_score reflect the gate.
    if not has_orrick:
        # Compute a nominal score from non-Orrick components for diagnostics
        diag_components: list[tuple[float, float]] = [
            (WEIGHT_SCHEMA_VALIDITY, schema_score),
            (WEIGHT_EVIDENCE_GROUNDING, evidence_score),
            (WEIGHT_COMPLETENESS, completeness_score),
            (WEIGHT_SOURCE_QUALITY, source_score),
        ]
        if has_cv:
            diag_components.append((WEIGHT_CROSS_VALIDATION, cv_score))
        diag_weight = sum(w for w, _ in diag_components)
        diag_total = sum(w * s for w, s in diag_components) / diag_weight if diag_weight > 0 else 0.0

        # Cap the total score below Tier C threshold so tier is always D
        capped_score = min(diag_total, TIER_C_THRESHOLD - 0.01)

        return ConfidenceBreakdown(
            schema_validity=schema_score,
            evidence_grounding=evidence_score,
            completeness=completeness_score,
            source_quality=source_score,
            orrick_alignment=0.0,
            cross_validation=cv_score,
            total_score=round(capped_score, 4),
            tier="D",
            orrick_matched_tokens=[],
            orrick_gated=True,
        )

    # Normal path: Orrick data exists — compute weighted average
    components: list[tuple[float, float]] = [
        (WEIGHT_SCHEMA_VALIDITY, schema_score),
        (WEIGHT_EVIDENCE_GROUNDING, evidence_score),
        (WEIGHT_COMPLETENESS, completeness_score),
        (WEIGHT_SOURCE_QUALITY, source_score),
        (WEIGHT_ORRICK_ALIGNMENT, orrick_score),
    ]
    if has_cv:
        components.append((WEIGHT_CROSS_VALIDATION, cv_score))

    active_weight = sum(w for w, _ in components)
    total = sum(w * s for w, s in components) / active_weight if active_weight > 0 else 0.0

    tier = _score_to_tier(total)

    return ConfidenceBreakdown(
        schema_validity=schema_score,
        evidence_grounding=evidence_score,
        completeness=completeness_score,
        source_quality=source_score,
        orrick_alignment=orrick_score,
        cross_validation=cv_score,
        total_score=round(total, 4),
        tier=tier,
        orrick_matched_tokens=matched_tokens,
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
