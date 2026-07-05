"""IAPP alignment — three-state comparison against the IAPP AI law tracker.

Loads data/trackers/iapp_law_tracker.csv and provides per-extraction alignment checks.

IAPP Scope codes:
  G  — general (any actor using AI; broad obligation net)
  G* — general-broad (government + general AI use)
  F  — frontier/developer (foundation model developers, providers)
  D  — deployer (deployers, operators of AI systems)
  A  — ADMT (automated decision-making systems; any actor)
  A* — ADMT-broad (ADMT + broader AI system use)

Three-state alignment result:
  "aligned"        — IAPP entry present; extraction actor in law's actor scope
  "scope_mismatch" — IAPP entry present; extraction actor not in scope (informational)
  "tracker_silent" — no IAPP entry for this law (not penalized)
"""

from __future__ import annotations

import csv
import pathlib
from dataclasses import dataclass
from functools import lru_cache

_IAPP_CSV = (
    pathlib.Path(__file__).parent.parent.parent / "data" / "trackers" / "iapp_law_tracker.csv"
)
_DIM_JURISDICTIONS = (
    pathlib.Path(__file__).parent.parent.parent / "data" / "dim_jurisdictions.csv"
)

# IAPP Scope code → set of canonical actor codes that this scope targets.
IAPP_SCOPE_TO_ACTORS: dict[str, set[str]] = {
    "G": {
        "developer", "provider", "deployer", "operator", "distributor",
        "compute_provider", "controller", "processor", "data_broker",
        "regulator", "government_agency", "individual", "regulated_entity",
    },
    "G*": {
        "developer", "provider", "deployer", "operator", "distributor",
        "compute_provider", "controller", "processor", "data_broker",
        "regulator", "government_agency", "individual", "regulated_entity",
    },
    "F": {"developer", "provider", "compute_provider"},
    "D": {"deployer", "operator", "controller", "data_broker"},
    "A": {
        "developer", "provider", "deployer", "operator",
        "controller", "processor", "regulated_entity",
    },
    "A*": {
        "developer", "provider", "deployer", "operator",
        "controller", "processor", "data_broker", "regulated_entity",
    },
}

# Obligation columns in the IAPP CSV (non-empty value means requirement exists)
IAPP_OBLIGATION_COLUMNS = [
    "Program and documentation",
    "Assessments",
    "Training",
    "Responsible individual",
    "General notice",
    "Labeling/notification",
    "Explanation/incident reporting",
    "Developer documentation",
    "Registration",
    "Third-party review",
    "Opt out/appeal",
    "Nondiscrimination",
]


@dataclass
class IAPPEntry:
    """Parsed row from iapp_law_tracker.csv."""

    section: str            # "LAWS SIGNED" / "ACTIVE BILLS" / "INACTIVE BILLS"
    jurisdiction: str       # State name, e.g. "California"
    bill_number: str        # e.g. "SB 205"
    scope_raw: str          # Raw scope string, e.g. "F,D" or "G"
    scope_codes: list[str]  # Parsed individual codes
    obligations: dict[str, str]  # column → raw cell value (may be empty)

    @property
    def is_enacted(self) -> bool:
        return self.section == "LAWS SIGNED"

    @property
    def has_data(self) -> bool:
        return bool(self.scope_raw)

    @property
    def actor_set(self) -> set[str]:
        """Union of canonical actors from all scope codes for this law."""
        result: set[str] = set()
        for code in self.scope_codes:
            result |= IAPP_SCOPE_TO_ACTORS.get(code, set())
        return result

    @property
    def obligation_types(self) -> list[str]:
        """Obligation column names that have a non-empty value."""
        return [col for col, val in self.obligations.items() if val.strip()]


def _parse_scope_codes(raw: str) -> list[str]:
    """Parse composite scope strings like 'F,D,G' into individual codes."""
    return [s.strip() for s in raw.split(",") if s.strip()]


