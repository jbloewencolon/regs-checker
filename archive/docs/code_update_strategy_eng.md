# Engineering Strategy: Code Updates from Run-1 Learnings

**Audience:** Engineering team (NLP, BE, SDPA, DevOps, DO) + VC
**Inputs (companion docs):**
- `extraction_run_corrections_eng.md` — run-integrity corrections (C-1 … C-8)
- `vocab_harvest_spec_eng.md` — vocabulary self-improvement loop (D-1 … D-4)
**Governing plan:** Taxonomy Redesign Plan + Strategy doc (conventions inherited, not restated)
**Run baseline:** Extraction 2026-05-10 → 2026-05-11

---

## 1. Purpose

The first full extraction run produced two distinct streams of work. This doc unifies them into one code-update strategy so engineers touch each file once with both streams in mind, rather than landing conflicting changes:

- **Workstream A — Run integrity** (from the corrections doc): make the pipeline produce a *complete and trustworthy* run. Blocking item C-1 (`applicability_agent`) plus telemetry, coverage, and reliability fixes.
- **Workstream B — Vocabulary loop** (from the harvest spec): make each run *feed the next*, harvesting agent-emitted vocabulary into lookups, prompts, and fixtures.

The two streams are not independent. Three couplings drive the sequencing in §4:

1. **C-1 gates part of B.** The `applicability_agent` fields (`ai_system_types_in_scope`, `covered_sectors`) can't be harvested (D-1) until the bill-level agent actually runs.
2. **C-7 and D-1/D-2 are the same machinery.** The "naming-drift mapping" (agent → extraction_type) and the "vocab harvest maps" (agent value → controlled code) are both version-controlled mapping artifacts consumed by the same rollup normalization stage. Build one mechanism, not two.
3. **C-2 and D-5 share a constraint.** Telemetry single-source-of-truth (C-2) and prompt-version pinning (`_prompt_hash`, harvest spec §5) are the same reproducibility discipline applied to two surfaces.

---

## 2. Guiding principles for these code changes

Inherited from the plan and binding on every change below:

