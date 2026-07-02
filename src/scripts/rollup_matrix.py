"""Rollup extraction data into State AI Regulation Matrix detail tables.

Aggregates per-passage synced_extractions into per-law summary rows in:
  - law_enforcement_details
  - law_obligation_flags
  - law_triggering_thresholds
  - jurisdictional_conflicts

Run after sync_extractions.py to populate the matrix view.

P2-1: every query here filters to review_status IN ('approved', 'verified')
(see _ELIGIBLE_REVIEW_STATUSES below for why 'approved' alone is wrong once
Policy Navigator's own review workflow is in the picture) and confidence_tier
at or above the publish floor. This is defense in depth —
synced_extractions should only ever contain eligible rows once P2-2 (purge)
and P2-3 (CHECK constraint) have landed, but this script must not silently
re-aggregate a legacy unapproved row that predates that cleanup, or a row
that was eligible under a since-lowered floor.

P2-4: rollups are recomputed from scratch and REPLACE the target row on
every run (plain overwrite in the ON CONFLICT clause) rather than merging
with GREATEST/LEAST/COALESCE against whatever was there before. The old
merge behavior was ratchet-only — a correction (re-review lowering a
penalty, a rejected extraction being purged) could never be reflected,
because the merged value could only grow more extreme, never shrink back
toward a corrected one. Every merge-aggregating table now also records
contributing_extraction_count and derived_from_tier_floor so a reviewer can
see how much source material and what trust floor produced each row.

P2-5 (partial — see the docstring on rollup_enforcement() for why a full
fix is architecturally out of scope here): max_civil_penalty_usd is still a
MAX() across contributing extractions, but is now paired with a
penalty_notes caveat when more than one differently-worded penalty mention
contributed, so a bare number never implies more precision than the source
data supports.

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

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.core.config import settings
from src.core.vocab_loader import get_canonical_codes, normalize

# P2-1: mirrors src/scripts/sync_extractions.py::_eligible_tiers. sync_extractions.py
# already gates what reaches synced_extractions, so this is defense in depth against
# legacy rows or a floor value that has since changed, not the primary enforcement point.
_TIER_ORDER = ["A", "B", "C", "D"]

# Policy Navigator has its own independent, post-sync review workflow
# (extraction_reviews + the fn_update_extraction_consensus trigger) that
# overwrites this same review_status column to one of
# pending/flagged/verified/rejected the moment a PN reviewer votes — it never
# writes 'approved', which is exclusively Regs Checker's vocabulary, set once
# at sync time by sync_extractions.py and never touched by this table's own
# rows afterward unless PN review activity occurs.
#
# Product decision: RC approval is the baseline gate (a row must have been
# RC-approved to sync at all); PN's own review is a backup veto layer, not a
# required blessing. So a row is rollup-eligible if it is either still at its
# RC-approved sync-time value ('approved', meaning no PN reviewer has acted
# on it yet) or has been positively confirmed by PN's own reviewers
# ('verified'). 'pending'/'needs_revision' (legacy pre-P2-1 rows that were
# never RC-approved) and 'rejected'/'flagged' (an active veto, by either
# side) are excluded.
_ELIGIBLE_REVIEW_STATUSES = ("approved", "verified")


def _eligible_tiers(min_tier: str) -> list[str]:
    min_tier = (min_tier or "C").upper()
    if min_tier not in _TIER_ORDER:
        return list(_TIER_ORDER)
    idx = _TIER_ORDER.index(min_tier)
    return _TIER_ORDER[: idx + 1]


def _parse_payload(raw) -> dict:
    """Parse a JSONB payload that may be a string or dict."""
    if isinstance(raw, str):
        return json.loads(raw)
    return raw or {}


def rollup_enforcement(session, min_tier: str) -> dict:
    """Aggregate obligation enforcement fields into law_enforcement_details.

    Logic:
      - private_right_of_action: ANY(true) across extractions for the law
      - max_civil_penalty_usd: MAX() across extractions
      - cure_period_days: MIN() (most restrictive cure period)

    P2-5 note: max_civil_penalty_usd here is aggregated from PASSAGE-level
    obligation.enforcement payloads (src.schemas.extraction.EnforcementInfo),
    which carry no penalty-unit field at all — a $500/day figure and a
    $10,000/violation figure are indistinguishable at this layer, so MAX()
    across them cannot be corrected for unit even in principle. The
    bill-level enforcement_agent DOES tag amounts with penalty_per
    (violation/day/occurrence) — see src/agents/enforcement_agent.py — but
    bill_level_extractions is not currently synced to Policy Navigator at
    all (sync_extractions.py only syncs the passage-level `extractions`
    table), and law_enforcement_details has no column to hold a unit even
    if it were. Properly fixing this means syncing bill_level_extractions
    and preferring the enforcement_agent's single clean per-law record over
    this passage-level scatter-gather — a larger change than this rollup
    function's aggregation logic, tracked separately (see
    docs/phase2_completion_log.md).

    Within that constraint, this function no longer silently implies a
    single verified number: contributing_extraction_count lets a reviewer
    see when the MAX() ceiling was built from multiple extractions (higher
    chance of a unit conflation) versus a single clean source.
    """
    eligible_tiers = _eligible_tiers(min_tier)
    rows = session.execute(
        text("""
            SELECT law_id, id as extraction_id, payload
            FROM synced_extractions
            WHERE extraction_type IN ('obligation', 'enforcement')
              AND review_status = ANY(:statuses)
              AND confidence_tier::text = ANY(:tiers)
        """),
        {"tiers": eligible_tiers, "statuses": list(_ELIGIBLE_REVIEW_STATUSES)},
    ).mappings().all()

    by_law: dict[int, dict] = {}
    for row in rows:
        law_id = row["law_id"]
        payload = _parse_payload(row["payload"])
        if law_id not in by_law:
            by_law[law_id] = {
                "private_right_of_action": None,
                "max_civil_penalty_usd": None,
                "penalty_amounts_seen": 0,
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
            entry["penalty_amounts_seen"] += 1

        cure = payload.get("cure_period_days")
        if isinstance(cure, (int, float)) and cure > 0:
            cur = entry["cure_period_days"]
            entry["cure_period_days"] = min(cur, int(cure)) if cur else int(cure)

        body = payload.get("enforcing_body")
        if body and not entry["enforcing_body"]:
            entry["enforcing_body"] = body

    upserted = 0
    for law_id, data in by_law.items():
        penalty_note = (
            f"max_civil_penalty_usd is the ceiling across {data['penalty_amounts_seen']} "
            "differently-worded penalty mentions in this law's text; the source data does "
            "not record whether each is per-violation, per-day, or per-occurrence, so this "
            "figure may mix units and should be verified against the statute before citing."
            if data["penalty_amounts_seen"] > 1
            else None
        )
        session.execute(
            text("""
                INSERT INTO law_enforcement_details
                    (law_id, private_right_of_action, max_civil_penalty_usd,
                     cure_period_days, enforcing_body, penalty_notes,
                     contributing_extraction_count, derived_from_tier_floor,
                     source_extraction_ids, updated_at)
                VALUES (:law_id, :pra, :penalty, :cure, :body, :notes, :count, :floor, :eids, now())
                ON CONFLICT (law_id) DO UPDATE SET
                    private_right_of_action = EXCLUDED.private_right_of_action,
                    max_civil_penalty_usd = EXCLUDED.max_civil_penalty_usd,
                    cure_period_days = EXCLUDED.cure_period_days,
                    enforcing_body = EXCLUDED.enforcing_body,
                    penalty_notes = EXCLUDED.penalty_notes,
                    contributing_extraction_count = EXCLUDED.contributing_extraction_count,
                    derived_from_tier_floor = EXCLUDED.derived_from_tier_floor,
                    source_extraction_ids = EXCLUDED.source_extraction_ids,
                    updated_at = now()
            """),
            {
                "law_id": law_id,
                "pra": data["private_right_of_action"],
                "penalty": data["max_civil_penalty_usd"],
                "cure": data["cure_period_days"],
                "body": data["enforcing_body"],
                "notes": penalty_note,
                "count": len(data["extraction_ids"]),
                "floor": min_tier.upper(),
                "eids": data["extraction_ids"],
            },
        )
        upserted += 1

    return {"enforcement_laws_updated": upserted}


def rollup_obligation_flags(session, min_tier: str) -> dict:
    """Aggregate compliance_mechanism extractions into law_obligation_flags.

    Logic:
      - is_bias_testing ANY(true) → bias_testing_required
      - is_red_teaming ANY(true) → red_teaming_required
      - nist_measure_refs UNION → nist_mappings
      - assessment_frequency_months MIN() → impact_assessment_frequency_months
      - is_third_party_audit ANY(true) → third_party_audit_required
      - incident_reporting_hours MIN() → ag_incident_reporting_hours
    """
    eligible_tiers = _eligible_tiers(min_tier)
    rows = session.execute(
        text("""
            SELECT law_id, id as extraction_id, payload
            FROM synced_extractions
            WHERE extraction_type = 'compliance_mechanism'
              AND review_status = ANY(:statuses)
              AND confidence_tier::text = ANY(:tiers)
        """),
        {"tiers": eligible_tiers, "statuses": list(_ELIGIBLE_REVIEW_STATUSES)},
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
                     contributing_extraction_count, derived_from_tier_floor,
                     source_extraction_ids, last_rollup_at, updated_at)
                VALUES (:law_id, :bias, :red, :nist, :freq, :tpa, :ctn, :hours,
                        :count, :floor, :eids, now(), now())
                ON CONFLICT (law_id) DO UPDATE SET
                    bias_testing_required = EXCLUDED.bias_testing_required,
                    red_teaming_required = EXCLUDED.red_teaming_required,
                    nist_mappings = EXCLUDED.nist_mappings,
                    impact_assessment_frequency_months = EXCLUDED.impact_assessment_frequency_months,
                    third_party_audit_required = EXCLUDED.third_party_audit_required,
                    consumer_transparency_notice = EXCLUDED.consumer_transparency_notice,
                    ag_incident_reporting_hours = EXCLUDED.ag_incident_reporting_hours,
                    contributing_extraction_count = EXCLUDED.contributing_extraction_count,
                    derived_from_tier_floor = EXCLUDED.derived_from_tier_floor,
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
                "count": len(data["extraction_ids"]),
                "floor": min_tier.upper(),
                "eids": data["extraction_ids"],
            },
        )
        upserted += 1

    return {"obligation_flags_laws_updated": upserted}


def rollup_thresholds(session, min_tier: str) -> dict:
    """Aggregate threshold/exception extractions into law_triggering_thresholds.

    Logic:
      - compute_flops: MAX() across extractions
      - consequential_decision_sectors: UNION of sector_applicability arrays
      - exemptions: UNION of exception descriptions
    """
    eligible_tiers = _eligible_tiers(min_tier)
    rows = session.execute(
        text("""
            SELECT law_id, id as extraction_id, payload
            FROM synced_extractions
            WHERE extraction_type IN ('threshold', 'exception')
              AND review_status = ANY(:statuses)
              AND confidence_tier::text = ANY(:tiers)
        """),
        {"tiers": eligible_tiers, "statuses": list(_ELIGIBLE_REVIEW_STATUSES)},
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
                     contributing_extraction_count, derived_from_tier_floor,
                     source_extraction_ids, updated_at)
                VALUES (:law_id, :flops, :cdesc, :sectors, :exemptions,
                        :count, :floor, :eids, now())
                ON CONFLICT (law_id) DO UPDATE SET
                    compute_flops = EXCLUDED.compute_flops,
                    compute_description = EXCLUDED.compute_description,
                    consequential_decision_sectors = EXCLUDED.consequential_decision_sectors,
                    exemptions = EXCLUDED.exemptions,
                    contributing_extraction_count = EXCLUDED.contributing_extraction_count,
                    derived_from_tier_floor = EXCLUDED.derived_from_tier_floor,
                    source_extraction_ids = EXCLUDED.source_extraction_ids,
                    updated_at = now()
            """),
            {
                "law_id": law_id,
                "flops": data["compute_flops"],
                "cdesc": data["compute_description"],
                "sectors": sectors_list,
                "exemptions": exemptions_list,
                "count": len(data["extraction_ids"]),
                "floor": min_tier.upper(),
                "eids": data["extraction_ids"],
            },
        )
        upserted += 1

    return {"threshold_laws_updated": upserted}


