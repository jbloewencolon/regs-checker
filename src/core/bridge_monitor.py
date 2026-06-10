"""Bridge gap monitor — detects new document families without bridge rows.

When the Regs Checker pipeline processes a new law that does not yet exist in
the Policy Navigator's law_document_bridge table, those extractions cannot be
linked to a fact_laws row and will be skipped during sync.

This module provides:
  1. Detection of unbridged document families (families with extractions but no bridge row)
  2. A notification mechanism to alert the Policy Navigator team
  3. A report generator for inclusion in sync health checks

Per the Sync Team Onboarding Guide Q7: "What is the notification or handoff
process when new families are added?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class UnbridgedFamily:
    """A document family that has extractions but no bridge row."""

    family_id: int
    jurisdiction_code: str
    canonical_title: str
    short_cite: str | None
    extraction_count: int
    first_extraction_at: datetime | None = None


@dataclass
class BridgeGapReport:
    """Report of document families needing bridge rows."""

    total_families: int = 0
    bridged_families: int = 0
    unbridged_families: int = 0
    unbridged: list[UnbridgedFamily] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def has_gaps(self) -> bool:
        return self.unbridged_families > 0


def detect_unbridged_families(
    source_session,
    target_session,
) -> BridgeGapReport:
    """Detect document families with extractions but no bridge row.

    Queries the source (Regs Checker) for all document families that have
    extractions, then checks the target (Policy Navigator) bridge table
    for coverage.

    Args:
        source_session: SQLAlchemy session for Regs Checker DB.
        target_session: SQLAlchemy session for Policy Navigator DB.

    Returns:
        BridgeGapReport with lists of unbridged families.
    """
    from sqlalchemy import text

    # Get all document families with extractions from source
    families_with_extractions = source_session.execute(
        text(
            """
            SELECT
                df.id AS family_id,
                s.jurisdiction_code,
                df.canonical_title,
                df.short_cite,
                COUNT(e.id) AS extraction_count,
                MIN(e.created_at) AS first_extraction_at
            FROM document_families df
            JOIN sources s ON df.source_id = s.id
            JOIN document_versions dv ON dv.family_id = df.id
            JOIN normalized_source_records nsr ON nsr.document_version_id = dv.id
            JOIN extractions e ON e.source_record_id = nsr.id
            GROUP BY df.id, s.jurisdiction_code, df.canonical_title, df.short_cite
            ORDER BY df.id
            """
        )
    ).mappings().all()

    if not families_with_extractions:
        return BridgeGapReport()

    source_family_ids = {row["family_id"] for row in families_with_extractions}

    # Get bridged family IDs from target
    bridged_rows = target_session.execute(
        text("SELECT document_family_id FROM law_document_bridge WHERE review_status = 'verified'")
    ).fetchall()
    bridged_ids = {row[0] for row in bridged_rows}

    # Build report
    report = BridgeGapReport(
        total_families=len(source_family_ids),
        bridged_families=len(source_family_ids & bridged_ids),
        unbridged_families=len(source_family_ids - bridged_ids),
    )

    for row in families_with_extractions:
        if row["family_id"] not in bridged_ids:
            report.unbridged.append(
                UnbridgedFamily(
                    family_id=row["family_id"],
                    jurisdiction_code=row["jurisdiction_code"],
                    canonical_title=row["canonical_title"],
                    short_cite=row["short_cite"],
                    extraction_count=row["extraction_count"],
                    first_extraction_at=row["first_extraction_at"],
                )
            )

    if report.has_gaps:
        logger.warning(
            "bridge_gaps_detected",
            unbridged_count=report.unbridged_families,
            total_families=report.total_families,
            unbridged_ids=[f.family_id for f in report.unbridged],
        )

    return report


def format_bridge_gap_notification(report: BridgeGapReport) -> str:
    """Format a bridge gap report as a human-readable notification.

    Designed to be sent to the Policy Navigator PM contact per the
    onboarding guide's handoff workflow requirement.
    """
    if not report.has_gaps:
        return "No bridge gaps detected. All document families have bridge rows."

    lines = [
        f"BRIDGE GAP ALERT: {report.unbridged_families} document families need bridge rows",
        f"Generated: {report.generated_at.isoformat()}",
        f"Total families with extractions: {report.total_families}",
        f"Bridged: {report.bridged_families}",
        f"Unbridged: {report.unbridged_families}",
        "",
        "Unbridged families (extractions exist but cannot sync):",
        "-" * 70,
    ]

    for fam in report.unbridged:
        lines.append(
            f"  Family {fam.family_id}: [{fam.jurisdiction_code}] "
            f"{fam.short_cite or 'N/A'} — {fam.canonical_title[:60]}"
        )
        lines.append(
            f"    Extractions: {fam.extraction_count}, "
            f"First: {fam.first_extraction_at.isoformat() if fam.first_extraction_at else 'N/A'}"
        )

    lines.extend([
        "",
        "ACTION REQUIRED:",
        "  Create law_document_bridge rows for each unbridged family before the",
        "  next sync run. Without bridge rows, these extractions will be skipped.",
        "",
        "  To create a bridge row, insert into Policy Navigator's law_document_bridge:",
        "    INSERT INTO law_document_bridge (document_family_id, law_id, match_confidence,",
        "      match_method, review_status) VALUES (<family_id>, <law_id>, 1.0, 'manual', 'verified');",
    ])

    return "\n".join(lines)


def report_to_dict(report: BridgeGapReport) -> dict[str, Any]:
    """Convert a BridgeGapReport to a JSON-serializable dict."""
    return {
        "has_gaps": report.has_gaps,
        "total_families": report.total_families,
        "bridged_families": report.bridged_families,
        "unbridged_families": report.unbridged_families,
        "generated_at": report.generated_at.isoformat(),
        "unbridged": [
            {
                "family_id": f.family_id,
                "jurisdiction_code": f.jurisdiction_code,
                "canonical_title": f.canonical_title,
                "short_cite": f.short_cite,
                "extraction_count": f.extraction_count,
                "first_extraction_at": (
                    f.first_extraction_at.isoformat() if f.first_extraction_at else None
                ),
            }
            for f in report.unbridged
        ],
    }
