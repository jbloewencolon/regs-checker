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

QA-6 adds a credibility assessment on top: the 2026-07-13 run produced 81
preemption signals of which the overwhelming majority asserted a conflict on
no basis beyond "this passage references another statute" — citing the law's
OWN state codes as a "cross_state_conflict", parroting the prompt's example
authorities verbatim ("Dec 2025 Federal EO on AI", "US Constitution Art. I
§ 8"), or self-negating in the description ("...does not appear to conflict
with federal law"). A conflict-asserting signal is credible only when it is
anchored: a verbatim preemption/savings clause, a concrete federal citation,
or a named other state. Non-credible rows are hidden at sync time
(``display: False``) and dropped at extraction time (PreemptionAgent).
"""

from __future__ import annotations

import re
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

# ---------------------------------------------------------------------------
# QA-6: credibility assessment for conflict-asserting signals
# ---------------------------------------------------------------------------

# Types that assert a conflict with an EXTERNAL jurisdiction — these need an
# external anchor to be credible. first_amendment / agency_jurisdiction /
# other are excluded: a 1A flag or an intra-state dual-regulator note doesn't
# claim a second jurisdiction exists.
CONFLICT_ASSERTING_TYPES = frozenset({
    "cross_state_conflict",
    "federal_preemption",
    "interstate_commerce",
    "dormant_commerce_clause",
})

# Example authorities that appear verbatim in the agent prompt / schema docs.
# The 8B model parrots them into related_authority when the passage names no
# authority at all (observed 14x on the 2026-07-13 run), so their presence is
# evidence of nothing — they are removed from the haystack before anchoring.
_PROMPT_EXAMPLE_AUTHORITIES = (
    "dec 2025 federal eo on ai",
    "us constitution art. i § 8",
    "eu ai act art. 6",
)

# The model's own conclusion that there is NO conflict, emitted as a signal
# anyway ("This passage references the Welfare and Institutions Code and does
# not appear to conflict with federal law" — 5x on the 2026-07-13 run).
# "incorporates federal law" is the same failure: incorporation by reference
# is a cross-law reference, not a conflict.
_SELF_NEGATION_RE = re.compile(
    r"\b(?:does\s+not|do\s+not|doesn't|will\s+not|would\s+not)\s+"
    r"(?:appear\s+to\s+)?(?:conflict|preempt|implicate)"
    r"|\bno\s+(?:apparent\s+|direct\s+|actual\s+)?(?:conflict|preemption)\b"
    r"|\bunlikely\s+to\s+(?:conflict|be\s+preempted)"
    r"|\bincorporates\s+federal\s+law\b"
    r"|\bconsistent\s+with\s+federal\s+law\b",
    re.IGNORECASE,
)

# Concrete federal anchors: an actual citation or a named federal statute.
# Deliberately absent: "Commerce Clause" / "US Constitution" alone — every
# vacuous dormant-commerce hedge invokes them, and the genuine ones also
# carry a statute or clause quote.
_FEDERAL_ANCHOR_RE = re.compile(
    r"\b\d+\s*u\.?\s?s\.?\s?c\.?\b"            # 47 U.S.C. § 230, 42 USC 2000e
    r"|\b\d+\s*c\.?\s?f\.?\s?r\.?\b"           # 16 CFR Part 312
    r"|\bpublic\s+law\s+\d"                    # Public Law 104-191
    r"|\bpub\.?\s?l\.?\s+\d"
    r"|\bsection\s+230\b"                      # CDA 230 shorthand
    r"|\bcommunications\s+decency\s+act\b"
    r"|\bfirst\s+amendment\b|\bfourteenth\s+amendment\b"
    r"|\bhipaa\b|\bcoppa\b|\bfcra\b|\bgina\b|\badea\b"
    r"|\bamericans\s+with\s+disabilities\b|\bcivil\s+rights\s+act\b"
    r"|\bage\s+discrimination\s+in\s+employment\b"
    r"|\btitle\s+vii\b"
    r"|\bexecutive\s+order\s+(?:no\.?\s*)?\d"  # a NUMBERED EO, not "an EO"
    r"|\bsupremacy\s+clause\b",
    re.IGNORECASE,
)

_STATE_NAMES: dict[str, str] = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut",
    "DE": "delaware", "FL": "florida", "GA": "georgia", "HI": "hawaii",
    "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa",
    "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine",
    "MD": "maryland", "MA": "massachusetts", "MI": "michigan",
    "MN": "minnesota", "MS": "mississippi", "MO": "missouri",
    "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico",
    "NY": "new york", "NC": "north carolina", "ND": "north dakota",
    "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont",
    "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia",
}


def _normalize_haystack(text: str) -> str:
    """Lowercase + fix the recurring mojibake ('Â§' for '§') + collapse
    whitespace, so anchor patterns and example-authority snippets match the
    messy strings the model actually emits."""
    text = text.lower().replace("â§", "§")
    return re.sub(r"\s+", " ", text)


def assess_preemption_credibility(payload: dict[str, Any]) -> dict[str, Any]:
    """Decide whether a preemption_signal payload asserts a conflict it can
    support.

    Returns ``{"credible": bool, "reason": str | None}``. ``reason`` is set
    only when not credible: ``self_negating_description`` or
    ``no_external_authority``.

    Rules (measured against the 81 signals of the 2026-07-13 run):
      1. A non-empty ``preemption_language`` (verbatim savings/preemption
         clause) makes the signal credible outright — savings clauses are the
         strongest signal class and their negative wording ("does not
         preempt") is the statute's, not the model's.
      2. Otherwise, a description concluding there is NO conflict kills the
         signal.
      3. Otherwise, conflict-asserting types (cross_state_conflict,
         federal_preemption, interstate_commerce, dormant_commerce_clause)
         must anchor to an external jurisdiction: a concrete federal citation
         or another state's name, found in related_authority / description /
         cross_law_refs — after discounting the prompt's own example
         authorities. Citations to the law's own state codes ("California
         Penal Code" on a CA law) anchor nothing.
      4. Non-conflict-asserting types (first_amendment, agency_jurisdiction,
         other) pass through — existing classification handles them.
    """
    if (payload.get("preemption_language") or "").strip():
        return {"credible": True, "reason": None}

    description = payload.get("description") or ""
    if _SELF_NEGATION_RE.search(description):
        return {"credible": False, "reason": "self_negating_description"}

    conflict_type = (payload.get("conflict_type") or "").strip().lower()
    if conflict_type not in CONFLICT_ASSERTING_TYPES:
        return {"credible": True, "reason": None}

    ref_bits: list[str] = []
    for ref in payload.get("cross_law_refs") or []:
        if isinstance(ref, dict):
            ref_bits.append(str(ref.get("law_name") or ""))
            ref_bits.append(str(ref.get("section") or ""))
    haystack = _normalize_haystack(
        " | ".join(
            [
                str(payload.get("related_authority") or ""),
                description,
                *ref_bits,
            ]
        )
    )
    for example in _PROMPT_EXAMPLE_AUTHORITIES:
        haystack = haystack.replace(example, " ")

    if _FEDERAL_ANCHOR_RE.search(haystack):
        return {"credible": True, "reason": None}

    # Another state's name anchors a cross-state claim — but only when we
    # know which state is "own": without that, the law's own state name in
    # the description ("California's amendment ... may conflict") would
    # anchor itself. Federal anchors above don't need this knowledge.
    own_name = _STATE_NAMES.get((payload.get("jurisdiction") or "").strip().upper())
    if own_name:
        for name in _STATE_NAMES.values():
            if name != own_name and re.search(rf"\b{name}\b", haystack):
                return {"credible": True, "reason": None}

    return {"credible": False, "reason": "no_external_authority"}


def classify_legal_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Classify a preemption_signal payload into a typed legal-context category.

    Args:
        payload: A PreemptionSignalPayload dict.

    Returns:
        ``{"legal_context_type": str, "display": bool, "raw_conflict_type": str,
        "credible": bool, "credibility_reason": str | None}``

        ``display`` is False for low-value rows (unclassified with no usable
        signal) AND for conflict-asserting rows that fail the QA-6
        credibility assessment, so the UI can hide them. Applying this at
        sync time repairs already-stored junk rows without re-extraction
        (same retroactive pattern as QA-3).
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

    credibility = assess_preemption_credibility(payload)
    display = (
        legal_context_type not in LOW_VALUE_TYPES and credibility["credible"]
    )
    return {
        "legal_context_type": legal_context_type,
        "display": display,
        "raw_conflict_type": raw or None,
        "credible": credibility["credible"],
        "credibility_reason": credibility["reason"],
    }


def is_low_value(payload: dict[str, Any]) -> bool:
    """True if this legal-context row should be hidden by default."""
    return not classify_legal_context(payload)["display"]