@lru_cache(maxsize=1)
def _load_jurisdiction_abbrevs() -> dict[str, str]:
    """Return {full_name_lower: abbrev_lower} from dim_jurisdictions.csv."""
    mapping: dict[str, str] = {}
    if not _DIM_JURISDICTIONS.exists():
        return mapping
    with open(_DIM_JURISDICTIONS, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row.get("name", "").strip().lower()
            abbrev = row.get("state_abbrev", "").strip().lower()
            if name and abbrev:
                mapping[name] = abbrev
    return mapping


@lru_cache(maxsize=1)
def _load_iapp_index() -> dict[tuple[str, str], IAPPEntry]:
    """Load and index the IAPP CSV by (jurisdiction_key, bill_number_lower).

    Each entry is stored under TWO keys:
      (full_name_lower, bill_lower)  — e.g. ("california", "sb 205")
      (abbrev_lower,   bill_lower)   — e.g. ("ca", "sb 205")

    This lets callers use either the IAPP full name or the DB jurisdiction_code.
    Cached after first load.  Call reload_iapp_index() to invalidate.
    """
    index: dict[tuple[str, str], IAPPEntry] = {}
    if not _IAPP_CSV.exists():
        return index

    abbrev_map = _load_jurisdiction_abbrevs()

    with open(_IAPP_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            jurisdiction = row.get("Jurisdiction", "").strip()
            bill_number = row.get("Statute/bill", "").strip()
            scope_raw = row.get("Scope", "").strip()
            if not jurisdiction or not bill_number:
                continue

            entry = IAPPEntry(
                section=row.get("Section", "").strip(),
                jurisdiction=jurisdiction,
                bill_number=bill_number,
                scope_raw=scope_raw,
                scope_codes=_parse_scope_codes(scope_raw),
                obligations={col: row.get(col, "") for col in IAPP_OBLIGATION_COLUMNS},
            )
            bill_lower = bill_number.lower()
            jur_lower = jurisdiction.lower()
            index[(jur_lower, bill_lower)] = entry

            abbrev = abbrev_map.get(jur_lower)
            if abbrev:
                index[(abbrev, bill_lower)] = entry

    return index


def reload_iapp_index() -> None:
    """Invalidate the cached IAPP index (useful for testing)."""
    _load_iapp_index.cache_clear()
    _load_jurisdiction_abbrevs.cache_clear()


def get_iapp_entry(jurisdiction: str, bill_number: str) -> IAPPEntry | None:
    """Look up an IAPP entry by jurisdiction and bill number.

    Matching is case-insensitive.  Returns None if no entry found.
    """
    index = _load_iapp_index()
    key = (jurisdiction.lower().strip(), bill_number.lower().strip())
    return index.get(key)


def check_iapp_alignment(
    subject_normalized: str | None,
    iapp_entry: IAPPEntry | None,
) -> str:
    """Determine three-state IAPP alignment for an extraction's actor.

    Returns one of:
      "aligned"        — IAPP entry present; subject_normalized in scope actors
      "scope_mismatch" — IAPP entry present; subject_normalized not in scope actors
      "tracker_silent" — no IAPP entry for this law (do not penalize)
    """
    if iapp_entry is None or not iapp_entry.has_data:
        return "tracker_silent"

    if not subject_normalized:
        return "tracker_silent"

    actor_set = iapp_entry.actor_set
    if not actor_set:
        return "tracker_silent"

    if subject_normalized in actor_set:
        return "aligned"
    return "scope_mismatch"


def get_iapp_entry_for_context(context: dict) -> IAPPEntry | None:
    """Convenience wrapper that extracts jurisdiction/bill from a build context dict.

    The context dict is produced by _build_context() in extractor.py and contains:
      context["jurisdiction"]      — state abbreviation (e.g. "CA")
      context["jurisdiction_name"] — full state name (e.g. "California")
      context["short_cite"]        — bill citation (e.g. "SB 205")
      context["bill_id"]           — metadata bill ID (fallback)

    Tries short_cite first, then bill_id as fallback for the bill lookup.
    Tries jurisdiction_name first (matches IAPP CSV directly), then abbreviation.
    """
    # Determine bill number to try
    bill_number = (
        (context.get("short_cite") or context.get("bill_id") or "").strip()
    )
    if not bill_number:
        return None

    # Try full jurisdiction name first (direct IAPP CSV match), then abbreviation
    for jur_key in [
        context.get("jurisdiction_name") or "",
        context.get("jurisdiction") or "",
    ]:
        jur_key = jur_key.strip()
        if jur_key:
            entry = get_iapp_entry(jur_key, bill_number)
            if entry is not None:
                return entry

    return None
