"""PNE-2 — RC-canonical → Policy Navigator vocabulary crosswalks.

Policy Navigator (PN) asked RC to emit `actor_role` (its 7-value role vocabulary)
and `obligation_type` (its 13-value taxonomy). RC keeps its own richer, formally
ratified vocabularies canonical (13 actor codes, 22 obligation families — see
`docs/NORMALIZATION_VOCABULARY_RATIFICATION_PLAN.md`) and ships the PN value
*alongside* the RC code via the deterministic crosswalks in this module, mirroring
the existing Orrick/IAPP crosswalk pattern in `data/lookups/`.

Two design points settled with the operator (2026-07-06):
  - RC canon + crosswalk: emit BOTH codes, never remap RC's ratified vocabulary.
  - Alias-aware actor mapping: PN treats `employer` / `vendor` / `integrator` as
    first-class roles that RC deliberately folds away (employer→deployer,
    vendor→provider). When the *raw* extracted term is one of those, recover
    PN's finer value from `pn_actor_alias_overrides.csv` instead of flattening
    it through the RC code.

Everything here is deterministic (CSV lookups + the existing obligation-family
alias classifier) — no LLM, no new extraction. It runs at sync time in
`payload_adapter.py`, so it applies retroactively to every stored extraction.
"""

from __future__ import annotations

import csv
import pathlib
import re

from src.core.concept_grouping import _classify_obligation_family
from src.core.vocab_loader import normalize

_LOOKUPS_DIR = pathlib.Path(__file__).parent.parent.parent / "data" / "lookups"

# Module-level caches, loaded once at first access (same pattern as vocab_loader).
_actor_code_to_pn: dict[str, str] | None = None
_actor_alias_to_pn: dict[str, str] | None = None
_oblig_code_to_pn: dict[str, str] | None = None


def _load_two_col(filename: str, key_col: str, val_col: str) -> dict[str, str]:
    """Load a {key_col: val_col} map from a lookups CSV; blank val_col → skipped.

    A blank value column is meaningful in these crosswalks (e.g. `regulator` has
    no PN role), so it is intentionally left out of the map — callers treat a
    missing key as "no PN value", which is the same honest null.
    """
    path = _LOOKUPS_DIR / filename
    out: dict[str, str] = {}
    if not path.exists():
        return out
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            key = (row.get(key_col) or "").strip().lower()
            val = (row.get(val_col) or "").strip()
            if key and val:
                out[key] = val
    return out


def _actor_code_map() -> dict[str, str]:
    global _actor_code_to_pn
    if _actor_code_to_pn is None:
        _actor_code_to_pn = _load_two_col("pn_actor_crosswalk.csv", "rc_code", "pn_role")
    return _actor_code_to_pn


def _actor_alias_map() -> dict[str, str]:
    global _actor_alias_to_pn
    if _actor_alias_to_pn is None:
        _actor_alias_to_pn = _load_two_col(
            "pn_actor_alias_overrides.csv", "raw_term", "pn_role"
        )
    return _actor_alias_to_pn


def _oblig_code_map() -> dict[str, str]:
    global _oblig_code_to_pn
    if _oblig_code_to_pn is None:
        _oblig_code_to_pn = _load_two_col(
            "pn_obligation_type_crosswalk.csv", "rc_code", "pn_type"
        )
    return _oblig_code_to_pn


def reload_cache() -> None:
    """Force reload of all crosswalk caches from disk (useful in tests)."""
    global _actor_code_to_pn, _actor_alias_to_pn, _oblig_code_to_pn
    _actor_code_to_pn = None
    _actor_alias_to_pn = None
    _oblig_code_to_pn = None


def _match_actor_alias(text: str | None) -> str | None:
    """Return the PN role if any alias-override term appears as a word in text.

    Word-boundary + optional trailing 's', so "an employer" and "employers"
    both resolve to `employer` while "employment" does not.
    """
    if not text or not text.strip():
        return None
    low = text.strip().lower()
    for term, pn_role in _actor_alias_map().items():
        if re.search(rf"\b{re.escape(term)}s?\b", low):
            return pn_role
    return None


