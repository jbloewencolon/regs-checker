# Run-1 Unified Plan v2 — Tracker-Grounded Data Quality

**Reframed by:** [`engineering_strategy_v2.md`](./engineering_strategy_v2.md) (trust-model spine; supersedes `code_update_strategy_eng.md`)
**Incorporates:** C-1…C-8 corrections · D-1…D-4 vocab harvest · the ~10-code actor model ([`actor_taxonomy_analysis.md`](./actor_taxonomy_analysis.md), [`data/lookups/candidates/actor_value_to_code_full.csv`](../data/lookups/candidates/actor_value_to_code_full.csv)) · E-1 evidence-span fix · this session's verification-layer code investigation
**Status legend:** ✅ done · 🔧 in progress · ⏳ ready · 🔒 gated

> **What changed from v1 of this plan.** v1 sequenced "fix the run" (Phases 0–2) and
> "migrate taxonomy" (Phases 3–8) as parallel tracks ending in a Track-3.F re-extraction.
> v2 collapses them into **one pipeline with a trust spine**: extract → link every fact to
> a source → normalize to a shared taxonomy → **compare to Orrick/IAPP** → match=`tracker_grounded`,
> disagree=flag-for-human, neither=`ungrounded` → **recompute confidence after the comparison.**
> The product layer (law cards, applicability engine, API) is **deferred**. v1 Phases 0–2
> are not wasted — they map into WS-A/B below.

---

## 1. The trust model (the spine)

1. The AI extracts the law's contents.
2. **Every fact links to a source** — a tracker entry or a verified evidence span.
3. Normalize the extraction to a shared controlled vocabulary (WS-B).
4. **Compare** the normalized extraction to Orrick **and** IAPP (WS-C).
5. Match → `tracker_grounded`. Disagree → flag for human (WS-D). Neither tracker → `ungrounded`, shown only if labeled.
6. **Confidence is recomputed *after* this comparison** — not fixed at extraction time.

The 88% Tier-C/D skew is most likely a *symptom* of steps 4–6 not fully running. **This
session's code investigation confirms that hypothesis in part** (see §2).

---

## 2. Verification-layer reality check (resolves Strategy v2 §9.1 — build vs. fix)

Strategy v2 asked: *does the alignment/verification layer exist at all?* I traced the code.
**Answer: built but partly disconnected.** WS-C1 is therefore mostly **"fix," not "build,"**
with one genuine "build" (IAPP).

| Component | State | Evidence |
|---|---|---|
| **Orrick alignment** | ✅ **works** — runs every extraction, feeds the 0.30 weight, hard-gates Tier D | `src/core/orrick_validation.py:80-177`; called `extractor.py:1179,2122,2534`; gate `confidence.py:166-195` |
| **Cross-validation agent** | ⚠️ **exists but orphaned** — runs *post*-extraction, writes `Extraction.metadata_["cross_validation"]`, but the score is **never passed back** into `compute_confidence` | agent `src/agents/cross_validation.py`; called `extractor.py:3026`; persisted `extractor.py:3050-3061`; **never** passed at the 3 confidence call sites |
| **`cross_validation_score` (0.25 weight)** | ❌ **dead** — always `None` → excluded from the denominator → **25% of the confidence model is currently absent** | `confidence.py:74,160-161`; no call site passes it |
| **Gap detector** | ⚠️ exists, post-hoc, not integrated into confidence or re-extraction | `src/agents/gap_detector.py` |
| **IAPP** | ❌ **not built** — CSV sits in `static/iapp_law_tracker.csv`, used only as status metadata; no ingestion, no alignment agent | `static/iapp_law_tracker.csv`; `src/ingestion/legacy/iapp_scraper.py` (unused) |
| **`verification_results` table** | ❌ absent — results live ephemerally in `metadata_` | `src/db/models.py` (no such table) |

**Why this matters for the confidence skew:** the dead 0.25 cross-validation weight isn't a
*penalty* (it's excluded from the denominator when `None`), but wiring it in adds a
**0.25-weight positive signal that is currently missing** — exactly the "fix the comparison
and confidence cleans up for free" effect v2 predicts. Combined with E-1 (evidence grounding,
0.20) this addresses **0.45 of the weight model** that is presently under-delivering.

---

## 3. ⚠️ Open contradiction to resolve before WS-A1