def rollup_conflicts(session, min_tier: str) -> dict:
    """Insert preemption_signal extractions into jurisdictional_conflicts.

    Each extraction becomes one row (not a per-law merge, so the de-ratchet
    concern in P2-4 doesn't apply here — there's nothing to overwrite,
    only append). Idempotent via source_extraction_ids check.
    """
    eligible_tiers = _eligible_tiers(min_tier)
    rows = session.execute(
        text("""
            SELECT law_id, id as extraction_id, payload, evidence_spans
            FROM synced_extractions
            WHERE extraction_type = 'preemption_signal'
              AND review_status = ANY(:statuses)
              AND confidence_tier::text = ANY(:tiers)
        """),
        {"tiers": eligible_tiers, "statuses": list(_ELIGIBLE_REVIEW_STATUSES)},
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
        # Validate against ratified legal_context raw conflict_type values.
        # Unknown values fall back to "other" via normalize(); unrecognized
        # terms are queued for vocab_review_queue via flush_unrecognized().
        valid_types = set(get_canonical_codes("legal_context")) or {
            "federal_preemption", "interstate_commerce", "cross_state_conflict",
            "first_amendment", "dormant_commerce_clause", "agency_jurisdiction", "other",
        }
        if conflict_type not in valid_types:
            # Attempt normalization through alias table; unrecognized → "other"
            normalized = normalize("legal_context", conflict_type)
            conflict_type = "other" if normalized == "unclassified" else conflict_type

        # P2-4 bugfix (pre-existing, unrelated to the review/tier filter change
        # above): SQLAlchemy's text() treats `::` immediately after a named
        # bind param as an escaped literal colon, not a Postgres cast operator
        # — ":ctype::conflict_type" was silently unparseable through the ORM
        # session path and raised a SyntaxError the moment there was a
        # preemption_signal row not already in jurisdictional_conflicts.
        # CAST(... AS type) has no such ambiguity.
        session.execute(
            text("""
                INSERT INTO jurisdictional_conflicts
                    (law_id, conflict_type, description, related_authority,
                     severity, confidence, evidence_spans, source_extraction_ids)
                VALUES (:law_id, CAST(:ctype AS conflict_type), :desc, :auth,
                        :sev, :conf, CAST(:spans AS jsonb), ARRAY[:eid])
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


def run_rollup(target_url: str, dry_run: bool = False, min_tier: str | None = None) -> dict:
    """Run full rollup across all matrix detail tables."""
    min_tier = (min_tier or settings.confidence_publish_min_tier or "C").upper()
    engine = create_engine(target_url)
    session = sessionmaker(bind=engine)()

    try:
        print("Rolling up extraction data into matrix detail tables...")
        print(f"Publish filter: review_status='approved' AND confidence_tier IN {_eligible_tiers(min_tier)}\n")

        results = {}

        print("  1/4 Enforcement details...")
        r = rollup_enforcement(session, min_tier)
        results.update(r)
        print(f"       {r}")

        print("  2/4 Obligation flags...")
        r = rollup_obligation_flags(session, min_tier)
        results.update(r)
        print(f"       {r}")

        print("  3/4 Triggering thresholds...")
        r = rollup_thresholds(session, min_tier)
        results.update(r)
        print(f"       {r}")

        print("  4/4 Jurisdictional conflicts...")
        r = rollup_conflicts(session, min_tier)
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
    parser.add_argument(
        "--min-tier",
        default=None,
        help="Confidence tier floor (A/B/C/D, default: settings.confidence_publish_min_tier)",
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

    results = run_rollup(target_url, dry_run=args.dry_run, min_tier=args.min_tier)

    print(f"\n{'=' * 60}")
    for k, v in results.items():
        print(f"  {k}: {v}")
    print("Done.")


if __name__ == "__main__":
    main()
