"""Confidence scoring model — Orrick-gated, near-term earnable signals only.

Hard gate: extractions without Orrick validation data are automatically Tier D.

Active weighted signals (near-term, earnable today):
  - Orrick alignment  (0.50): Token similarity vs Orrick key_requirements/enforcement
    REQUIRED: no Orrick data → auto-Tier D.
  - Evidence grounding (0.35): Proportion of verified evidence spans (verbatim-quote check)
  - Citation quality  (0.15): Specificity of section_reference (subsection > § > generic)

Phase-in signals (weight redistributes to active when absent):
  - Cross-validation  (target 0.10): Post-extraction CV agent accuracy score
  - IAPP alignment    (target 0.20): Wires in after Orrick-only trust check is stable
  - Gap detection     (target 0.05): When gap detector is confirmed wired
  - Analyst review    (target 0.10): When review staffing is live

Near-term weights renormalize to 1.0 over active signals.  As each phase-in
signal lands, scale the base weights proportionally so the sum stays 1.0.
See engineering guide §3.2 for the full target model.

Diagnostic fields (schema_validity, completeness, source_quality) are still
computed and returned in ConfidenceBreakdown but are NOT included in the
weighted total — they are retained for observability only.

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
    broad_spans: bool = False   # True when a verified span exceeds 50% of passage length
    section_ref_quality: float = 0.0  # 0.0–1.0 specificity of section_reference field


# Near-term active weights (earnable signals only — sum to 1.0 over these three).
WEIGHT_ORRICK_ALIGNMENT = 0.50
WEIGHT_EVIDENCE_GROUNDING = 0.35
WEIGHT_CITATION = 0.15

# Phase-in weights: added when each signal becomes computable; base weights
# scale proportionally to keep the total at 1.0.
WEIGHT_CV_TARGET = 0.10          # cross-validation (phases in when confirmed)
WEIGHT_IAPP_TARGET = 0.20        # IAPP alignment (fast-follow after Orrick-only)

# Diagnostic-only constants (not part of the weighted formula).
_DIAG_WEIGHT_SCHEMA_VALIDITY = 0.10
_DIAG_WEIGHT_COMPLETENESS = 0.10
_DIAG_WEIGHT_SOURCE_QUALITY = 0.05

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
    passage_text: str | None = None,
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

    # 2. Evidence grounding — proportion of spans that are verified, with span
    #    length penalty for spans that copy-paste the bulk of the passage rather
    #    than quoting a targeted phrase.
    broad_spans = False
    if evidence_spans:
        verified_count = sum(1 for s in evidence_spans if s.get("verified", False))
        evidence_score = verified_count / len(evidence_spans)

        # Span length penalty: if a verified span is longer than 50% of the
        # passage, it is not a targeted quote — penalise evidence grounding.
        if passage_text and len(passage_text) > 0:
            max_ratio = max(
                (
                    len(s.get("text", "")) / len(passage_text)
                    for s in evidence_spans
                    if s.get("verified")
                ),
                default=0.0,
            )
            if max_ratio > 0.75:
                # Span is nearly the whole passage — heavy penalty
                evidence_score *= 0.60
                broad_spans = True
            elif max_ratio > 0.50:
                # Span is majority of passage — moderate penalty
                evidence_score *= 0.80
                broad_spans = True
    else:
        evidence_score = 0.0

    # 3. Completeness — proportion of non-null optional fields
    completeness_score = _compute_completeness(extraction_payload, schema_class)

    # 3a. Section reference quality — continuous sub-signal added to completeness.
    #     A highly specific section reference (§ 6-1-1702(3)(a)) is stronger
    #     evidence of grounding than a generic one (Section 5) or none at all.
    #     Blended into completeness at 20% weight to avoid weight-sum changes.
    section_ref_quality = _score_section_reference(
        extraction_payload.get("section_reference")
    )
    completeness_score = completeness_score * 0.80 + section_ref_quality * 0.20

    # 4. Source quality — diagnostic only (not in weighted formula)
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

    # Cross-validation — accuracy score from verification agent
    has_cv = cross_validation_score is not None
    cv_score = cross_validation_score if has_cv else 0.0

    # If no Orrick data, force Tier D immediately.
    # Compute a diagnostic score from earnable signals for observability only.
    if not has_orrick:
        diag_components: list[tuple[float, float]] = [
            (WEIGHT_EVIDENCE_GROUNDING, evidence_score),
            (WEIGHT_CITATION, section_ref_quality),
        ]
        if has_cv:
            diag_components.append((WEIGHT_CV_TARGET, cv_score))
        diag_weight = sum(w for w, _ in diag_components)
        diag_total = sum(w * s for w, s in diag_components) / diag_weight if diag_weight > 0 else 0.0
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
            broad_spans=broad_spans,
            section_ref_quality=section_ref_quality,
        )

    # Normal path: Orrick data exists.
    # Active formula: Orrick (0.50) + evidence (0.35) + citation (0.15).
    # When CV is present, scale the base by (1 - WEIGHT_CV_TARGET) and add CV
    # at its target weight so the sum stays at 1.0.
    if has_cv:
        base_scale = 1.0 - WEIGHT_CV_TARGET
        components: list[tuple[float, float]] = [
            (WEIGHT_ORRICK_ALIGNMENT * base_scale, orrick_score),
            (WEIGHT_EVIDENCE_GROUNDING * base_scale, evidence_score),
            (WEIGHT_CITATION * base_scale, section_ref_quality),
            (WEIGHT_CV_TARGET, cv_score),
        ]
    else:
        components = [
            (WEIGHT_ORRICK_ALIGNMENT, orrick_score),
            (WEIGHT_EVIDENCE_GROUNDING, evidence_score),
            (WEIGHT_CITATION, section_ref_quality),
        ]

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
        broad_spans=broad_spans,
        section_ref_quality=section_ref_quality,
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
        if payload.get(name) is not None and payload.get(name) != ""
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


def _score_section_reference(section_ref: str | None) -> float:
    """Score the specificity of a section reference on a 0.0–1.0 scale.

    Highly specific references (e.g. '§ 6-1-1702(3)(a)') indicate the
    extraction is grounded in a precise location in the law — a stronger
    quality signal than a generic reference ('Section 5') or none at all.

    Scoring:
      1.0 — Contains subsection notation: § followed by digits and brackets/parens,
             or explicit subsection letter/number like (3)(a)(ii)
      0.6 — Has a section symbol (§) or numeric reference but no subsection detail
      0.3 — Generic label only: 'Section X', 'Part Y', 'Article Z'
      0.0 — No section reference
    """
    import re as _re

    if not section_ref or not section_ref.strip():
        return 0.0

    ref = section_ref.strip()

    # High specificity: § with subsection detail, or explicit nested notation
    if _re.search(r"§\s*[\d\w-]+(?:\.\d+)*\s*[\(\[]", ref):
        return 1.0
    if _re.search(r"\(\s*\d+\s*\)\s*\(\s*[a-z]\s*\)", ref, _re.IGNORECASE):
        return 1.0
    # Medium: has § symbol or clear numeric citation
    if "§" in ref or _re.search(r"\b\d{1,4}[a-z]?(?:\.\d+)+\b", ref, _re.IGNORECASE):
        return 0.6
    # Low: generic label
    if _re.search(r"\b(?:section|part|article|subsection|paragraph)\b", ref, _re.IGNORECASE):
        return 0.3

    # Has something but unrecognised pattern — give minimal credit
    return 0.2