def derive_actor_role(
    subject: str | None,
    subject_normalized: str | None,
) -> tuple[str | None, str | None]:
    """Return (actor_role_rc, actor_role_pn) for an obligation's regulated party.

    - actor_role_rc: RC canonical actor code (13-value vocabulary) via the
      ratified alias table. `None` only when there's no subject at all.
    - actor_role_pn: PN's 7-value role. Alias-aware — a raw term PN treats as
      first-class (employer/vendor/integrator) wins over the flattened RC code.
      `None` when the RC code has no PN equivalent (e.g. `regulator`,
      `individual`) so an enforcer or a protected party never displays as a
      regulated actor.
    """
    raw = subject_normalized or subject
    if not raw or not raw.strip():
        return None, None

    # RC canonical code from the ratified actor alias table.
    actor_role_rc = normalize("actor", raw)

    # Alias-aware PN value: check the raw terms first so PN's finer roles
    # (employer/vendor/integrator) are recovered before the RC code flattens
    # them. Word-boundary match (allowing a trailing plural) so "an employer"
    # / "employers" still resolve, while avoiding substring false positives.
    for candidate in (subject, subject_normalized):
        hit = _match_actor_alias(candidate)
        if hit:
            return actor_role_rc, hit

    actor_role_pn = _actor_code_map().get(actor_role_rc)
    return actor_role_rc, actor_role_pn


def derive_obligation_type(action: str | None) -> tuple[str | None, str | None]:
    """Return (obligation_family_rc, obligation_type_pn) for an obligation.

    - obligation_family_rc: RC's 22-value family, via the same deterministic
      alias classifier concept-grouping uses (`_classify_obligation_family`),
      so the sync value matches what the concept layer derives.
    - obligation_type_pn: PN's 13-value taxonomy via crosswalk. `None` when the
      family is the unmatched catch-all (`obligation_general`) so PN can infer.
    """
    if not action or not action.strip():
        return None, None
    family = _classify_obligation_family(action)
    pn_type = _oblig_code_map().get(family)
    return family, pn_type


# --- PNE-2d: threshold → machine-comparable trigger predicate -----------------

# Operator phrasings → a precise operator token. Deliberately keeps gt/lt
# distinct from gte/lte: "more than 50 employees" is strictly > 50 (i.e. 51+),
# and collapsing it to gte would silently shift the boundary. PN can fold
# gt→gte / lt→lte on its side if its column enum is strict — but the precise
# operator plus the raw condition text (both emitted) keep the boundary
# verifiable rather than fabricated. Longest phrasings first so "no fewer than"
# beats "fewer than".
_OPERATOR_PATTERNS: list[tuple[str, str]] = [
    ("greater than or equal", "gte"),
    ("no fewer than", "gte"),
    ("no less than", "gte"),
    ("at least", "gte"),
    ("or more", "gte"),
    ("or greater", "gte"),
    ("minimum of", "gte"),
    ("less than or equal", "lte"),
    ("no more than", "lte"),
    ("at most", "lte"),
    ("or fewer", "lte"),
    ("or less", "lte"),
    ("up to", "lte"),
    ("maximum of", "lte"),
    ("more than", "gt"),
    ("greater than", "gt"),
    ("exceeds", "gt"),
    ("exceeding", "gt"),
    ("over", "gt"),
    ("above", "gt"),
    ("fewer than", "lt"),
    ("less than", "lt"),
    ("under", "lt"),
    ("below", "lt"),
    ("equal to", "eq"),
    ("exactly", "eq"),
]

