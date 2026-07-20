"""Human-readable label tables for Law Card rendering (LC-2b, design Rule 5).

Single source of truth for status/review-state humanization. Registered as
Jinja globals in src/api/app.py so templates never render a raw enum value,
and imported directly by LC-2c's exhaustiveness test (every TemporalStatus
value must have an entry in STATUS_LABELS — mirrors LC-1b's "every schema
field needs a catalog entry" pattern from field_catalog.py).
"""
from __future__ import annotations

from datetime import datetime

from src.db.models import TemporalStatus

STATUS_LABELS: dict[str, str] = {
    TemporalStatus.introduced.value: "Introduced",
    TemporalStatus.pending.value: "Pending",
    TemporalStatus.passed_one_chamber.value: "Passed One Chamber",
    TemporalStatus.enacted.value: "Enacted",
    TemporalStatus.active.value: "Active",
    TemporalStatus.future_effective.value: "Future Effective",
    TemporalStatus.repealed.value: "Repealed",
    TemporalStatus.stayed.value: "Stayed",
    TemporalStatus.vetoed.value: "Vetoed",
    TemporalStatus.dead.value: "Dead",
    TemporalStatus.withdrawn.value: "Withdrawn",
}

# Extraction.human_review_state / LawCardState.human_review_state use two
# different small vocabularies (per-extraction vs. law-level rollup) — kept
# in one table since callers pass whichever value they have.
REVIEW_STATE_LABELS: dict[str, str] = {
    "unedited": "Unedited",
    "edited": "Edited",
    "verified": "Verified",
    "none": "No review",
    "in_progress": "In progress",
    "complete": "Complete",
}

# Rule 2, condition 1 — enforcement only renders for enacted/in-force laws.
# The design-rule doc's source language says "not withdrawn/vetoed/enjoined";
# regs-checker's TemporalStatus has no "enjoined" value, so `stayed` (a law
# whose enforcement is paused by court order) is treated as its closest
# analog and suppressed too.
_ENFORCEMENT_SUPPRESSED_STATUSES = {
    TemporalStatus.withdrawn.value,
    TemporalStatus.vetoed.value,
    TemporalStatus.dead.value,
    TemporalStatus.stayed.value,
}


def humanize_status(status: str | None) -> str:
    if status is None:
        return "Status unknown"
    return STATUS_LABELS.get(status, status)


def humanize_review_state(state: str | None) -> str:
    if state is None:
        return REVIEW_STATE_LABELS["unedited"]
    return REVIEW_STATE_LABELS.get(state, state)


def is_enforcement_visible(status: str | None) -> bool:
    if status is None:
        return False
    return status not in _ENFORCEMENT_SUPPRESSED_STATUSES


def humanize_extracted_at(iso_string: str | None) -> str:
    """Absolute + relative "last extracted" display, mirroring
    src/api/routes/_dashboard_helpers.py's `_format_last_updated` (same
    "YYYY-MM-DD HH:MM UTC (Xh ago)" convention already used elsewhere in
    this dashboard) so a law card's dating reads consistently with the
    pipeline dashboard's own "last run" indicators. Extraction.created_at
    is a naive datetime written via server_default=func.now(), assumed UTC
    to match that helper's own assumption.
    """
    if not iso_string:
        return "Never extracted"
    try:
        dt = datetime.fromisoformat(iso_string)
    except ValueError:
        return iso_string
    now = datetime.utcnow()
    delta = now - dt.replace(tzinfo=None)
    seconds = delta.total_seconds()
    if seconds < 0:
        relative = "just now"
    elif seconds < 60:
        relative = f"{int(seconds)}s ago"
    elif seconds < 3600:
        relative = f"{int(seconds // 60)}m ago"
    elif seconds < 86400:
        relative = f"{int(seconds // 3600)}h ago"
    else:
        relative = f"{int(seconds // 86400)}d ago"
    return f'{dt.strftime("%Y-%m-%d %H:%M UTC")} ({relative})'
