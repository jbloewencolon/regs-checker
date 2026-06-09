"""B1.5 — Actor field sanitizer.

Filters INVALID_nonactor and garbled values out of normalized actor fields
(subject_normalized, actor_type, right_holder_normalized,
responsible_party_normalized) before they reach the DB.

Raw actor fields (subject, actor_name, right_holder, responsible_party) are
left untouched — they carry the LLM's verbatim output for provenance.
Normalized fields carry the canonical code; garbage in that field is set to
None so the normalization pass (B4 vocab_loader) can route it to
vocab_review_queue rather than silently storing a non-actor value.
"""

from __future__ import annotations

import re

# Terms extracted directly from the LLM that are not actor roles.
# Source: actor_unresolved_terms.csv, routing=excluded_non_actor.
INVALID_NONACTOR_TERMS: frozenset[str] = frozenset(
    {
        "contract",
        "document",
        "website",
        "program",
        "operat",
        "socia",
        "legal_claim",
        "procurement_process",
        "request",
        "report",
        "content",
        "legislative provision",
        "software_tool",
        "distribution_platform",
        "automated decision-making system",
    }
)

# Patterns that indicate a garbled value regardless of exact text.
_GARBLED_PATTERNS = [
    re.compile(r"\s{2,}"),        # embedded double-spaces (e.g. "deploy   ployer")
    re.compile(r"\t"),             # literal tab characters
    re.compile(r"^.{1,3}$"),       # suspiciously short (1-3 chars)
]


def sanitize_normalized_actor(value: str | None) -> str | None:
    """Return None if value is a known non-actor term or garbled string.

    Used as a Pydantic field_validator on *_normalized actor fields.
    The raw (un-normalized) counterpart is never touched.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    lower = stripped.lower()
    if lower in INVALID_NONACTOR_TERMS:
        return None
    for pat in _GARBLED_PATTERNS:
        if pat.search(stripped):
            return None
    return stripped


def is_invalid_actor(value: str | None) -> bool:
    """True when sanitize_normalized_actor would return None for a non-None value."""
    if value is None:
        return False
    return sanitize_normalized_actor(value) is None
