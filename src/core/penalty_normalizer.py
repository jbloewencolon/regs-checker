"""Normalize free-text penalty_type values to the controlled enforcement vocabulary.

The ratified canonical codes live in data/lookups/enforcement_canonical_codes.csv.
The alias mapping (120+ raw penalty_type strings → codes) is in enforcement_aliases.csv.

Usage::

    from src.core.penalty_normalizer import normalize_penalty_type, PENALTY_LABELS

    code = normalize_penalty_type("civil damages")    # → "private_right_of_action"
    label = PENALTY_LABELS.get(code, code)            # → "Private right of action"

The normalizer is a filtered subset of vocab_loader's "enforcement" dimension —
it only loads rows where source == "penalty_type", so enforcing_body values
(e.g. "attorney general") don't pollute penalty_type lookups.
"""

from __future__ import annotations

import csv
import pathlib

_LOOKUPS_DIR = pathlib.Path(__file__).parent.parent.parent / "data" / "lookups"

# Human-readable labels for canonical penalty codes
PENALTY_LABELS: dict[str, str] = {
    "civil_penalty":          "Civil penalty",
    "criminal_penalty":       "Criminal penalty",
    "private_right_of_action": "Private right of action",
    "ag_enforcement":         "AG enforcement",
    "regulatory_enforcement": "Regulatory enforcement",
    "administrative_action":  "Administrative action",
    "injunctive_relief":      "Injunctive relief",
    "restitution":            "Restitution / disgorgement",
}

# Which codes represent enforcement actions rather than penalty types
# (used to classify EnforcementInfo.enforcing_body separately)
_ENFORCING_BODY_CODES = {"ag_enforcement", "regulatory_enforcement", "injunctive_relief"}

_penalty_cache: dict[str, str] | None = None
_body_cache: dict[str, str] | None = None


def _load_penalty_lookup() -> dict[str, str]:
    """Load penalty_type alias → canonical code (filtered to source==penalty_type)."""
    global _penalty_cache
    if _penalty_cache is not None:
        return _penalty_cache

    lookup: dict[str, str] = {}
    path = _LOOKUPS_DIR / "enforcement_aliases.csv"
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("source", "") not in ("penalty_type", "extraction_or_orrick"):
                    continue
                raw = (row.get("raw_term") or "").strip().lower()
                code = (row.get("proposed_code") or "").strip()
                if raw and code and not code.startswith("REVIEW") and not code.startswith("PENDING"):
                    lookup[raw] = code
    _penalty_cache = lookup
    return lookup


def _load_body_lookup() -> dict[str, str]:
    """Load enforcing_body alias → canonical code (filtered to source==enforcing_body)."""
    global _body_cache
    if _body_cache is not None:
        return _body_cache

    lookup: dict[str, str] = {}
    path = _LOOKUPS_DIR / "enforcement_aliases.csv"
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("source", "") != "enforcing_body":
                    continue
                raw = (row.get("raw_term") or "").strip().lower()
                code = (row.get("proposed_code") or "").strip()
                if raw and code and not code.startswith("REVIEW") and not code.startswith("PENDING"):
                    lookup[raw] = code
    _body_cache = lookup
    return lookup


def normalize_penalty_type(raw: str | None) -> str | None:
    """Map a free-text penalty_type string to a canonical code.

    Returns the canonical code if a mapping exists, the original string if not,
    or None when the input is blank.
    """
    if not raw:
        return None
    lookup = _load_penalty_lookup()
    key = raw.strip().lower()
    return lookup.get(key, raw)


def normalize_enforcing_body(raw: str | None) -> str | None:
    """Map a free-text enforcing_body string to a canonical code."""
    if not raw:
        return None
    lookup = _load_body_lookup()
    key = raw.strip().lower()
    return lookup.get(key, raw)


def reload_caches() -> None:
    """Force reload of all caches (useful in tests)."""
    global _penalty_cache, _body_cache
    _penalty_cache = None
    _body_cache = None
