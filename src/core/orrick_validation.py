"""Orrick similarity validation — cross-check extractions against Orrick metadata.

Compares extraction payloads against Orrick's key_requirements and enforcement
summaries using token-level Jaccard similarity.  The check is intentionally
fuzzy: Orrick's table cells are concise summaries while our extractions are
detailed structured data, so we expect *overlap* rather than exact match.

The similarity score is used as a confidence signal:
  - High overlap (>= 0.25) suggests the extraction aligns with Orrick's analysis
  - Low overlap (< 0.10) on an obligation extraction may indicate drift
  - Score of 0.0 means no Orrick metadata was available (neutral — no penalty)

This module is deliberately simple (token Jaccard) to avoid adding embedding
model dependencies.  It can be upgraded to semantic similarity later if needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# Tokens shorter than this are noise (articles, prepositions)
_MIN_TOKEN_LENGTH = 3

# Common legal stop-words that inflate similarity without meaning
_STOP_WORDS = frozenset({
    "the", "and", "for", "that", "this", "with", "shall", "must", "may",
    "not", "any", "all", "such", "from", "under", "upon", "into", "each",
    "but", "are", "has", "have", "been", "will", "who", "which", "than",
    "its", "also", "other", "more", "when", "where", "than", "does", "can",
    "would", "should", "could", "about", "their", "them", "they", "what",
    "section", "subsection", "paragraph", "provision",
})

# Thresholds for interpreting similarity scores
SIMILARITY_HIGH = 0.25  # Strong alignment with Orrick
SIMILARITY_LOW = 0.10   # Weak alignment — worth flagging


@dataclass
class OrrickSimilarityResult:
    """Result of comparing an extraction against Orrick metadata."""

    key_requirements_similarity: float  # 0.0–1.0 Jaccard score
    enforcement_similarity: float       # 0.0–1.0 Jaccard score
    combined_score: float               # Weighted average
    has_orrick_data: bool               # Whether any Orrick metadata existed
    matched_tokens: list[str]           # Sample of overlapping tokens (for debugging)


def _tokenize(text: str) -> set[str]:
    """Tokenize and normalize text for comparison.

    Lowercases, strips punctuation, removes stop-words and short tokens.
    """
    if not text:
        return set()
    # Split on non-alphanumeric, lowercase
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {
        t for t in tokens
        if len(t) >= _MIN_TOKEN_LENGTH and t not in _STOP_WORDS
    }


def _jaccard(set_a: set[str], set_b: set[str]) -> tuple[float, list[str]]:
    """Compute Jaccard similarity and return overlapping tokens."""
    if not set_a or not set_b:
        return 0.0, []
    intersection = set_a & set_b
    union = set_a | set_b
    score = len(intersection) / len(union) if union else 0.0
    return score, sorted(intersection)[:20]  # Cap sample for logging


def compute_orrick_similarity(
    extraction_payload: dict,
    orrick_key_requirements: str | None,
    orrick_enforcement: str | None,
) -> OrrickSimilarityResult:
    """Compare an extraction payload against Orrick metadata.

    Extracts text from the payload (action, subject, condition, penalty
    fields, evidence spans) and computes token Jaccard similarity against
    Orrick's key_requirements and enforcement summaries.

    Args:
        extraction_payload: The extraction dict from an agent.
        orrick_key_requirements: Orrick's "Key Requirements" cell text.
        orrick_enforcement: Orrick's "Enforcements & Penalties" cell text.

    Returns:
        OrrickSimilarityResult with per-field and combined scores.
    """
    has_data = bool(orrick_key_requirements or orrick_enforcement)

    if not has_data:
        return OrrickSimilarityResult(
            key_requirements_similarity=0.0,
            enforcement_similarity=0.0,
            combined_score=0.0,
            has_orrick_data=False,
            matched_tokens=[],
        )

    # Build extraction text from relevant fields
    extraction_text_parts = []
    for field in (
        "action", "subject", "condition", "object",
        "modality", "subject_normalized",
        "threshold_condition", "threshold_value",
        "exception_type", "description",
        "ambiguous_text", "interpretation_notes",
        "suggested_clarification",
    ):
        val = extraction_payload.get(field)
        if val and isinstance(val, str):
            extraction_text_parts.append(val)

    # Include nested enforcement/timeline fields
    for nested_key in ("enforcement", "timeline", "exceptions"):
        nested = extraction_payload.get(nested_key)
        if isinstance(nested, dict):
            for v in nested.values():
                if isinstance(v, str):
                    extraction_text_parts.append(v)
        elif isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    for v in item.values():
                        if isinstance(v, str):
                            extraction_text_parts.append(v)

    # Include evidence span text
    for span in extraction_payload.get("evidence_spans", []):
        if isinstance(span, dict) and span.get("text"):
            extraction_text_parts.append(span["text"])

    extraction_tokens = _tokenize(" ".join(extraction_text_parts))

    # Compare against key_requirements
    kr_tokens = _tokenize(orrick_key_requirements or "")
    kr_score, kr_matched = _jaccard(extraction_tokens, kr_tokens)

    # Compare against enforcement
    enf_tokens = _tokenize(orrick_enforcement or "")
    enf_score, enf_matched = _jaccard(extraction_tokens, enf_tokens)

    # Weighted average: key_requirements weighted higher since it covers
    # broader obligation content; enforcement is more specific
    if orrick_key_requirements and orrick_enforcement:
        combined = 0.6 * kr_score + 0.4 * enf_score
    elif orrick_key_requirements:
        combined = kr_score
    else:
        combined = enf_score

    all_matched = sorted(set(kr_matched + enf_matched))[:20]

    return OrrickSimilarityResult(
        key_requirements_similarity=round(kr_score, 4),
        enforcement_similarity=round(enf_score, 4),
        combined_score=round(combined, 4),
        has_orrick_data=True,
        matched_tokens=all_matched,
    )


def validate_extraction_against_orrick(
    extraction_payload: dict,
    context: dict,
) -> OrrickSimilarityResult | None:
    """Convenience wrapper that pulls Orrick data from the build context.

    Returns None if no Orrick metadata is available (neutral — no impact).
    """
    key_reqs = context.get("key_requirements")
    enforcement = context.get("enforcement_summary")

    if not key_reqs and not enforcement:
        return None

    return compute_orrick_similarity(extraction_payload, key_reqs, enforcement)