# threshold_type / unit / condition keyword → PN trigger_type. First match wins.
_TRIGGER_TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("employee", "employee_count"),
    ("worker", "employee_count"),
    ("staff", "employee_count"),
    ("revenue", "revenue"),
    ("gross receipt", "revenue"),
    ("annual income", "revenue"),
    ("turnover", "revenue"),
    ("consumer", "consumer_count"),
    ("resident", "consumer_count"),
    ("data subject", "consumer_count"),
    ("household", "consumer_count"),
    ("record", "consumer_count"),
    ("flop", "compute"),
    ("compute", "compute"),
    ("sector", "sector"),
    ("use case", "ai_use_case"),
    ("use_case", "ai_use_case"),
]

_MULTIPLIERS: list[tuple[str, float]] = [
    ("billion", 1_000_000_000.0),
    ("million", 1_000_000.0),
    ("thousand", 1_000.0),
]


def _parse_trigger_value(raw: str | None) -> float | str | None:
    """Best-effort numeric parse of a threshold value.

    Handles "$25 million", "25,000,000", "50", "10^26". Returns a float when
    parseable, else the original string (never a wrong number), else None.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    low = text.lower()
    multiplier = 1.0
    for word, factor in _MULTIPLIERS:
        if word in low:
            multiplier = factor
            low = low.replace(word, " ")
            break
    # Scientific / caret notation: 10^26, 1e26
    caret = re.search(r"(\d+(?:\.\d+)?)\s*\^\s*(\d+)", low)
    if caret:
        return float(caret.group(1)) ** float(caret.group(2))
    cleaned = low.replace("$", "").replace(",", "").replace("%", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", cleaned)
    if m:
        try:
            return float(m.group(0)) * multiplier
        except ValueError:
            return text
    return text


def _infer_trigger_type(payload: dict) -> str | None:
    if payload.get("compute_flops") is not None:
        return "compute"
    if payload.get("sector_applicability"):
        return "sector"
    haystack = " ".join(
        str(payload.get(k) or "")
        for k in ("threshold_type", "threshold_unit", "threshold_condition")
    ).lower()
    for keyword, ttype in _TRIGGER_TYPE_KEYWORDS:
        if keyword in haystack:
            return ttype
    # Fall back to the raw threshold_type so the field is never silently empty.
    raw_type = payload.get("threshold_type")
    return raw_type.strip() if isinstance(raw_type, str) and raw_type.strip() else None


def _infer_operator(condition: str | None, has_value: bool) -> str:
    if condition:
        low = condition.lower()
        for phrase, op in _OPERATOR_PATTERNS:
            if phrase in low:
                return op
    # Applicability thresholds are conventionally "≥ X triggers the obligation".
    # Default to gte only when there IS a value to compare; otherwise "any".
    return "gte" if has_value else "any"


def derive_trigger(payload: dict) -> dict | None:
    """PNE-2d (PN Ask 4b): a machine-comparable trigger predicate.

    Turns RC's threshold fields into `{trigger_type, trigger_operator,
    trigger_value}` plus the original phrasing (`trigger_condition_raw`) so the
    boundary stays verifiable. Returns None when there's no threshold signal at
    all (nothing to compare on).

    Note (Ask 4a): the stable obligation id PN wants for `applies_to_obligation`
    already ships as `system_a_extraction_id` on every synced row — RC does not
    invent a new id here. Linking a threshold row to a specific obligation row
    (`applies_to_obligation_id`) is a separate design question (PNE-4b), not a
    field this deterministic parser can honestly fill.
    """
    value = _parse_trigger_value(payload.get("threshold_value"))
    if payload.get("compute_flops") is not None and value is None:
        value = payload.get("compute_flops")
    trigger_type = _infer_trigger_type(payload)
    if trigger_type is None and value is None:
        return None
    return {
        "trigger_type": trigger_type,
        "trigger_operator": _infer_operator(
            payload.get("threshold_condition"), value is not None
        ),
        "trigger_value": value,
        "trigger_condition_raw": payload.get("threshold_condition"),
    }
