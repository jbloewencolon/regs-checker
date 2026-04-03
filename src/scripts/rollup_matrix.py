"""Rollup extraction data into State AI Regulation Matrix detail tables.

Aggregates per-passage synced_extractions into per-law summary rows in:
  - law_enforcement_details
  - law_obligation_flags
  - law_triggering_thresholds
  - jurisdictional_conflicts

Run after sync_extractions.py to populate the matrix view.

Usage:
    python -m src.scripts.rollup_matrix
    python -m src.scripts.rollup_matrix --target-url "postgresql://..."
    python -m src.scripts.rollup_matrix --dry-run

Environment variables:
    REGS_POLICY_NAVIGATOR_URL — Policy Navigator Supabase (target)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def _parse_payload(raw) -> dict:
    """Parse a JSONB payload that may be a string or dict."""
    if isinstance(raw, str):
        return json.loads(raw)
    return raw or {}


def rollup_enforcement(session) -> dict:
    """Aggregate obligation enforcement fields into law_enforcement_details.

    Logic:
      - private_right_of_action: ANY(true) across extractions for the law
      - max_civil_penalty_usd: MAX() across extractions
      - cure_period_days: MIN() (most restrictive cure period)
    """
    rows = session.execute(
        text("""
            SELECT law_id, id as extraction_id, payload
            FROM synced_extractions
            WHERE extraction_type IN ('obligation', 'enforcement')
        """)
    ).mappings().all()

    by_law: dict[int, dict] = {}
    for row in rows:
        law_id = row["law_id"]
        payload = _parse_payload(row["payload"])
        if law_id not in by_law:
            by_law[law_id] = {
                "private_right_of_action": None,
                "max_civil_penalty_usd": None,
                "cure_period_days": None,
                "enforcing_body": None,
                "extraction_ids": [],
            }
        entry = by_law[law_id]
        entry["extraction_ids"].append(row["extraction_id"])

        pra = payload.get("private_right_of_action")
        if pra is True:
            entry["private_right_of_action"] = True

        penalty = payload.get("max_civil_penalty_usd")
        if isinstance(penalty, (int, float)) and penalty > 0:
            cur = entry["max_civil_penalty_usd"]
            entry["max_civil_penalty_usd"] = max(cur or 0, int(penalty))

        cure = payload.get("cure_period_days")
        if isinstance(cure, (int, float)) and cure > 0:
            cur = entry["cure_period_days"]
            entry["cure_period_days"] = min(cur, int(cure)) if cur else int(cure)

        body = payload.get("enforcing_body")
        if body and not entry["enforcing_body"]:
            entry["enforcing_body"] = body

    upserted = 0
    for law_id, data in by_law.items():
        session.execute(
            text("""
                INSERT INTO law_enforcement_details
                    (law_id, private_right_of_action, max_civil_penalty_usd,
                     cure_period_days, enforcing_body, source_extraction_ids, updated_at)
                VALUES (:law_id, :pra, :penalty, :cure, :body, :eids, now())
                ON CONFLICT (law_id) DO UPDATE SET
                    private_right_of_action = COALESCE(EXCLUDED.private_right_of_action, law_enforcement_details.private_right_of_action),
                    max_civil_penalty_usd = GREATEST(EXCLUDED.max_civil_penalty_usd, law_enforcement_details.max_civil_penalty_usd),
                    cure_period_days = LEAST(EXCLUDED.cure_period_days, law_enforcement_details.cure_period_days),
                    enforcing_body = COALESCE(EXCLUDED.enforcing_body, law_enforcement_details.enforcing_body),
                    source_extraction_ids = EXCLUDED.source_extraction_ids,
                    updated_at = now()
            """),
            {
                "law_id": law_id,
                "pra": data["private_right_of_action"],
                "penalty": data["max_civil_penalty_usd"],
                "cure": data["cure_period_days"],
                "body": data["enforcing_body"],
                "eids": data["extraction_ids"],
            },
        )
        upserted += 1

    return {"enforcement_laws_updated": upserted}


def rollup_obligation_flags(session) -> dict:
    """Aggregate compliance_mechanism extractions into law_obligation_flags.

    Logic:
      - is_bias_testing ANY(true) → bias_testing_required
      - is_red_teaming ANY(true) → red_teaming_required
      - nist_measure_refs UNION → nist_mappings
      - assessment_frequency_months MIN() → impact_assessment_frequency_months
      - is_third_party_audit ANY(true) → third_party_audit_required
      - incident_reporting_hours MIN() → ag_incident_reporting_hours
    """
    rows = session.execute(
        text("""
            SELECT law_id, id as extraction_id, payload
            FROM synced_extractions
            WHERE extraction_type = 'compliance_mechanism'
        """)
    ).mappings().all()

    by_law: dict[int, dict] = {}
    for row in rows:
        law_id = row["law_id"]
        payload = _parse_payload(row["payload"])
        if law_id not in by_law:
            by_law[law_id] = {
                "bias_testing": False,
                "red_teaming": False,
                "nist_refs": set(),
                "freq_months": None,
                "third_party": False,
                "transparency": False,
                "reporting_hours": None,
                "extraction_ids": [],
            }
        entry = by_law[law_id]
        entry["extraction_ids"].append(row["extraction_id"])

        if payload.get("is_bias_testing"):
            entry["bias_testing"] = True
        if payload.get("is_red_teaming"):
            entry["red_teaming"] = True
        if payload.get("is_third_party_audit"):
            entry["third_party"] = True

        nist = payload.get("nist_measure_refs")
        if isinstance(nist, list):
            entry["nist_refs"].update(r for r in nist if isinstance(r, str))

        freq = payload.get("assessment_frequency_months")
        if isinstance(freq, (int, float)) and freq > 0:
            cur = entry["freq_months"]
            entry["freq_months"] = min(cur, int(freq)) if cur else int(freq)

        hours = payload.get("incident_reporting_hours")
        if isinstance(hours, (int, float)) and hours > 0:
            cur = entry["reporting_hours"]
            entry["reporting_hours"] = min(cur, int(hours)) if cur else int(hours)

        mtype = payload.get("mechanism_type", "")
        if mtype in ("notification", "disclosure"):
            entry["transparency"] = True

    upserted = 0
    for law_id, data in by_law.items():
        nist_list = sorted(data["nist_refs"]) if data["nist_refs"] else None
        session.execute(
            text("""
                INSERT INTO law_obligation_flags
                    (law_id, bias_testing_required, red_teaming_required, nist_mappings,
                     impact_assessment_frequency_months, third_party_audit_required,
                     consumer_transparency_notice, ag_incident_reporting_hours,
                     source_extraction_ids, last_rollup_at, updated_at)
                VALUES (:law_id, :bias, :red, :nist, :freq, :tpa, :ctn, :hours, :eids, now(), now())
                ON CONFLICT (law_id) DO UPDATE SET
                    bias_testing_required = EXCLUDED.bias_testing_required OR law_obligation_flags.bias_testing_required,
                    red_teaming_required = EXCLUDED.red_teaming_required OR law_obligation_flags.red_teaming_required,
                    nist_mappings = COALESCE(EXCLUDED.nist_mappings, law_obligation_flags.nist_mappings),
                    impact_assessment_frequency_months = LEAST(EXCLUDED.impact_assessment_frequency_months, law_obligation_flags.impact_assessment_frequency_months),
                    third_party_audit_required = EXCLUDED.third_party_audit_required OR law_obligation_flags.third_party_audit_required,
                    consumer_transparency_notice = EXCLUDED.consumer_transparency_notice OR law_obligation_flags.consumer_transparency_notice,
                    ag_incident_reporting_hours = LEAST(EXCLUDED.ag_incident_reporting_hours, law_obligation_flags.ag_incident_reporting_hours),
                    source_extraction_ids = EXCLUDED.source_extraction_ids,
                    last_rollup_at = now(),
                    updated_at = now()
            """),
            {
                "law_id": law_id,
                "bias": data["bias_testing"],
                "red": data["red_teaming"],
                "nist": nist_list,
                "freq": data["freq_months"],
                "tpa": data["third_party"],
                "ctn": data["transparency"],
                "hours": data["reporting_hours"],
                "eids": data["extraction_ids"],
            },
        )
        upserted += 1

    return {"obligation_flags_laws_updated": upserted}


def rollup_thresholds(session) -> dict:
    """Aggregate threshold/exception extractions into law_triggering_thresholds.

    Logic:
      - compute_flops: MAX() across extractions
      - consequential_decision_sectors: UNION of sector_applicability arrays
      - exemptions: UNION of exception descriptions
    """
    rows = session.execute(
        text("""
            SELECT law_id, id as extraction_id, payload
            FROM synced_extractions
            WHERE extraction_type IN ('threshold', 'exception')
        """)
    ).mappings().all()

    by_law: dict[int, dict] = {}
    for row in rows:
        law_id = row["law_id"]
        payload = _parse_payload(row["payload"])
        if law_id not in by_law:
            by_law[law_id] = {
                "compute_flops": None,
                "compute_description": None,
                "sectors": set(),
                "exemptions": set(),
                "extraction_ids": [],
            }
        entry = by_law[law_id]
        entry["extraction_ids"].append(row["extraction_id"])

        flops = payload.get("compute_flops")
        if isinstance(flops, (int, float)) and flops > 0:
            cur = entry["compute_flops"]
            entry["compute_flops"] = max(cur or 0, float(flops))

        cdesc = payload.get("compute_description")
        if cdesc and not entry["compute_description"]:
            entry["compute_description"] = cdesc

        sectors = payload.get("sector_applicability")
        if isinstance(sectors, list):
            entry["sectors"].update(s for s in sectors if isinstance(s, str))

        exceptions = payload.get("exceptions")
        if isinstance(exceptions, str) and exceptions:
            entry["exemptions"].add(exceptions)
        elif isinstance(exceptions, list):
            for exc in exceptions:
                if isinstance(exc, str) and exc:
                    entry["exemptions"].add(exc)

    upserted = 0
    for law_id, data in by_law.items():
        sectors_list = sorted(data["sectors"]) if data["sectors"] else None
        exemptions_list = sorted(data["exemptions"]) if data["exemptions"] else None
        session.execute(
            text("""
                INSERT INTO law_triggering_thresholds
                    (law_id, compute_flops, compute_description,
                     consequential_decision_sectors, exemptions,
                     source_extraction_ids, updated_at)
                VALUES (:law_id, :flops, :cdesc, :sectors, :exemptions, :eids, now())
                ON CONFLICT (law_id) DO UPDATE SET
                    compute_flops = GREATEST(EXCLUDED.compute_flops, law_triggering_thresholds.compute_flops),
                    compute_description = COALESCE(EXCLUDED.compute_description, law_triggering_thresholds.compute_description),
                    consequential_decision_sectors = COALESCE(EXCLUDED.consequential_decision_sectors, law_triggering_thresholds.consequential_decision_sectors),
                    exemptions = COALESCE(EXCLUDED.exemptions, law_triggering_thresholds.exemptions),
                    source_extraction_ids = EXCLUDED.source_extraction_ids,
                    updated_at = now()
            """),
            {
                "law_id": law_id,
                "flops": data["compute_flops"],
                "cdesc": data["compute_description"],
                "sectors": sectors_list,
                "exemptions": exemptions_list,
                "eids": data["extraction_ids"],
            },
        )
        upserted += 1

    return {"threshold_laws_updated": upserted}


def rollup_conflicts(session) -> dict:
    """Insert preemption_signal extractions into jurisdictional_conflicts.

    Each extraction becomes one row. Idempotent via source_extraction_ids check.
    """
    rows = session.execute(
        text("""
            SELECT law_id, id as extraction_id, payload, evidence_spans
            FROM synced_extractions
            WHERE extraction_type = 'preemption_signal'
        """)
    ).mappings().all()

    # Get existing extraction IDs to avoid duplicates
    existing = session.execute(
        text("SELECT unnest(source_extraction_ids) AS eid FROM jurisdictional_conflicts")
    ).scalars().all()
    existing_ids = set(existing)

    inserted = 0
    for row in rows:
        if row["extraction_id"] in existing_ids:
            continue

        payload = _parse_payload(row["payload"])
        conflict_type = payload.get("conflict_type", "other")
        # Validate enum value
        valid_types = {
            "federal_preemption", "interstate_commerce", "cross_state_conflict",
            "first_amendment", "dormant_commerce_clause", "agency_jurisdiction", "other",
        }
        if conflict_type not in valid_types:
            conflict_type = "other"

        session.execute(
            text("""
                INSERT INTO jurisdictional_conflicts
                    (law_id, conflict_type, description, related_authority,
                     severity, confidence, evidence_spans, source_extraction_ids)
                VALUES (:law_id, :ctype::conflict_type, :desc, :auth,
                        :sev, :conf, :spans::jsonb, ARRAY[:eid])
            """),
            {
                "law_id": row["law_id"],
                "ctype": conflict_type,
                "desc": payload.get("description", ""),
                "auth": payload.get("related_authority"),
                "sev": payload.get("severity", "medium"),
                "conf": 0.7,  # Default confidence for extraction-derived conflicts
                "spans": json.dumps(row["evidence_spans"]) if row["evidence_spans"] else None,
                "eid": row["extraction_id"],
            },
        )
        inserted += 1

    return {"conflicts_inserted": inserted}


def run_rollup(target_url: str, dry_run: bool = False) -> dict:
    """Run full rollup across all matrix detail tables."""
    engine = create_engine(target_url)
    session = sessionmaker(bind=engine)()

    try:
        print("Rolling up extraction data into matrix detail tables...\n")

        results = {}

        print("  1/4 Enforcement details...")
        r = rollup_enforcement(session)
        results.update(r)
        print(f"       {r}")

        print("  2/4 Obligation flags...")
        r = rollup_obligation_flags(session)
        results.update(r)
        print(f"       {r}")

        print("  3/4 Triggering thresholds...")
        r = rollup_thresholds(session)
        results.update(r)
        print(f"       {r}")

        print("  4/4 Jurisdictional conflicts...")
        r = rollup_conflicts(session)
        results.update(r)
        print(f"       {r}")

        if dry_run:
            session.rollback()
            print("\nDRY RUN — changes rolled back.")
        else:
            session.commit()
            print("\nRollup committed.")

        return results

    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Rollup synced extractions into matrix detail tables"
    )
    parser.add_argument(
        "--target-url",
        default=None,
        help="Policy Navigator Supabase URL (or set REGS_POLICY_NAVIGATOR_URL)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing",
    )
    args = parser.parse_args()

    target_url = args.target_url or os.environ.get("REGS_POLICY_NAVIGATOR_URL")
    if not target_url:
        print(
            "Error: No target URL. Set --target-url or REGS_POLICY_NAVIGATOR_URL.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Target: {target_url[:60]}...")
    print(f"Mode:   {'DRY RUN' if args.dry_run else 'LIVE'}\n")

    results = run_rollup(target_url, dry_run=args.dry_run)

    print(f"\n{'=' * 60}")
    for k, v in results.items():
        print(f"  {k}: {v}")
    print("Done.")


if __name__ == "__main__":
    main()