Strategy v2 **A1 asserts the applicability agent "did not run — confirmed."** My earlier C-1
analysis found **472 `bill_level_extractions` rows** in `run_summary`. These reconcile cleanly
if the 472 are **enforcement_agent + compliance_timeline_agent only** (2 agents × ~236 laws ≈ 472),
with **`applicability_agent` contributing 0**. That is consistent with both claims.

**Action (Phase 0.1, unchanged):** run `SELECT agent_name, COUNT(*) FROM bill_level_extractions
GROUP BY agent_name;`. If `applicability_agent = 0`, WS-A1 is a real extraction pass, not an
export fix. **My C-1 export fix (`_export_bill_level_extractions`) remains a prerequisite** —
it is what makes the applicability rows land in the run folder once A1 runs.

---

## 4. Workstreams

### WS-A — Run integrity & versioning *(enables everything; run now)*

| # | Task | Driver | v1 map | Status |
|---|---|---|---|---|
| A1 | Run bill-level **applicability** across all 232 laws (confirm 0 rows first, §3). Verify migration `k7h3i9j1f612_add_bill_level_extractions` applied. | C-1 | Ph0.1 | ⏳ (needs DB/LM Studio) |
| A2 | **Run versioning (NEW):** add `extraction_runs` table (`run_id, git_sha, prompt_versions, model_config, source_snapshot_hash, summary`); add `run_id` FK to extractions/review/bill-level. Replace destructive full-run purge with run-create + serving-run promotion. | v2 §5.3 | — | ⏳ |
| A3 | ✅ Model-of-record pinned (`gemma-4-26b-a4b`). C-2 root cause documented; add `"scope"` label to `agent_stats.json` writer. C-4 jurisdiction-skip counter done. | C-2,C-4 | Ph0.2/0.4 | 🔧 (label TODO) |
| A4 | Reconcile 138→232 coverage; seed 135 `text_ready` laws; re-fetch 8 BAD_TEXT (SB 205 first; SB_2966 missing entirely). | C-3,C-8 | Ph1 | ⏳ |

### WS-B — Taxonomy normalization *(the substrate; gates the trust check)*

| # | Task | Driver | v1 map | Status |
|---|---|---|---|---|
| B0 | **Pull Orrick/IAPP's own covered-entity vocabulary first** (~1 day). Choose Tier-1 codes to maximize tracker comparability — this re-defines "correct" for B2–B4. | trust model | — | ⏳ owners RPR/LKA |
| B1 | ✅ Harvest done (`actor_value_to_code_full.csv`, 209 values). Build reusable `src/scripts/harvest_vocab.py` pinned to `_prompt_hash`. | D-1 | Ph3 | 🔧 (CSV done; script TODO) |
| B1.5 | **Clean the actor field before mapping (NEW):** ~5% are non-actors/garbled (`INVALID_nonactor` in CSV). Fix at parse layer, re-harvest, so committee maps signal not noise. | data-quality | — | ⏳ |
| B2 | Stand up the **two-tier dim model** (§4 of v2): Tier-1 (~10 actor codes incl. `data_handler`, `regulator`, `individual`, `regulated_entity`, `data_broker`) + Tier-2 descriptive `dim_*` + Tier2→Tier1 lookup. All three DBs. | §4.2 | Ph4 | 🔒 after B0 |
| B3 | Ratify maps via VC (full 209 actor; `modality_to_strength`; `agent_to_extraction_type` ✅). **Defer the 4 LKA forks** (data_handler split, regulator-vs-gov, individual-as-protected, operator-vs-deployer) with data in hand. | D-2,C-7,§4.2 | Ph4 | 🔒 LKA forks |
| B4 | Unified normalization stage in `rollup_matrix.py` reading `data/lookups/*` → canonical IDs; idempotent; mismatches → `vocab_review_queue`. | D-2,C-7 | Ph7 | ⏳ |
| B5 | Inject ratified enums into prompts + parse-time validation against `dim_*`. | D-3 | Ph5 | 🔒 after B3 |
| B6 | **Re-confirm before locking:** re-harvest after A1; lock codes only when two runs agree, pinned to `_prompt_hash`. | reproducibility | — | 🔒 after A1 |

### WS-C — Tracker alignment & verification *(the trust check; #1 priority)*