- **Additive-not-destructive.** New columns/tables/maps coexist with old; nothing is dropped until a change is validated against the live corpus. No destructive migration in this batch.
- **Inference offline, serving instant.** No new runtime LLM dependency. Harvesting, normalization, and validation run in batch; the API serves materialized tables.
- **Three-database parity.** Every schema change applies to local Docker Postgres (:5434), Regs-Checker Supabase, and Policy-Navigator Supabase via the `scripts/apply_pending_migrations.sql` pattern (Supabase can't `ALTER TYPE` in a transaction).
- **Reproducibility pinning.** Any artifact derived from agent output (lookups, fixtures, telemetry baselines) records the `_prompt_hash` / `_template_version` it was derived from. A prompt change invalidates and triggers re-derivation.
- **Structured logging.** Use `structlog`, not `print`, for every new stage.

---

## 3. Code-change inventory

The centerpiece. Each row is a file or module, the change, the driver(s), and owner. **New** marks files that don't exist yet.

### 3.1 Pipeline configuration & orchestration

| File / surface | Change | Driver | Owner |
|---|---|---|---|
| `config/agent_models.json` | Confirm `applicability_agent` is in the configured agent set; if absent, add it (Gemma 4 26B, same as peers). Hot-reload via `/dashboard/models`. | C-1 | NLP |
| Run orchestration / telemetry writers | Reconcile the two token/call accountings into one authoritative source. Either fix the under-counting writer or label each field's scope explicitly. Emit a single `tokens`/`calls` figure per run. | C-2 | BE, DevOps |
| Run orchestration (completeness) | Surface `passages_processed < passages_total` as a non-zero skip/failure count in `run_summary.json` — not only in `agent_stats.json`. Reconcile `records_processed` vs `passages_processed`. | C-4 | BE |
| `alembic/versions/l8i4j0k2g713…` | Confirm applied in all three DBs before the bill-level run (adds `duration_ms`/`input_tokens`/`output_tokens`). | C-1 | DevOps |

### 3.2 Prompts & agents

| File / surface | Change | Driver | Owner |
|---|---|---|---|
| `prompts/obligation.yml` | Inject the VC-ratified controlled-vocab enums (actor codes, obligation strength) inline so the agent sees valid codes. Add disambiguation examples for conflated values (e.g. the `controller/processor` hedge). | D-3 | NLP |
| compliance_mechanism prompt + signal gating | Review the 190 abstentions (20%); if false abstentions are material, revise prompt and/or gating threshold. | C-6 | NLP, RPR |
| Parse-time validation layer | Validate agent output against `dim_*` codes; route mismatches to `vocab_review_queue` instead of silently accepting. (LLMs unreliable at strict enums — plan principle.) | D-3 | NLP, BE |
| `bill_level_extractions` write path | When re-running an improved prompt, suffix `agent_name` (`applicability_agent` → `applicability_agent_v2`) per the plan's A/B convention — no `agent_version` column added. | C-1, D-3 | NLP, BE |

### 3.3 Normalization & lookups (unified C-7 + D machinery)

| File / surface | Change | Driver | Owner |
|---|---|---|---|
| `src/scripts/harvest_vocab.py` **(new)** | Reusable harvest job: per-field, tier-stratified value distributions for each agent's classification fields. Pins output to `_prompt_hash`/`_template_version`. Reproduces the two starter CSVs. | D-1 | BE |
| `data/lookups/` **(new dir if absent)** + `README.md` | Home for all maps. Add: `agent_to_extraction_type.json` (C-7), `subject_to_actor_code.json` (D-2), `modality_to_strength.json` (D-2). Each file header records the pinned prompt version. | C-7, D-2 | DO, BE |
| `src/scripts/normalization/` **(new module)** | Normalization stages that read `data/lookups/*` and write controlled-vocab columns. Idempotent (`WHERE … IS NULL`, `ON CONFLICT DO NOTHING`). One loader consumes all maps — the naming-drift map and the vocab maps go through the same code. | C-7, D-2 | BE |
| `src/scripts/rollup_matrix.py` | Register the new normalization stage(s); run first in the rollup order. Report matched/unmatched/ambiguous counts. | C-7, D-2 | BE |
| `src/db/models.py` | Add `VocabReviewQueueItem` (keyed by `field_name, original_value`) if not yet present; it is the committee inbox for both unmapped vocab and validation mismatches. | D-3 | BE, SDPA |

### 3.4 Coverage & data triage

| File / surface | Change | Driver | Owner |
|---|---|---|---|
| Corpus diff script **(new / one-off)** | Diff the 138 run-laws against the 232-law `fact_laws` seed; emit the missing-law list classified as deferred / text-missing / ingest-gap. | C-3 | DO |
| Ingest path | Fix any ingest-gap laws surfaced by the diff; schedule deferred batches. | C-3 | BE, PTPL |
| Orrick-gate instrumentation | Cross-tab Tier D against Orrick-data availability; label Orrick-gated D distinctly from genuine low-confidence so the C/D skew isn't misread. | C-5 | NLP, RPR |
| Sparse-law triage | Classify the 21 obligation-empty and 6 single-extraction laws as correct-and-accepted or fix-and-rerun. Confirm enforcement-status derived-field design with SDPA/LKA. | C-8 | DO, RPR, SDPA, LKA |

### 3.5 Tests & fixtures

| File / surface | Change | Driver | Owner |
|---|---|---|---|
| `tests/fixtures/gold_standard/` | Build baseline fixtures from Tier-A + evidence-span rows (149 available); prioritize human-corrected Tier-C/D + abstention fixtures for the decision boundary. | D-4 | NLP, RPR |
| Eval harness | Produce a pre-change accuracy baseline so the D-3 prompt update is measurable. Track A/B/C/D distribution before/after. | D-4 | NLP |
| `tests/unit/test_normalize_*.py` | Unit tests per normalization stage: known value → expected code, articles stripped, unmapped → review queue, idempotent re-run. | C-7, D-2 | BE |

---

## 4. Sequencing

```
                ┌─ C-2 telemetry SoT ─┐
C-1 applicability run ─┼─ C-4 surface skips ─┼─► clean Run-2 baseline
   (BLOCKING)          └─ C-3 coverage diff ─┘            │
        │                                                 │
        └─► D-1 harvest (incl. applicability fields) ◄────┘
                        │
        C-7 ┐           ▼
            ├─► data/lookups/* ──► D-2 VC ratification ──┐
   (naming) ┘   (unified maps)    (privacy-actor decision │ gates D-2)
                                                          ▼
                              D-3 prompt enums + validation ──┐
                                          ▲                   │
                              D-4 gold-standard fixtures ─────┘
                                          │
                                          ▼
                        Track 3.F quality-improved re-extraction
                              (do NOT run before D-2 + D-3 + D-4)
```

Read in order:

1. **C-1 first, always.** It is blocking and it gates the applicability-field harvest. Land the agent-config fix, verify the migration, run the bill-level pass.
2. **Run-integrity fixes (C-2, C-3, C-4) in parallel** with C-1 — they touch telemetry/orchestration/ingest, not the agent run, so they can land while the bill-level extraction executes.
3. **D-1 harvest** runs against the now-complete output (passage fields immediately from Run-1; applicability fields once C-1's run lands).
4. **C-7 folds into D's lookup machinery** — build the unified `data/lookups/` + normalization loader once.
5. **D-2 ratification** is gated on the **privacy-actor decision** (VC + LKA): the harvest showed `controller`/`processor`/`person`/`business` dominate and don't fit the 6-code model. Resolve before finalizing the lookup.
6. **D-3 + D-4 together** — prompt enums need the ratified codes (D-2) and an eval baseline (D-4) to measure against.
7. **Track 3.F re-extraction last.** Running it before D-2/D-3/D-4 spends GPU on un-improved prompts — the heaviest, least-reversible step in the plan.

Diagnostic/triage items (C-5, C-6, C-8) run opportunistically; none block the critical path but each feeds prompt/design decisions in D-3 and Phase 1.G.

---

## 5. Testing & rollback

Inherit the plan's per-phase patterns; specifics for this batch:

- **Idempotency is the load-bearing test.** Every normalization stage must produce zero changes on a second run. Assert it in unit tests and in a CI data-quality check.
- **Eval-harness baseline before prompt changes.** Capture A/B/C/D distribution and gold-standard accuracy *before* D-3 lands, so a regression is detectable. Plan threshold: > 10% A→B drop triggers prompt review.
- **A/B store for re-extraction.** New-prompt rows carry suffixed `agent_name`; rollback = delete suffixed rows, revert rollup queries to drop the `LIKE '%_v2'` filter. Original rows untouched.
- **Lookup rollback.** Lookups are version-controlled JSON; revert the file and re-run the idempotent normalization. No schema change to unwind.
- **Telemetry/orchestration.** Pure additive logging changes; revert the writer if a metric misbehaves.

No change in this batch drops a column or table — rollback is file-revert or row-delete throughout.

---

## 6. Risks specific to this code update

| Risk | Mitigation |
|---|---|
| Privacy-actor decision stalls D-2 → blocks the whole vocab loop | Time-box the VC decision; ship the top-44 supply-chain + auto-mapped values first, queue privacy roles as a fast-follow |
| C-7 and D-2 built as separate mechanisms → duplicate, drifting lookup loaders | Enforce the unified `src/scripts/normalization/` loader in review; reject a second ad-hoc map reader |
| Re-extraction (3.F) run before prompts improved → wasted GPU + tier instability | Hard gate in §4: 3.F PR blocked until D-2/D-3/D-4 acceptance |
| Harvest pooled across prompt versions → trains next prompt on stale vocab | `_prompt_hash` pinning in `harvest_vocab.py`; segment, never pool |
| Three-DB drift during lookup/column adds | `sync_monitor.py` parity check after each migration; run in low-traffic window |

---

## 7. Definition of done for this batch

- `applicability_agent` runs and `bill_level_extractions` clears the Phase-0 verification gate (C-1).
- A single authoritative tokens/calls figure per run; passage skips surfaced (C-2, C-4).
- 232-law coverage reconciled (C-3); C/D skew cause documented (C-5).
- `data/lookups/` holds VC-ratified `agent_to_extraction_type`, `subject_to_actor_code` (top-44), `modality_to_strength`, each prompt-version-pinned (C-7, D-2).
- Agent prompts carry ratified enums with parse-time validation routing to `vocab_review_queue` (D-3).
- Gold-standard fixtures + eval baseline exist; all normalization stages idempotent and unit-tested (D-4).
- Track 3.F is unblocked but not yet run; it proceeds on the next planning gate.
