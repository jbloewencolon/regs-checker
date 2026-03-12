"""Sync exclusion list — law_ids and document families excluded from sync.

These are intentionally excluded from the Policy Navigator's synced_extractions
table and must not be re-inserted. Each entry documents the reason and the
conditions under which it can be re-enabled.

Source: US Policy Navigator Sync Team Onboarding Guide, Section 8.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class SyncExclusion:
    """A single sync exclusion entry."""

    law_id: int
    reason: str
    resolution: str
    can_auto_reenable: bool = False


# ---------------------------------------------------------------------------
# Explicit exclusions per Policy Navigator Onboarding Guide Section 8
# ---------------------------------------------------------------------------

EXCLUDED_LAW_IDS: dict[int, SyncExclusion] = {
    21: SyncExclusion(
        law_id=21,
        reason=(
            "CA CCPA Regulations: Disputed mapping in Regs Checker — document is "
            "mapped to AB1008 but should be CPPA ADMT regulations. Excluded in "
            "extraction_snapshot.mjs."
        ),
        resolution="Correct upstream document mapping, then remove from exclusion list.",
    ),
    188: SyncExclusion(
        law_id=188,
        reason=(
            "SC (unknown): Pipeline fetched SC Real Estate Licensing Law (§ 40-57) "
            "instead of a 2024 AI law. 1,435 extractions cleared from Policy Navigator."
        ),
        resolution="Identify and provide correct SC AI law source document URL.",
    ),
    159: SyncExclusion(
        law_id=159,
        reason=(
            "NY (unknown): Pipeline fetched CT transportation pricing statute instead "
            "of NY Algorithmic Pricing Law. 1,046 extractions cleared."
        ),
        resolution="Identify correct NY Algorithmic Pricing Law source URL.",
    ),
    60: SyncExclusion(
        law_id=60,
        reason=(
            "CT (unknown): Full text is CT transportation network pricing statute "
            "(§ 13b-116). May be legitimate (algorithmic pricing) or a misfetch. "
            "Pending legal verification."
        ),
        resolution="Legal team to verify whether § 13b-116 is the intended document.",
    ),
}


def is_excluded(law_id: int) -> bool:
    """Check if a law_id is on the sync exclusion list."""
    return law_id in EXCLUDED_LAW_IDS


def get_exclusion_reason(law_id: int) -> str | None:
    """Return the exclusion reason for a law_id, or None if not excluded."""
    exclusion = EXCLUDED_LAW_IDS.get(law_id)
    return exclusion.reason if exclusion else None


def filter_excluded_rows(
    rows: list[dict[str, Any]],
    law_id_key: str = "law_id",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition rows into included and excluded based on the exclusion list.

    Args:
        rows: List of row dicts to filter.
        law_id_key: Key in the row dict that contains the law_id.

    Returns:
        Tuple of (included_rows, excluded_rows).
    """
    included = []
    excluded = []

    for row in rows:
        lid = row.get(law_id_key)
        if lid is not None and is_excluded(lid):
            excluded.append(row)
            logger.debug(
                "sync_row_excluded",
                law_id=lid,
                reason=get_exclusion_reason(lid),
            )
        else:
            included.append(row)

    if excluded:
        logger.info(
            "sync_exclusions_applied",
            excluded_count=len(excluded),
            excluded_law_ids=sorted({r.get(law_id_key) for r in excluded}),
        )

    return included, excluded
