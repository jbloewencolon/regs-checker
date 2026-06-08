# Run-1 Unified Plan — Run Integrity + Vocabulary Loop + Quality

**Supersedes/extends:** [`extraction_run_remediation_plan.md`](./extraction_run_remediation_plan.md) (R0–R4, C-1…C-8)
**Unifies:**
- [`extraction_run_corrections_eng.md`](./extraction_run_corrections_eng.md) — run-integrity corrections (C-1…C-8)
- [`code_update_strategy_eng.md`](./code_update_strategy_eng.md) — code-update strategy (Workstreams A + B)
- [`vocab_harvest_spec_eng.md`](./vocab_harvest_spec_eng.md) — vocabulary self-improvement loop (D-1…D-4)
- `data/lookups/candidates/*.csv` — harvest starter artifacts
- **E-1** — evidence-span verbatim-quoting fix (this plan's addition; the missing confidence lever)

**Run baseline:** Extraction 2026-05-10 → 2026-05-11
**Status legend:** ✅ done · 🔧 in progress · ⏳ ready, not started · 🔒 gated (external/dependency)

---

## 0. The thesis

The Run-1 learnings split into two streams that **must be sequenced together**, not
landed independently:

- **Stream A — Run integrity (C-1…C-8):** make the run *complete and trustworthy*.
  Mostly resolved already (R0–R4).
- **Stream B — Vocabulary loop (D-1…D-4):** make each run *feed the next* by
  harvesting agent-emitted vocabulary into lookups, prompts, and fixtures.

Plus one lever neither stream addresses:

- **E-1 — Evidence-span verbatim quoting:** the dominant cause of the 88% C/D
  confidence skew is that the model **paraphrases** evidence spans instead of
  verbatim-quoting them, so `_verify_evidence_spans()` (exact string match) fails on
  41% of extractions. The vocab loop (D-3) fixes *vocabulary* compliance but does
  **nothing** for evidence grounding (a separate 0.20 weight). This is the single
  highest-leverage fix and it has no home in either source doc — so it is a
  first-class phase here.

Three couplings drive sequencing:
1. **C-7 *is* the D-1/D-2 machinery.** One `data/lookups/` directory, one normalization loader — built once.
2. **C-1 gates part of B.** `applicability_agent` fields can't be harvested until the bill-level layer is real (now resolved).
3. **C-2 and reproducibility pinning** are the same discipline (`_prompt_hash`) on two surfaces.

### Two blockers this plan surfaces that the source docs missed

- **Actor target vocab is 4 codes, not 6.** `dim_actor_types.csv` = `Deployer,
  Developer, Provider, Distributor`. The harvest spec assumes 6 (`operator`,
  `compute_provider` absent). D-2 must **extend the dim table**, not just write a
  lookup. The privacy-actor axis (`controller`/`processor`/`business`/`person` =
  82% of obligation volume) is a VC+LKA *schema* decision.
- **`modality_to_strength` has no target dim table.** A strength vocab home must be
  created before even the "clean" fast-lane map can land.

---

## 1. Dependency graph

```
 Phase 0 (residuals + relocate map) ─┐
 Phase 2 (E-1 evidence spans) ───────┼─ run NOW, independent ──┐
 Phase 3 (D-1 harvest job) ──────────┘                         │
 Phase 1 (C-3/C-8 coverage backfill) ─ independent ────────────┤
                                                               ▼
 Phase 4 (D-2 ratify) 🔒 privacy-actor VC decision + dim extend │
        │  └─ 4.1 modality fast-lane (no actor gate) ───────────┤
        ▼                                                       │
 Phase 5 (D-3 prompt enums + validation) ──────────────────────┤
 Phase 6 (D-4 fixtures + eval baseline) ─ partly now ───────────┤
 Phase 7 (C-7 normalization loader + rollup) ──────────────────┤
                                                               ▼
 Phase 8 — Track 3.F quality-improved re-extraction (HARD GATE)
        requires: Phase 2 (E-1) + Phase 4 (D-2) + Phase 5 (D-3) + Phase 6 (D-4)
```

---

## Phase 0 — Close run-integrity residuals & relocate the map

**Objective:** finish what R0–R4 left open and lay the `data/lookups/` foundation.

| # | Task | Driver | Status |
|---|---|---|---|
| 0.1 | Verify the 472 `bill_level_extractions` rows exist in DB (incl. `applicability_agent`); spot-check sample (NLP+RPR) | C-1 residual | ⏳ (needs Docker/psql) |
| 0.2 | Reconcile C-2 token telemetry residual — investigate monitor accumulation across sessions (`run_summary` vs `agent_stats` ~4×) | C-2 | ⏳ (log analysis) |
| 0.3 | ✅ Relocate `config/agent_type_map.json` → `data/lookups/agent_to_extraction_type.json`; add `data/lookups/README.md` | C-7 + unification | ✅ done |
| 0.4 | Reconcile model-of-record: `CLAUDE.md` says `gpt-oss-20b`, `config/agent_models.json` says `gemma-4-26b-a4b`. Pin one before any `_prompt_hash`-derived artifact is committed | adjacent | ⏳ |

**Done in code already (R0–R4 recap):** C-1 export fix (`run_archiver._export_bill_level_extractions`), C-4 jurisdiction-skip counter, C-7 map. C-6 closed.

---

## Phase 1 — Coverage backfill (C-3, C-8)

**Objective:** account for all 232 authoritative laws; fix bad/thin laws.

| # | Task | Driver | Status |
|---|---|---|---|
| 1.1 | Seed 135 `text_ready` laws → ingest → triage → extract (`docs/missing_laws_ingest_queue.csv`) | C-3 | ⏳ owners DO/BE |
| 1.2 | Re-fetch statutory text for 8 BAD_TEXT laws — **SB 205 (Colorado AI Act) highest priority** | C-8 | ⏳ |
| 1.3 | Re-run obligation agent on 2 GENUINE_MISS laws (`TMP-CA-AICALIFORNIACO`, `TMP-MO-ANDRELATEDOFFE`) | C-8 | ⏳ |
| 1.4 | Inspect 6 DB-only laws in `normalized_source_records` (AB 2602, HB 4762, HB178, SB 1361, SB 20, SB25) | C-8 | ⏳ |
| 1.5 | Confirm `enforcement_status` derived-field design with SDPA/LKA after bill-level export verified | C-8 | 🔒 |

---

## Phase 2 — Evidence-span verbatim quoting (E-1) ★ highest value

**Objective:** raise the evidence-grounding component (0.20 weight) by making the
model copy statutory text verbatim. Directly attacks the 41% zero-grounding rate.

| # | Task | Status |
|---|---|---|
| 2.1 | Add explicit verbatim-quote instructions to `prompts/obligation.yml`, `rights_protection.yml`, `definition_actor.yml`, `compliance_mechanism.yml` — "copy-paste exact statutory text into evidence spans; do not summarize/paraphrase" | ⏳ |
| 2.2 | Capture eval-harness baseline (verified-span rate + A/B/C/D distribution) **before** 2.1 lands (ties to Phase 6) | ⏳ |
| 2.3 | Run a 10–20 law test batch with suffixed `agent_name` (`_v2`); measure verified-span rate and A+B lift | ⏳ |
| 2.4 | Audit Orrick-alignment distribution on non-gated laws (secondary C-5 cause; scores live in DB, not CSV) | ⏳ |

**Gate:** if 2.3 raises A+B to ≥30–40% on the test batch, Track 3.F (Phase 8) is
justified at full scope; if not, evaluate Gemma 4 26B against an alternative model
before committing GPU budget.

---

## Phase 3 — Vocabulary harvest job (D-1)

**Objective:** the reusable, prompt-pinned harvester. Unblocked now.

| # | Task | Status |
|---|---|---|
| 3.1 | Build `src/scripts/harvest_vocab.py` — per-field, tier-stratified value distributions; pinned to `_prompt_hash`/`_template_version` (machinery exists in `base.py`/`prompt_loader.py`) | ⏳ |
| 3.2 | Validate: running it on the Run-1 export reproduces `candidates/subject_to_actor_code_candidates.csv` and `modality_to_strength_candidates.csv` | ⏳ |
| 3.3 | Harvest fields: `subject_normalized`, `modality` (obligation); `action` verb heads; preemption phrasing. `applicability_agent` fields wait for Phase 1 re-run | ⏳ |

---

## Phase 4 — Vocabulary ratification (D-2) 🔒

**Objective:** turn candidate CSVs into committee-ratified `data/lookups/*.json`.

| # | Task | Status |
|---|---|---|
| 4.1 | **FAST LANE — `modality_to_strength`:** create a strength vocab home (no dim table exists), commit 8 auto-mapped rows, queue 5 `REVIEW` (liability phrasings). No actor gate | ⏳ ready |
| 4.2 | **GATE — privacy-actor decision (VC+LKA):** rule on `controller`/`processor`/`business`/`person` (82% of volume); **extend `dim_actor_types` beyond its current 4 codes** (add `operator`, `compute_provider`, privacy axis) | 🔒 external |
| 4.3 | Ratify top-44 actor values (80% coverage) → `data/lookups/subject_to_actor_code.json`; long tail → `vocab_review_queue` | 🔒 after 4.2 |
| 4.4 | Add `VocabReviewQueueItem` model + table (`field_name, original_value`); the committee inbox for unmapped vocab + validation mismatches | ⏳ |

---

## Phase 5 — Prompt enums + parse-time validation (D-3)

**Objective:** put ratified codes in the prompt; validate output, queue mismatches.
Depends on Phase 4 (needs ratified codes).

| # | Task | Status |
|---|---|---|
| 5.1 | Inject approved enum lists inline into agent prompts | 🔒 after 4.3 |
| 5.2 | Parse-time validation against `dim_*` codes → route mismatches to `vocab_review_queue` (never silently accept) | 🔒 after 4.4 |
| 5.3 | Disambiguation examples for conflated values (e.g. the `controller/processor` hedge → pick one or flag genuine dual-role) | 🔒 after 4.3 |

---

## Phase 6 — Gold-standard fixtures + eval harness (D-4)

**Objective:** a measurable pre/post baseline. `tests/fixtures/gold_standard/`
already exists (CA SB1047, CO SB205) — extend it.

| # | Task | Status |
|---|---|---|
| 6.1 | Extend fixtures from the 149-row Tier-A + evidence-span pool | ⏳ partly done |
| 6.2 | **Prioritize** human-corrected Tier-C/D + abstention fixtures (decision-boundary > easy Tier-A wins) — start with `compliance_mechanism` abstentions and `subject_normalized` hedges | ⏳ |
| 6.3 | SB 205 gold fixture is **blocked on Phase 1.2 re-fetch** (current corpus text is truncated/bad) | 🔒 after 1.2 |
| 6.4 | Eval harness produces pre/post accuracy + tier distribution; >10% A→B drop triggers prompt review | ⏳ |
| 6.5 | Idempotency + per-stage unit tests for normalization (known value→code, articles stripped, unmapped→queue, idempotent re-run) | ⏳ (test-coverage agent) |

---

## Phase 7 — Normalization loader + rollup (C-7 unified machinery)

**Objective:** one loader reads all `data/lookups/*` and writes controlled-vocab
columns. Build once; migrate the existing hard-coded maps.

| # | Task | Status |
|---|---|---|
| 7.1 | Create `src/scripts/normalization/` — idempotent stages (`WHERE … IS NULL`, `ON CONFLICT DO NOTHING`); one loader consumes every map | ⏳ |
| 7.2 | Register stage(s) in `src/scripts/rollup_matrix.py`; run first; report matched/unmatched/ambiguous counts | ⏳ |
| 7.3 | Migrate hard-coded maps to consume the lookup: `payload_adapter.py:326-333` (ADAPTER_MAP), `rollup_matrix.py:314` (`preemption_signal` literal) | ⏳ |

---

## Phase 8 — Track 3.F quality-improved re-extraction (HARD GATE)

**Objective:** the heaviest, least-reversible step. **Do not run** until all of:
Phase 2 (E-1 verbatim fix) **and** Phase 4 (D-2 ratified codes) **and** Phase 5
(D-3 prompts+validation) **and** Phase 6 (D-4 baseline) have cleared.

| # | Task | Status |
|---|---|---|
| 8.1 | A/B re-extraction with suffixed `agent_name` (`_v2`) — no `agent_version` column; rollback = delete `LIKE '%_v2'` rows | 🔒 |
| 8.2 | PTPL records the Track 3.F scope decision in the Decisions Log | 🔒 |

---

## Sequencing at a glance

| Phase | Items | Can start | Gated by |
|---|---|---|---|
| **0** | C-1/C-2 residuals, relocate map, model pin | now | — |
| **1** | C-3, C-8 coverage | now | owners |
| **2** ★ | E-1 evidence spans | **now** | — |
| **3** | D-1 harvest | now | — |
| **4** | D-2 ratify | 4.1 now; 4.2+ gated | privacy-actor VC + dim extend |
| **5** | D-3 prompts+validation | after 4 | Phase 4 |
| **6** | D-4 fixtures+eval | partly now; SB205 after 1.2 | Phase 1.2 |
| **7** | C-7 loader+rollup | after lookups exist | Phase 4 |
| **8** | Track 3.F re-extraction | last | Phases 2+4+5+6 |

**Critical path:** Phase 2 + Phase 4 (privacy-actor decision) are the two long
poles. Everything else parallelizes around them. Phase 2 is both highest-value and
unblocked — start there.

**Definition of done:** all C-1…C-8 gates met; `data/lookups/` holds ratified
`agent_to_extraction_type` + `subject_to_actor_code` (top-44) + `modality_to_strength`,
each prompt-pinned; prompts carry enums with parse-time validation; evidence-span
verified rate materially improved; gold-standard + eval baseline exist; Track 3.F
scope recorded.
