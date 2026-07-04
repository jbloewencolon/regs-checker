"""Deterministic numeric-field grounding (EA2-1).

The typed numeric fields on extraction payloads — penalty amounts, cure
periods, retention windows, incident-reporting deadlines, size thresholds,
compute thresholds — are LLM-derived integers/floats with unit conversion
baked in (e.g. "3 years" -> 36 months, "$10,000" -> 10000). Evidence-span
verification (``text_grounding.py``) only confirms that a quoted STRING
appears verbatim in the passage; nothing previously checked that the NUMBER
attached to a field actually matches what the quoted evidence says. A model
could quote a real sentence and still attach a fabricated or miscalculated
number to it, and evidence-grounding would score it as fully verified.

This module is a rule-based cross-check, not a replacement for span
verification: it looks at the payload's already-verified evidence spans,
extracts candidate numbers under the field's expected unit, and reports
whether the payload's value is corroborated by, contradicted by, or simply
absent from that text. It never raises — a field with no evidence spans, or
spans with no parseable numbers, is reported as ``unverifiable`` (no
signal), not a mismatch, since the goal is to catch clear numeric
contradictions, not to penalize passages where our regex can't parse a
spelled-out number ("thirty days").

Field-name attribution in evidence_spans is unreliable across nested
payload shapes (e.g. enforcement.max_civil_penalty_usd vs. a bare
"max_civil_penalty_usd" field_name), so this checks the field's value
against the union of ALL verified evidence span text on the payload, scoped
by unit family — not by field_name string matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Field -> unit-family registry
# ---------------------------------------------------------------------------

# unit families: "usd", "days", "months", "hours", "count", "flops"
NUMERIC_FIELD_UNITS: dict[str, str] = {
    "max_civil_penalty_usd": "usd",
    "cure_period_days": "days",
    "retention_period_months": "months",
    "incident_reporting_hours": "hours",
    "employee_threshold": "count",
    "revenue_threshold_usd": "usd",
    "consumer_data_threshold": "count",
    "compute_flops": "flops",
    "assessment_frequency_months": "months",
}

# Nested-payload fields live under a sub-object on the top-level payload
# (e.g. ObligationPayload.enforcement.max_civil_penalty_usd). Maps
# field_name -> parent key so callers can pass the raw payload dict without
# pre-flattening it.
NESTED_FIELD_PARENT: dict[str, str] = {
    "max_civil_penalty_usd": "enforcement",
    "cure_period_days": "enforcement",
}

_RELATIVE_TOLERANCE = 0.01  # floats (compute_flops) — 1% relative tolerance


@dataclass
class NumericGroundingResult:
    """Grounding outcome for one numeric field on one extraction payload."""

    field_name: str
    payload_value: float
    status: str  # "grounded" | "mismatch" | "unverifiable"
    candidates_found: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Number/unit extraction from evidence text
# ---------------------------------------------------------------------------

_USD_PATTERN = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)|(?<![\d.])([\d,]{4,}(?:\.\d+)?)\s*dollars",
    re.IGNORECASE,
)
_DAYS_PATTERN = re.compile(r"(\d+)\s*(?:calendar\s+|business\s+)?days?\b", re.IGNORECASE)
_HOURS_PATTERN = re.compile(r"(\d+)[\s-]*hours?\b", re.IGNORECASE)
_MONTHS_PATTERN = re.compile(r"(\d+)\s*months?\b", re.IGNORECASE)
_YEARS_PATTERN = re.compile(r"(\d+)\s*years?\b", re.IGNORECASE)
# Excludes matches starting right after a digit, period, $, OR comma — the
# comma exclusion matters because without it, "$25,000,000" would still
# yield a spurious count match starting just after a comma (e.g. "000,000"
# -> 0), since a single-character lookbehind can't otherwise tell that the
# comma is part of a dollar-prefixed number.
_COUNT_PATTERN = re.compile(r"(?<![\d.,$])([\d,]{1,12})(?!\d)")
# Compute/FLOPS: "10^26" (base^exp), "1e26"/"1E26" (base * 10^exp), or
# "10 x 10^26" / "10x10^26" (base * 10^exp). The caret and scientific-notation
# forms compute differently, so the operator is captured and dispatched
# explicitly rather than assumed — "10^26" is 1e26, not 10 * 1e26.
_FLOPS_CARET_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*\^\s*(\d+)")
_FLOPS_SCI_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*[eE]\s*(\d+)")
_FLOPS_MULT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*x\s*10\s*\^?\s*(\d+)", re.IGNORECASE)
# Note: on "10 x 10^26" the caret pattern also matches the embedded "10^26",
# adding a spurious 1e26 candidate alongside the correct 1e27 reading. This
# is accepted over-inclusion, not fixed with non-overlapping-match tracking:
# an extra candidate can only produce a false "grounded" (safe direction),
# never a false "mismatch" — see module docstring on the mismatch/grounded
# asymmetry.


def _parse_number(raw: str) -> float | None:
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_candidates(text: str, unit: str) -> list[float]:
    """Extract candidate numeric values of the given unit family from text.

    Returns normalized values in the field's base unit (e.g. years -> months
    for the "months" family). Best-effort: unparseable/spelled-out numbers
    ("thirty days") are simply not returned as candidates — that's a
    coverage gap, not a false mismatch, so callers must not treat an empty
    list as a contradiction.
    """
    if not text:
        return []

    candidates: list[float] = []

    if unit == "usd":
        for m in _USD_PATTERN.finditer(text):
            raw = m.group(1) or m.group(2)
            val = _parse_number(raw)
            if val is not None:
                candidates.append(val)

    elif unit == "days":
        raw_vals = (_parse_number(m.group(1)) for m in _DAYS_PATTERN.finditer(text))
        candidates.extend(v for v in raw_vals if v is not None)

    elif unit == "hours":
        raw_vals = (_parse_number(m.group(1)) for m in _HOURS_PATTERN.finditer(text))
        candidates.extend(v for v in raw_vals if v is not None)

    elif unit == "months":
        raw_vals = (_parse_number(m.group(1)) for m in _MONTHS_PATTERN.finditer(text))
        candidates.extend(v for v in raw_vals if v is not None)
        # Years convert to months so "3 years" corroborates retention_period_months=36.
        raw_years = (_parse_number(m.group(1)) for m in _YEARS_PATTERN.finditer(text))
        candidates.extend(v * 12 for v in raw_years if v is not None)

    elif unit == "count":
        for m in _COUNT_PATTERN.finditer(text):
            val = _parse_number(m.group(1))
            if val is not None:
                candidates.append(val)

    elif unit == "flops":
        for m in _FLOPS_CARET_PATTERN.finditer(text):
            base = _parse_number(m.group(1))
            exp = _parse_number(m.group(2))
            if base is not None and exp is not None:
                candidates.append(base ** exp)
        for m in _FLOPS_MULT_PATTERN.finditer(text):
            base = _parse_number(m.group(1))
            exp = _parse_number(m.group(2))
            if base is not None and exp is not None:
                candidates.append(base * (10 ** exp))
        for m in _FLOPS_SCI_PATTERN.finditer(text):
            base = _parse_number(m.group(1))
            exp = _parse_number(m.group(2))
            if base is not None and exp is not None:
                candidates.append(base * (10 ** exp))
        # Also accept a plain large integer as a literal FLOPS value.
        for m in _COUNT_PATTERN.finditer(text):
            val = _parse_number(m.group(1))
            if val is not None and val >= 1e9:
                candidates.append(val)

    return candidates


def _values_match(payload_value: float, candidate: float, unit: str) -> bool:
    if unit == "flops":
        if candidate == 0:
            return payload_value == 0
        return abs(payload_value - candidate) / candidate <= _RELATIVE_TOLERANCE
    return abs(payload_value - candidate) < 1e-6


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_numeric_grounding(
    payload: dict,
    evidence_spans: list[dict],
) -> dict[str, NumericGroundingResult]:
    """Cross-check every populated typed-numeric field against evidence text.

    Args:
        payload: The extraction payload dict (already schema-validated).
        evidence_spans: The payload's evidence_spans list (verified or not —
            only entries with ``verified: True`` are used as grounding
            evidence; unverified spans carry no weight here).

    Returns:
        Dict of field_name -> NumericGroundingResult, one entry per numeric
        field that had a non-null value in the payload. Fields absent or
        null in the payload are omitted (nothing to check).
    """
    verified_texts = [
        s.get("text", "") for s in evidence_spans
        if isinstance(s, dict) and s.get("verified") and s.get("text")
    ]
    combined_text = "\n".join(verified_texts)

    results: dict[str, NumericGroundingResult] = {}

    for field_name, unit in NUMERIC_FIELD_UNITS.items():
        parent_key = NESTED_FIELD_PARENT.get(field_name)
        if parent_key:
            parent = payload.get(parent_key) or {}
            raw_value = parent.get(field_name) if isinstance(parent, dict) else None
        else:
            raw_value = payload.get(field_name)

        if raw_value is None:
            continue
        try:
            payload_value = float(raw_value)
        except (TypeError, ValueError):
            continue

        candidates = extract_candidates(combined_text, unit)

        if not candidates:
            status = "unverifiable"
        elif any(_values_match(payload_value, c, unit) for c in candidates):
            status = "grounded"
        else:
            status = "mismatch"

        results[field_name] = NumericGroundingResult(
            field_name=field_name,
            payload_value=payload_value,
            status=status,
            candidates_found=candidates,
        )

    return results


def has_numeric_mismatch(results: dict[str, NumericGroundingResult]) -> bool:
    """True if any checked field came back contradicted by its evidence."""
    return any(r.status == "mismatch" for r in results.values())
