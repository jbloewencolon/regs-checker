"""Grounding-based admission gate for extracted obligations and rights (Phase 4).

Determines whether an extraction passes the quality bar to enter the
trusted output set (admitted) or requires human review (needs_review).

Admission policy
----------------
admitted:
  - At least one evidence span has verified=True   (text grounded in statute)
  - OR confidence_tier is A / B / C                (tracker-confirmed: Orrick/IAPP)

needs_review:
  - Zero verified spans AND confidence_tier is D   (no grounding from any source)

excluded (reserved, not yet assigned by the pipeline):
  - Future: explicitly human-rejected rows, TMP-only laws with no text, etc.

Rationale: A Tier-D extraction with zero verified spans has no independent
corroboration — neither tracker data nor verbatim statutory text confirms it.
Those rows go to needs_review rather than into the trusted export set.
A Tier-D extraction that has at least one verified span is admitted: the model
found real statutory text to support the claim, even though trackers haven't
confirmed the law.
"""

from __future__ import annotations

ADMITTED = "admitted"
NEEDS_REVIEW = "needs_review"
EXCLUDED = "excluded"  # reserved


def compute_admission_status(
    evidence_spans: list[dict] | None,
    confidence_tier: str,
) -> str:
    """Return the admission status string for one extraction.

    Args:
        evidence_spans: List of evidence span dicts from Extraction.evidence_spans.
                        Each span should have a ``verified`` key (bool).
        confidence_tier: The extraction's confidence tier string ("A", "B", "C", "D").

    Returns:
        One of: "admitted", "needs_review", "excluded".
    """
    spans = evidence_spans or []
    has_verified_span = any(s.get("verified") is True for s in spans)

    if has_verified_span:
        return ADMITTED

    # No verified spans — rely on tracker grounding (tier A/B/C = has Orrick/IAPP data)
    tier = (confidence_tier or "D").upper().strip()
    if tier != "D":
        return ADMITTED

    # Zero verified spans + Tier D: no grounding from any source
    return NEEDS_REVIEW


def admission_summary(rows: list[dict]) -> dict[str, int]:
    """Aggregate admission counts over a list of extraction dicts.

    Each dict should have ``evidence_spans`` (list) and ``confidence_tier`` (str).
    Returns {admitted: N, needs_review: N, excluded: N, total: N}.
    """
    counts: dict[str, int] = {ADMITTED: 0, NEEDS_REVIEW: 0, EXCLUDED: 0}
    for row in rows:
        status = compute_admission_status(
            row.get("evidence_spans"), row.get("confidence_tier", "D")
        )
        counts[status] = counts.get(status, 0) + 1
    counts["total"] = sum(counts.values())
    return counts
