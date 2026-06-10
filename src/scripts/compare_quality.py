"""Compare local (new pipeline) vs Supabase (old sync) extraction quality.

Runs against local postgres only. Supabase baseline stats are embedded
from MCP queries run on 2026-03-24.

Usage:
    python -m src.scripts.compare_quality
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from sqlalchemy import create_engine, text

DB_URL = os.environ.get(
    "REGS_DATABASE_URL",
    "postgresql://regs:regs@localhost:5434/regs_checker",
)

# ── Supabase baseline (queried via MCP 2026-03-24) ──────────────────────
SUPABASE = {
    "model": "claude-sonnet-4-20250514",
    "total_extractions": 28_885,
    "total_passages": 9_182,
    "passages_with_extractions": 5_543,
    "coverage_pct": 60.4,
    "avg_passage_len": 900,
    "jurisdictions": 48,
    "document_families": 180,
    "by_type": {
        "obligation": {"count": 5077, "avg_conf": 0.837, "tier_a": 2476, "tier_b": 2093, "tier_c": 508},
        "definition": {"count": 1571, "avg_conf": 0.846, "tier_a": 657, "tier_b": 677, "tier_c": 237},
        "threshold":  {"count": 5189, "avg_conf": 0.772, "tier_a": 1847, "tier_b": 700, "tier_c": 2642},
        "ambiguity":  {"count": 17048, "avg_conf": 0.869, "tier_a": 10671, "tier_b": 121, "tier_c": 6256},
    },
    "obligation_fields": {
        "total": 5077,
        "has_subject": 5077, "has_action": 5077, "has_modality": 5077,
        "has_condition": 3887, "has_subject_normalized": 5077,
        "has_section_ref": 5075,
        "has_timeline_effective": 389, "has_enforcing_body": 676, "has_penalty_type": 541,
        "avg_action_len": 110, "avg_subject_len": 32,
    },
    "evidence": {
        "obligation": {"verified_pct": 91.4, "avg_spans": 3.8, "avg_span_len": 80},
        "definition": {"verified_pct": 84.9, "avg_spans": 2.3, "avg_span_len": 120},
        "threshold":  {"verified_pct": 49.5, "avg_spans": 1.6, "avg_span_len": 111},
        "ambiguity":  {"verified_pct": 63.3, "avg_spans": 1.0, "avg_span_len": 57},
    },
    "avg_confidence": 0.845,
    "all_evidence_rate": 100.0,
    "avg_evidence_spans": 1.7,
}


def run_query(engine, sql):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(text(sql)).mappings()]


def fmt_pct(n, total):
    return f"{n/total*100:.1f}%" if total else "N/A"


def compare_bar(label, local_val, supa_val, unit="", higher_better=True):
    """Print a comparison line with directional indicator."""
    if local_val is None or supa_val is None:
        arrow = "  "
    elif (local_val > supa_val) == higher_better:
        arrow = "▲"  # local is better
    elif local_val == supa_val:
        arrow = "="
    else:
        arrow = "▽"  # supabase was better

    local_str = f"{local_val:>10}{unit}" if local_val is not None else "       N/A"
    supa_str = f"{supa_val:>10}{unit}" if supa_val is not None else "       N/A"
    print(f"  {label:<35} {local_str}  {supa_str}  {arrow}")


def main():
    engine = create_engine(DB_URL)

    print("=" * 85)
    print("  DATA QUALITY COMPARISON: Local (New Pipeline) vs Supabase (Old Sync)")
    print("=" * 85)
    print(f"\n  {'':35} {'LOCAL':>12}  {'SUPABASE':>12}")
    print(f"  {'':35} {'(new)':>12}  {'(old)':>12}")
    print("  " + "─" * 70)

    # ── 1. Overall counts ────────────────────────────────────────────────
    print("\n  ◆ VOLUME")

    rows = run_query(engine, "SELECT COUNT(*) as c FROM extractions")
    local_total = rows[0]["c"]
    compare_bar("Total extractions", local_total, SUPABASE["total_extractions"])

    rows = run_query(engine, "SELECT COUNT(DISTINCT source_record_id) as c FROM extractions")
    local_passages_w = rows[0]["c"]

    rows = run_query(engine, "SELECT COUNT(*) as c FROM normalized_source_records")
    local_passages = rows[0]["c"]
    compare_bar("Total passages", local_passages, SUPABASE["total_passages"])
    compare_bar("Passages w/ extractions", local_passages_w, SUPABASE["passages_with_extractions"])

    local_cov = round(local_passages_w / local_passages * 100, 1) if local_passages else 0
    compare_bar("Coverage %", local_cov, SUPABASE["coverage_pct"], "%")

    rows = run_query(engine, "SELECT COUNT(DISTINCT s.jurisdiction_code) as c FROM sources s JOIN document_families df ON df.source_id = s.id JOIN document_versions dv ON dv.family_id = df.id JOIN extractions e ON e.source_record_id IN (SELECT id FROM normalized_source_records WHERE document_version_id = dv.id)")
    local_jurisdictions = rows[0]["c"]
    compare_bar("Jurisdictions w/ data", local_jurisdictions, SUPABASE["jurisdictions"])

    rows = run_query(engine, "SELECT COUNT(DISTINCT df.id) as c FROM document_families df JOIN document_versions dv ON dv.family_id = df.id JOIN normalized_source_records nsr ON nsr.document_version_id = dv.id JOIN extractions e ON e.source_record_id = nsr.id")
    local_families = rows[0]["c"]
    compare_bar("Document families", local_families, SUPABASE["document_families"])

    # ── 2. By extraction type ────────────────────────────────────────────
    print("\n  ◆ EXTRACTION TYPES")

    rows = run_query(engine, """
        SELECT extraction_type,
            COUNT(*) as count,
            ROUND(AVG(confidence_score)::numeric, 3) as avg_conf,
            COUNT(CASE WHEN confidence_tier = 'A' THEN 1 END) as tier_a,
            COUNT(CASE WHEN confidence_tier = 'B' THEN 1 END) as tier_b,
            COUNT(CASE WHEN confidence_tier = 'C' THEN 1 END) as tier_c,
            COUNT(CASE WHEN confidence_tier = 'D' THEN 1 END) as tier_d
        FROM extractions GROUP BY extraction_type ORDER BY count DESC
    """)
    local_by_type = {r["extraction_type"]: r for r in rows}

    all_types = sorted(set(list(local_by_type.keys()) + list(SUPABASE["by_type"].keys())))
    for t in all_types:
        local_c = local_by_type.get(t, {}).get("count", 0)
        supa_c = SUPABASE["by_type"].get(t, {}).get("count", 0)
        local_conf = float(local_by_type[t]["avg_conf"]) if t in local_by_type and local_by_type[t]["avg_conf"] else None
        supa_conf = SUPABASE["by_type"].get(t, {}).get("avg_conf")

        compare_bar(f"  {t} count", local_c, supa_c)
        compare_bar(f"  {t} avg confidence", local_conf, supa_conf)

        # Tier distribution
        if t in local_by_type:
            l = local_by_type[t]
            lt = l["count"]
            local_tier_a_pct = round(l["tier_a"] / lt * 100, 1) if lt else 0
        else:
            local_tier_a_pct = None

        if t in SUPABASE["by_type"]:
            s = SUPABASE["by_type"][t]
            st = s["count"]
            supa_tier_a_pct = round(s["tier_a"] / st * 100, 1) if st else 0
        else:
            supa_tier_a_pct = None

        compare_bar(f"  {t} tier-A %", local_tier_a_pct, supa_tier_a_pct, "%")

    # New types only in local
    new_types = [t for t in local_by_type if t not in SUPABASE["by_type"]]
    if new_types:
        print(f"\n  ★ New extraction types (local only): {', '.join(new_types)}")

    # ── 3. Confidence overview ───────────────────────────────────────────
    print("\n  ◆ CONFIDENCE")

    rows = run_query(engine, "SELECT ROUND(AVG(confidence_score)::numeric, 3) as avg FROM extractions")
    local_avg_conf = float(rows[0]["avg"]) if rows[0]["avg"] else None
    compare_bar("Overall avg confidence", local_avg_conf, SUPABASE["avg_confidence"])

    rows = run_query(engine, """
        SELECT confidence_tier, COUNT(*) as c FROM extractions
        GROUP BY confidence_tier ORDER BY confidence_tier
    """)
    total_local = sum(r["c"] for r in rows)
    for r in rows:
        tier = r["confidence_tier"]
        pct = round(r["c"] / total_local * 100, 1) if total_local else 0
        # Compute supabase equivalent
        supa_tier_count = sum(
            SUPABASE["by_type"][t].get(f"tier_{tier.lower()}", 0)
            for t in SUPABASE["by_type"]
        )
        supa_pct = round(supa_tier_count / SUPABASE["total_extractions"] * 100, 1)
        compare_bar(f"  Tier {tier} %", pct, supa_pct, "%")

    # ── 4. Obligation field completeness ─────────────────────────────────
    print("\n  ◆ OBLIGATION FIELD COMPLETENESS")

    rows = run_query(engine, """
        SELECT
            COUNT(*) as total,
            COUNT(CASE WHEN payload->>'subject' IS NOT NULL AND payload->>'subject' != '' THEN 1 END) as has_subject,
            COUNT(CASE WHEN payload->>'action' IS NOT NULL AND payload->>'action' != '' THEN 1 END) as has_action,
            COUNT(CASE WHEN payload->>'modality' IS NOT NULL THEN 1 END) as has_modality,
            COUNT(CASE WHEN payload->>'condition' IS NOT NULL AND payload->>'condition' != '' THEN 1 END) as has_condition,
            COUNT(CASE WHEN payload->>'subject_normalized' IS NOT NULL THEN 1 END) as has_subject_normalized,
            COUNT(CASE WHEN payload->>'section_reference' IS NOT NULL THEN 1 END) as has_section_ref,
            COUNT(CASE WHEN payload->'timeline'->>'effective_date' IS NOT NULL THEN 1 END) as has_timeline_effective,
            COUNT(CASE WHEN payload->'enforcement'->>'enforcing_body' IS NOT NULL THEN 1 END) as has_enforcing_body,
            COUNT(CASE WHEN payload->'enforcement'->>'penalty_type' IS NOT NULL THEN 1 END) as has_penalty_type,
            ROUND(AVG(length(payload->>'action'))::numeric, 0) as avg_action_len,
            ROUND(AVG(length(payload->>'subject'))::numeric, 0) as avg_subject_len
        FROM extractions WHERE extraction_type = 'obligation'
    """)

    if rows and rows[0]["total"] > 0:
        lo = rows[0]
        so = SUPABASE["obligation_fields"]
        lt = lo["total"]
        st = so["total"]

        compare_bar("Total obligations", lt, st)

        for field in ["has_subject", "has_action", "has_modality", "has_condition",
                      "has_subject_normalized", "has_section_ref",
                      "has_timeline_effective", "has_enforcing_body", "has_penalty_type"]:
            local_pct = round(lo[field] / lt * 100, 1)
            supa_pct = round(so[field] / st * 100, 1)
            compare_bar(f"  {field.replace('has_', '')} %", local_pct, supa_pct, "%")

        compare_bar("  avg action length", int(lo["avg_action_len"]) if lo["avg_action_len"] else 0,
                     so["avg_action_len"], " chars")
        compare_bar("  avg subject length", int(lo["avg_subject_len"]) if lo["avg_subject_len"] else 0,
                     so["avg_subject_len"], " chars")
    else:
        print("  (no obligations in local DB)")

    # ── 5. Evidence quality ──────────────────────────────────────────────
    print("\n  ◆ EVIDENCE QUALITY")

    rows = run_query(engine, """
        SELECT
            extraction_type,
            COUNT(*) as total,
            COUNT(CASE WHEN evidence_spans IS NOT NULL AND jsonb_array_length(evidence_spans) > 0 THEN 1 END) as has_evidence,
            COUNT(CASE WHEN EXISTS (
                SELECT 1 FROM jsonb_array_elements(evidence_spans) elem
                WHERE (elem->>'verified')::boolean = true
            ) THEN 1 END) as has_verified,
            ROUND(AVG(jsonb_array_length(COALESCE(evidence_spans, '[]'::jsonb)))::numeric, 1) as avg_spans,
            ROUND(AVG(
                (SELECT AVG(length(elem->>'text')) FROM jsonb_array_elements(evidence_spans) elem)
            )::numeric, 0) as avg_span_len
        FROM extractions
        GROUP BY extraction_type
    """)

    for r in rows:
        t = r["extraction_type"]
        se = SUPABASE["evidence"].get(t, {})

        local_verified_pct = round(r["has_verified"] / r["total"] * 100, 1) if r["total"] else 0
        supa_verified_pct = se.get("verified_pct")

        compare_bar(f"  {t} verified %", local_verified_pct, supa_verified_pct, "%")
        compare_bar(f"  {t} avg spans", float(r["avg_spans"]) if r["avg_spans"] else 0,
                     se.get("avg_spans"))
        compare_bar(f"  {t} avg span len", int(r["avg_span_len"]) if r["avg_span_len"] else 0,
                     se.get("avg_span_len"), " chars")

    # ── 6. Model distribution ────────────────────────────────────────────
    print("\n  ◆ MODELS USED")

    rows = run_query(engine, """
        SELECT model_id, COUNT(*) as c,
            ROUND(AVG(confidence_score)::numeric, 3) as avg_conf
        FROM extractions GROUP BY model_id ORDER BY c DESC
    """)
    print(f"  {'Model':<45} {'Count':>8}  {'Avg Conf':>8}")
    print(f"  {'─'*45} {'─'*8}  {'─'*8}")
    for r in rows:
        print(f"  {r['model_id'] or 'unknown':<45} {r['c']:>8}  {r['avg_conf'] or 'N/A':>8}")
    print(f"\n  Supabase used: {SUPABASE['model']} (all {SUPABASE['total_extractions']:,} extractions)")

    # ── 7. Jurisdiction coverage comparison ──────────────────────────────
    print("\n  ◆ TOP JURISDICTIONS (by extraction count)")

    rows = run_query(engine, """
        SELECT s.jurisdiction_code, s.jurisdiction_name,
            COUNT(DISTINCT e.id) as extractions,
            COUNT(DISTINCT nsr.id) as passages
        FROM sources s
        JOIN document_families df ON df.source_id = s.id
        JOIN document_versions dv ON dv.family_id = df.id
        JOIN normalized_source_records nsr ON nsr.document_version_id = dv.id
        LEFT JOIN extractions e ON e.source_record_id = nsr.id
        GROUP BY s.jurisdiction_code, s.jurisdiction_name
        HAVING COUNT(DISTINCT e.id) > 0
        ORDER BY extractions DESC
        LIMIT 15
    """)
    print(f"  {'State':<6} {'Name':<20} {'Extractions':>12}  {'Passages':>10}")
    print(f"  {'─'*6} {'─'*20} {'─'*12}  {'─'*10}")
    for r in rows:
        print(f"  {r['jurisdiction_code']:<6} {r['jurisdiction_name']:<20} {r['extractions']:>12}  {r['passages']:>10}")

    # ── 8. Triage stats (new pipeline feature) ───────────────────────────
    print("\n  ◆ TRIAGE (new pipeline only — not in old sync)")

    rows = run_query(engine, """
        SELECT decision, method, COUNT(*) as c
        FROM section_triage_results
        GROUP BY decision, method
        ORDER BY c DESC
    """)
    if rows:
        print(f"  {'Decision':<15} {'Method':<20} {'Count':>8}")
        print(f"  {'─'*15} {'─'*20} {'─'*8}")
        for r in rows:
            print(f"  {r['decision']:<15} {r['method']:<20} {r['c']:>8}")
    else:
        print("  (no triage results)")

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 85)
    print("  LEGEND:  ▲ = local is better   ▽ = supabase was better   = = same")
    print("=" * 85)


if __name__ == "__main__":
    main()
