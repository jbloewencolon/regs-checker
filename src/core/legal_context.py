"""Legal-context classification (Phase 2d).

The ``preemption_signal`` extraction type was named for one of the things it
captures (federal preemption) but in practice it collects a grab-bag of
cross-jurisdictional signals: true preemption, constitutional limits,
interstate friction, agency-authority allocation, and bare references to
other laws.  Lumping these under "preemption" — and surfacing a large,
low-value ``other`` bucket — makes the data hard to trust and act on.

This module reclassifies a preemption_signal payload into a small set of
**typed legal-context categories** and flags low-value rows so the UI can
hide them.  It is non-destructive: the raw ``conflict_type`` is preserved;
``legal_context_type`` is layered on top.

Categories:
  true_preemption      — federal or constitutional law displaces state law
  constitutional_limit — a constitutional challenge (e.g. First Amendment)
  interstate_conflict  — friction between state regimes / Commerce Clause
  agency_jurisdiction  — allocation of authority among agencies (not a conflict)
  cross_law_reference  — a citation to / incorporation of another law, not a conflict
  unclassified         — unknown or ``other`` with no usable signal (hidden)
"""

from __future__ import annotations

from typing import Any

TRUE_PREEMPTION = "true_preemption"
CONSTITUTIONAL_LIMIT = "constitutional_limit"
INTERSTATE_CONFLICT = "interstate_conflict"
AGENCY_JURISDICTION = "agency_jurisdiction"
CROSS_LAW_REFERENCE = "cross_law_reference"
UNCLASSIFIED = "unclassified"

LEGAL_CONTEXT_TYPES = (
    TRUE_PREEMPTION,
    CONSTITUTIONAL_LIMIT,
    INTERSTATE_CONFLICT,
    AGENCY_JURISDICTION,
    CROSS_LAW_REFERENCE,
    UNCLASSIFIED,
)

# Low-value categories the UI should hide by default.
LOW_VALUE_TYPES = frozenset({UNCLASSIFIED})

# Raw conflict_type (from PreemptionSignalPayload) → typed legal-context category.
CONFLICT_TYPE_TO_LEGAL_CONTEXT: dict[str, str] = {
    "federal_preemption": TRUE_PREEMPTION,
    "dormant_commerce_clause": TRUE_PREEMPTION,
    "first_amendment": CONSTITUTIONAL_LIMIT,
    "interstate_commerce": INTERSTATE_CONFLICT,
    "cross_state_conflict": INTERSTATE_CONFLICT,
    "agency_jurisdiction": AGENCY_JURISDICTION,
    "other": UNCLASSIFIED,
}


def classify_legal_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Classify a preemption_signal payload into a typed legal-context category.

    Args:
        payload: A PreemptionSignalPayload dict.

    Returns:
        ``{"legal_context_type": str, "display": bool, "raw_conflict_type": str}``

        ``display`` is False for low-value rows (unclassified with no usable
        signal) so the UI can hide them.
    """
    raw = (payload.get("conflict_type") or "").strip().lower()
    has_cross_law_refs = bool(payload.get("cross_law_refs"))
    has_preemption_language = bool(payload.get("preemption_language"))
    has_related_authority = bool(payload.get("related_authority"))

    if raw in CONFLICT_TYPE_TO_LEGAL_CONTEXT:
        legal_context_type = CONFLICT_TYPE_TO_LEGAL_CONTEXT[raw]
    else:
        # Unknown conflict_type — fall back to reference detection.
        legal_context_type = UNCLASSIFIED

    # Reclassify weak/unknown rows that are really just citations to other
    # laws as cross_law_reference rather than burying them in `other`.
    if (
        legal_context_type == UNCLASSIFIED
        and has_cross_law_refs
        and not has_preemption_language
        and not has_related_authority
    ):
        legal_context_type = CROSS_LAW_REFERENCE

    display = legal_context_type not in LOW_VALUE_TYPES
    return {
        "legal_context_type": legal_context_type,
        "display": display,
        "raw_conflict_type": raw or None,
    }


def is_low_value(payload: dict[str, Any]) -> bool:
    """True if this legal-context row should be hidden by default."""
    return not classify_legal_context(payload)["display"]