| # | Task | Driver | v1 map | Status |
|---|---|---|---|---|
| **C0 (E-1)** | ✅ **Evidence-span verbatim quoting** — 4 prompts v1.1. Prerequisite for C5 source-linkage; lifts the 0.20 evidence-grounding weight (41% currently zero-grounded). | E-1 | Ph2.1 | ✅ done; test batch ⏳ |
| C1 | **FIX (not build) — wire cross-validation into confidence.** The agent runs but its score never reaches `compute_confidence` (§2). Pass `cross_validation_score` from `metadata_["cross_validation"]` at the 3 confidence call sites → resurrects the dead 0.25 weight. Make swallowed failures explicit. | v2 §5.1 | — | ⏳ ★ high value |
| C2 | Persist `verification_results` (per-extraction alignment/verification status + score) — new table, currently ephemeral in `metadata_`. | v2 §5.2 | — | ⏳ |
| C3 | **BUILD — IAPP alignment.** Ingest `static/iapp_law_tracker.csv` into DB; add IAPP alignment scoring (mirror `orrick_validation.py`); emit `tracker_grounded`/`iapp_grounded`/`tracker_conflict`/`ungrounded`. Refine Orrick gate so IAPP-only laws aren't auto-Tier-D. | v2 §9 | — | ⏳ |
| C4 | **Recompute confidence after** alignment/verification (tracker-grounded weighted model). | v2 §5.2 | — | 🔒 after C1,C3 |
| C5 | Enforce **source linkage**: every served fact carries a tracker ref or verified evidence span, else `ungrounded`. (98% already have spans; E-1/C0 raises the *verified* rate.) | trust model | Ph2 | 🔒 after C0 batch |

### WS-D — Minimal human review *(the "flag for a human" requirement)*

| # | Task | Driver | Status |
|---|---|---|---|
| D1 | Analyst-review step + queue (C3 conflicts land here). Reviewer identity from auth context; corrections schema-validated; immutable audit log. Distinct from future legal review. | v2 §5.4 | ⏳ |
| D2 | Review UI surfaces Orrick + IAPP fields, evidence spans, conflict warnings, confidence breakdown. | v2 §9.6 | ⏳ |

---

## 5. Sequencing

```
WS-A  A1 applicability(232) + A2 run-versioning ──┐ (stop destructive runs; populate scope)
                                                  ▼
WS-B  B0 align codes to trackers ─► B1.5 clean field ─► B2/B3 [4 LKA forks = gate] ─► B6 re-confirm
                                                  ▼
WS-C  C0✅ ─► C1 wire cross-val (FIX) ─► C3 IAPP (BUILD) ─► C4 recompute confidence ─► C5 source linkage
                                                  ▼
WS-D  human review for conflicts
                                                  ▼
        DEFERRED: law cards · applicability product · API · productionization
```

- **WS-A first** — applicability must run (A1) and runs must stop being destructive (A2).
- **WS-B before WS-C** — no shared vocabulary, no comparison. Codes chosen against trackers (B0) on a cleaned field (B1.5).
- **WS-C is the priority deliverable.** Within it, **C1 is the cheapest big lever** (re-connect an already-built agent → +0.25 weight). C3 (IAPP) is the one genuine build.
- **No quality re-extraction** until WS-B + B5 prompt enums land (the old "Track 3.F").

## 6. Highest-leverage next actions (unblocked now)

1. **WS-A1 confirm query** — `GROUP BY agent_name` on `bill_level_extractions` to settle the §3 contradiction (decides whether A1 is a re-run).
2. **WS-C1 — wire cross-validation into confidence** — pure code, no committee, resurrects 25% of the confidence model. Highest value/effort ratio in the plan.
3. **WS-C0 test batch** — run the v1.1 verbatim prompts on 10–20 laws (`_v2` suffix); measure verified-span lift.
4. **WS-B1.5 — clean the actor field** — small parse-layer fix; unblocks a clean re-harvest.

## 7. Deferred (confirmed)

Law-card data model, business applicability product, product API, productionization. These sit
on top of clean, tracker-grounded data and resume once §WS-A…D done-criteria hold.

## 8. Open divergences to confirm (Strategy v2 §9, updated)

1. ~~Does the verification layer exist?~~ **Resolved (§2): built but disconnected; C1=fix, C3=build.**
2. **Is IAPP ingested?** No — CSV in `static/`, not in DB. C3 must ingest it first.
3. **Four actor-code forks** — LKA rulings gate WS-B3.
4. **Tracker entity vocabulary** — B0 needs Orrick/IAPP covered-entity categories; confirm accessible.
5. **C-1 contradiction (§3)** — confirm `applicability_agent` row count before sizing A1.
