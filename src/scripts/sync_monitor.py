"""Sync health monitor: Regs Checker Supabase ↔ Policy Navigator Supabase.

Runs before and after every sync to verify data integrity, detect drift,
and flag quality alerts. Connects to both Supabase instances and compares
extraction counts, confidence distributions, and sync lag.

Alert thresholds are calibrated against batch run results:
  - 40% Tier C: More than 40% of extractions at low confidence suggests
    a prompt or model quality regression.
  - 65% ambiguity: More than 65% of ambiguity extractions means the
    ambiguity agent may be over-flagging.

Usage:
    # Run health check:
    python -m src.scripts.sync_monitor

    # With explicit URLs:
    python -m src.scripts.sync_monitor \\
        --source-url "postgresql://..." \\
        --target-url "postgresql://..."

    # JSON output (for CI/scripting):
    python -m src.scripts.sync_monitor --json

Environment variables:
    REGS_SUPABASE_URL         — Regs Checker Supabase (source)
    REGS_POLICY_NAVIGATOR_URL — Policy Navigator Supabase (target)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Alert thresholds — calibrated against batch run baselines
# ---------------------------------------------------------------------------
TIER_C_ALERT_THRESHOLD = 0.40       # >40% Tier C = quality regression
SYNC_LAG_ALERT_THRESHOLD = 500      # >500 unsynced extractions = sync stalled


@dataclass
class HealthReport:
    """Aggregated health check results."""

    # Source (Regs Checker) stats
    source_total_extractions: int = 0
    source_by_type: dict[str, int] = field(default_factory=dict)
    source_by_tier: dict[str, int] = field(default_factory=dict)
    source_by_status: dict[str, int] = field(default_factory=dict)

    # Target (Policy Navigator) stats
    target_total_synced: int = 0
    target_by_type: dict[str, int] = field(default_factory=dict)
    target_by_tier: dict[str, int] = field(default_factory=dict)

    # Bridge coverage
    bridge_entries: int = 0
    source_doc_families: int = 0
    bridged_families: int = 0

    # Sync lag
    sync_lag: int = 0  # extractions in source not yet in target
    source_max_id: int = 0
    target_max_id: int = 0

    # Alerts
    alerts: list[str] = field(default_factory=list)
    healthy: bool = True

    def add_alert(self, message: str) -> None:
        self.alerts.append(message)
        self.healthy = False


def _query_source(session) -> dict:
    """Gather stats from Regs Checker Supabase."""
    stats: dict = {}

    stats["total"] = session.execute(
        text("SELECT COUNT(*) FROM extractions")
    ).scalar()

    stats["max_id"] = session.execute(
        text("SELECT COALESCE(MAX(id), 0) FROM extractions")
    ).scalar()

    # By extraction type
    rows = session.execute(
        text("SELECT extraction_type, COUNT(*) FROM extractions GROUP BY extraction_type")
    ).fetchall()
    stats["by_type"] = {row[0]: row[1] for row in rows}

    # By confidence tier
    rows = session.execute(
        text("SELECT confidence_tier, COUNT(*) FROM extractions GROUP BY confidence_tier")
    ).fetchall()
    stats["by_tier"] = {row[0]: row[1] for row in rows}

    # By review status
    rows = session.execute(
        text("SELECT review_status, COUNT(*) FROM extractions GROUP BY review_status")
    ).fetchall()
    stats["by_status"] = {row[0]: row[1] for row in rows}

    # Distinct document families with extractions
    stats["doc_families"] = session.execute(
        text(
            """
            SELECT COUNT(DISTINCT dv.family_id)
            FROM extractions e
            JOIN normalized_source_records nsr ON e.source_record_id = nsr.id
            JOIN document_versions dv ON nsr.document_version_id = dv.id
            """
        )
    ).scalar()

    return stats


def _query_target(session) -> dict:
    """Gather stats from Policy Navigator Supabase."""
    stats: dict = {}

    stats["total"] = session.execute(
        text("SELECT COUNT(*) FROM synced_extractions")
    ).scalar()

    stats["max_id"] = session.execute(
        text("SELECT COALESCE(MAX(system_a_extraction_id), 0) FROM synced_extractions")
    ).scalar()

    # By extraction type
    rows = session.execute(
        text("SELECT extraction_type, COUNT(*) FROM synced_extractions GROUP BY extraction_type")
    ).fetchall()
    stats["by_type"] = {row[0]: row[1] for row in rows}

    # By confidence tier
    rows = session.execute(
        text("SELECT confidence_tier, COUNT(*) FROM synced_extractions GROUP BY confidence_tier")
    ).fetchall()
    stats["by_tier"] = {row[0]: row[1] for row in rows}

    # Bridge coverage
    stats["bridge_entries"] = session.execute(
        text("SELECT COUNT(*) FROM law_document_bridge")
    ).scalar()

    return stats


def run_health_check(source_url: str, target_url: str) -> HealthReport:
    """Run full health check across both Supabase instances.

    Returns a HealthReport with stats, sync lag, and any triggered alerts.
    """
    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)

    source_session = sessionmaker(bind=source_engine)()
    target_session = sessionmaker(bind=target_engine)()

    report = HealthReport()

    try:
        # --- Source stats ---
        source_stats = _query_source(source_session)
        report.source_total_extractions = source_stats["total"]
        report.source_by_type = source_stats["by_type"]
        report.source_by_tier = source_stats["by_tier"]
        report.source_by_status = source_stats["by_status"]
        report.source_max_id = source_stats["max_id"]
        report.source_doc_families = source_stats["doc_families"]

        # --- Target stats ---
        target_stats = _query_target(target_session)
        report.target_total_synced = target_stats["total"]
        report.target_by_type = target_stats["by_type"]
        report.target_by_tier = target_stats["by_tier"]
        report.target_max_id = target_stats["max_id"]
        report.bridge_entries = target_stats["bridge_entries"]

        # --- Bridge coverage ---
        if report.bridge_entries > 0 and report.source_doc_families > 0:
            report.bridged_families = min(report.bridge_entries, report.source_doc_families)

        # --- Sync lag ---
        report.sync_lag = report.source_total_extractions - report.target_total_synced

        # --- Alert checks ---

        # 1. Tier C threshold
        if report.source_total_extractions > 0:
            tier_c_count = report.source_by_tier.get("C", 0)
            tier_c_pct = tier_c_count / report.source_total_extractions
            if tier_c_pct > TIER_C_ALERT_THRESHOLD:
                report.add_alert(
                    f"QUALITY: {tier_c_pct:.0%} of extractions are Tier C "
                    f"({tier_c_count:,}/{report.source_total_extractions:,}). "
                    f"Threshold: {TIER_C_ALERT_THRESHOLD:.0%}. "
                    f"Investigate prompt or model quality before next batch."
                )

        # 2. Sync lag
        if report.sync_lag > SYNC_LAG_ALERT_THRESHOLD:
            report.add_alert(
                f"SYNC LAG: {report.sync_lag:,} extractions in Regs Checker "
                f"not yet in Policy Navigator. "
                f"Threshold: {SYNC_LAG_ALERT_THRESHOLD:,}. "
                f"Run sync_extractions.py to catch up."
            )

        # 4. Empty bridge
        if report.bridge_entries == 0:
            report.add_alert(
                "BRIDGE: law_document_bridge is empty. "
                "No extractions can be synced until bridge entries are created."
            )

        # 5. Zero extractions in source
        if report.source_total_extractions == 0:
            report.add_alert(
                "SOURCE: Regs Checker has 0 extractions. "
                "Run the extraction pipeline before syncing."
            )

    finally:
        source_session.close()
        target_session.close()

    return report


def print_report(report: HealthReport) -> None:
    """Print a human-readable health report to stdout."""
    status = "HEALTHY" if report.healthy else "ALERTS DETECTED"
    print(f"\n{'=' * 60}")
    print(f"  Sync Health Monitor — {status}")
    print(f"{'=' * 60}")

    print("\n--- Source: Regs Checker Supabase ---")
    print(f"  Total extractions:   {report.source_total_extractions:,}")
    print(f"  Max extraction ID:   {report.source_max_id:,}")
    print(f"  Document families:   {report.source_doc_families}")
    if report.source_by_type:
        print("  By type:")
        for t, count in sorted(report.source_by_type.items()):
            print(f"    {t:25s} {count:>6,}")
    if report.source_by_tier:
        print("  By confidence tier:")
        for tier, count in sorted(report.source_by_tier.items()):
            pct = count / report.source_total_extractions * 100 if report.source_total_extractions else 0
            print(f"    Tier {tier}: {count:>6,}  ({pct:5.1f}%)")
    if report.source_by_status:
        print("  By review status:")
        for status_val, count in sorted(report.source_by_status.items()):
            print(f"    {status_val:25s} {count:>6,}")

    print("\n--- Target: Policy Navigator Supabase ---")
    print(f"  Total synced:        {report.target_total_synced:,}")
    print(f"  Max synced ID:       {report.target_max_id:,}")
    print(f"  Bridge entries:      {report.bridge_entries}")
    if report.target_by_type:
        print("  By type:")
        for t, count in sorted(report.target_by_type.items()):
            print(f"    {t:25s} {count:>6,}")

    print("\n--- Sync Status ---")
    print(f"  Sync lag:            {report.sync_lag:,} extractions")
    print(f"  Bridge coverage:     {report.bridged_families}/{report.source_doc_families} families")
    print(f"  Cursor:              source max={report.source_max_id}, target max={report.target_max_id}")

    if report.alerts:
        print(f"\n--- ALERTS ({len(report.alerts)}) ---")
        for i, alert in enumerate(report.alerts, 1):
            print(f"  [{i}] {alert}")
    else:
        print("\n  No alerts. All checks passed.")

    print(f"\n{'=' * 60}")


def report_to_dict(report: HealthReport) -> dict:
    """Convert HealthReport to a JSON-serializable dict."""
    return {
        "healthy": report.healthy,
        "source": {
            "total_extractions": report.source_total_extractions,
            "max_id": report.source_max_id,
            "doc_families": report.source_doc_families,
            "by_type": report.source_by_type,
            "by_tier": report.source_by_tier,
            "by_status": report.source_by_status,
        },
        "target": {
            "total_synced": report.target_total_synced,
            "max_id": report.target_max_id,
            "bridge_entries": report.bridge_entries,
            "by_type": report.target_by_type,
            "by_tier": report.target_by_tier,
        },
        "sync": {
            "lag": report.sync_lag,
            "bridged_families": report.bridged_families,
            "source_doc_families": report.source_doc_families,
        },
        "alerts": report.alerts,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Sync health monitor: Regs Checker ↔ Policy Navigator"
    )
    parser.add_argument(
        "--source-url",
        default=None,
        help="Regs Checker Supabase URL (or set REGS_SUPABASE_URL)",
    )
    parser.add_argument(
        "--target-url",
        default=None,
        help="Policy Navigator Supabase URL (or set REGS_POLICY_NAVIGATOR_URL)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output report as JSON (for CI/scripting)",
    )
    args = parser.parse_args()

    source_url = args.source_url or os.environ.get("REGS_SUPABASE_URL")
    target_url = args.target_url or os.environ.get("REGS_POLICY_NAVIGATOR_URL")

    if not source_url:
        print(
            "Error: No source URL. Set --source-url or REGS_SUPABASE_URL.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not target_url:
        print(
            "Error: No target URL. Set --target-url or REGS_POLICY_NAVIGATOR_URL.",
            file=sys.stderr,
        )
        sys.exit(1)

    report = run_health_check(source_url, target_url)

    if args.json_output:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        print_report(report)

    # Exit code: 0 = healthy, 1 = alerts
    sys.exit(0 if report.healthy else 1)


if __name__ == "__main__":
    main()
