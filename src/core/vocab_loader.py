"""B4 — Vocabulary lookup loader.

Reads ratified vocabulary artifacts from data/lookups/ and exposes normalization
helpers consumed by rollup_matrix.py and the unified normalization passes.

Design:
  - Each dimension has a canonical_codes.csv and a crosswalk.csv.
  - normalize(dim, raw_value) returns a canonical code or the fallback for that
    dimension.  Unrecognized values are returned as the fallback AND recorded
    in an in-process queue so callers can flush them to vocab_review_queue.
  - Lookups are loaded once at first access (module-level cache).

Supported dimensions (corresponding to data/lookups/{dim}_*.csv):
  actor, law_domain, covered_systems, obligation_family, rights,
  enforcement, legal_context
"""

from __future__ import annotations

import csv
import pathlib
from typing import NamedTuple

_LOOKUPS_DIR = pathlib.Path(__file__).parent.parent.parent / "data" / "lookups"

# Fallback canonical codes used when a raw value has no mapping.
_FALLBACKS: dict[str, str] = {
    "actor":            "regulated_entity",
    "law_domain":       "sector_specific",
    "covered_systems":  "all_ai_systems",
    "obligation_family": "REVIEW_unclassified",
    "rights":           "REVIEW_unclassified",
    "enforcement":      "REVIEW_unclassified",
    "legal_context":    "unclassified",
}

# In-process queue for unrecognized terms — flushed to vocab_review_queue table
# by the caller (rollup_matrix, normalization pass, etc.)
class UnrecognizedTerm(NamedTuple):
    dimension: str
    raw_term: str
    provisional_code: str


_unrecognized_queue: list[UnrecognizedTerm] = []

# Loaded lookup tables: {dimension: {raw_lower: canonical_code}}
_lookup_cache: dict[str, dict[str, str]] = {}


def _load_dimension(dim: str) -> dict[str, str]:
    """Load the alias → canonical code map for a dimension from disk."""
    aliases_path = _LOOKUPS_DIR / f"{dim}_aliases.csv"
    crosswalk_path = _LOOKUPS_DIR / f"{dim}_crosswalk.csv"

    lookup: dict[str, str] = {}

    # Primary: aliases file (raw_term → proposed_code)
    if aliases_path.exists():
        with open(aliases_path, newline="") as f:
            for row in csv.DictReader(f):
                raw = (row.get("raw_term") or "").strip().lower()
                code = (row.get("proposed_code") or "").strip()
                if raw and code and not code.startswith("REVIEW") and not code.startswith("PENDING"):
                    lookup[raw] = code

    # Supplementary: crosswalk raw values column (canonical first-look override)
    if crosswalk_path.exists():
        with open(crosswalk_path, newline="") as f:
            for row in csv.DictReader(f):
                code = (row.get("canonical_code") or "").strip()
                # The crosswalk may carry a semicolon-separated list of raw values
                for raw_col in ("raw_extraction_values_top6", "raw_term"):
                    raw_val = row.get(raw_col) or ""
                    for raw in raw_val.split(";"):
                        raw = raw.strip().lower()
                        if raw and code:
                            lookup[raw] = code

    return lookup


def _get_lookup(dim: str) -> dict[str, str]:
    if dim not in _lookup_cache:
        _lookup_cache[dim] = _load_dimension(dim)
    return _lookup_cache[dim]


def normalize(dim: str, raw_value: str | None) -> str:
    """Return the canonical code for raw_value in dimension dim.

    If the value is unrecognized, adds it to the unrecognized queue and
    returns the fallback code for the dimension.
    """
    if not raw_value:
        return _FALLBACKS.get(dim, "unclassified")

    lookup = _get_lookup(dim)
    key = raw_value.strip().lower()
    code = lookup.get(key)
    if code:
        return code

    fallback = _FALLBACKS.get(dim, "unclassified")
    _unrecognized_queue.append(UnrecognizedTerm(dim, raw_value, fallback))
    return fallback


def flush_unrecognized() -> list[UnrecognizedTerm]:
    """Return and clear the in-process unrecognized-term queue."""
    items = list(_unrecognized_queue)
    _unrecognized_queue.clear()
    return items


def get_canonical_codes(dim: str) -> list[str]:
    """Return the list of known canonical codes for a dimension."""
    codes_path = _LOOKUPS_DIR / f"{dim}_canonical_codes.csv"
    if not codes_path.exists():
        return []
    with open(codes_path, newline="") as f:
        return [row["code"] for row in csv.DictReader(f) if row.get("code")]


def reload_cache() -> None:
    """Force reload of all dimension caches from disk (useful in tests)."""
    _lookup_cache.clear()
    _unrecognized_queue.clear()
