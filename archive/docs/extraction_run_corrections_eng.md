# Engineering Corrections: Extraction Run 2026-05-10 → 2026-05-11

**Audience:** Engineering team (NLP, BE, DevOps, SDPA)
**Source review:** `extraction_run_review_2026-05-11.md`
**Run artifacts:** `run_summary.json`, `agent_stats.json`, `extractions.csv`
**Status of this run for Phase 0:** **NOT CLEARED** — see C-1.

---

## 0. How to use this document

Each correction item below is structured the same way:

- **Symptom** — what the data shows
- **Confirm first** — the diagnostic step before any fix (do not skip; some "fixes" depend on root cause)
- **Corrective action** — what to change
- **Owner** — role responsible
- **Done when** — acceptance check

Items are ordered by priority. **C-1 is blocking** — it gates Phase 0 and therefore Tracks 1.H, 2.B/2.C/2.D, and 3.F. **C-2 through C-8 are non-blocking** but should be resolved before the run is declared production-ready. Tracks 1.A–1.G are unaffected and proceed in parallel.

---

## C-1 — `applicability_agent` did not run *(BLOCKING)*

### Symptom
No `applicability_agent` appears in either telemetry file or the extraction dump. The six agents that ran are `rights_protection`, `definition_actor`, `obligation`, `threshold_exception`, `compliance_mechanism`, and `preemption` — all passage-level. The `applicability_agent` is the **bill-level scope agent** and is the named input for:

- Track 1.H — `law_category` inference (rule-based over `applicability_agent` rows)
- Track 2.C — `law_ai_scopes` from `ai_system_types_in_scope`
- Track 2.D — `law_sectors` from `covered_sectors`
- Track 3.F — re-extraction baseline

Phase 0 is defined as "every law has an `applicability_agent` row in `bill_level_extractions`." That condition is not met.

### Confirm first
Determine which of these is true — the corrective action differs:
1. **It ran in a separate job** and those 472 `bill_level_extractions` rows exist but weren't included in the export. → No re-run needed; export and verify the existing rows.
2. **It was never invoked** in this run (not in the agent roster / `config/agent_models.json` for this pass). → A bill-level extraction pass must be run.
3. **It ran but produced zero rows** (silent abstention / signal-gated out). → Investigate gating before re-running.

Check: `SELECT agent_name, COUNT(*) FROM bill_level_extractions GROUP BY agent_name;` and confirm `applicability_agent` is in the configured agent set for this run.

### Corrective action
- If (1): export `bill_level_extractions` and proceed to the Phase 0 verification spot-check (NLP + RPR).
- If (2) or (3): run the bill-level applicability pass via Dashboard Step 3 against the full corpus (LM Studio + Gemma 4 26B + Docker Postgres :5434). Confirm Alembic migration `l8i4j0k2g713` is applied first.

### Owner
NLP (agent config + run), DevOps (migration/infra), RPR + NLP (verification spot-check)

### Done when
Every in-scope law has an `applicability_agent` row in `bill_level_extractions`, **or** is explicitly accounted for (text-missing → quarantine queue, or Orrick-gated). NLP + RPR spot-check a sample and sign off.

---

## C-2 — Telemetry token/call totals disagree by ~4× *(data integrity)*

### Symptom
`run_summary.json` reports 9,942,470 tokens over 2,043 calls. `agent_stats.json` reports 38,553,380 tokens over 6,726 calls (the per-agent figures sum consistently to this). The two cannot describe the same accounting of the same run.

### Confirm first
Identify what each field counts. Hypotheses: `run_summary` may count only a subset (one phase, or only completed bill-level calls), or one file is from a different/partial run. Check the writer for each field in the run-orchestration code.

### Corrective action
Reconcile to a single authoritative source of truth for tokens and call counts. Fix the incorrect writer (or document explicitly what each field's scope is, if both are intentionally different scopes).

### Owner
BE (telemetry writers), DevOps (run orchestration)

### Done when
A single documented figure for total tokens and total calls per run, with the two files either agreeing or each clearly labeled with its scope. **This matters because Phase 3.F budgets GPU time and cost off `duration_ms` and token figures — a 4× error here mis-sizes the heaviest step in the plan.**

---

## C-3 — Corpus coverage is 138 laws, not the authoritative 232 *(coverage)*

### Symptom
The run touched 138 unique laws across 42 jurisdictions. The strategy doc fixes the authoritative count at **232** (Decisions Log, 2026-05-26). ~94 laws are unaccounted for in this run.

### Confirm first
Is the 138 an intentional batch scope, or did ~94 laws fail to load / get skipped at ingest? Diff the 138 run laws against the 232-law `fact_laws` seed.

### Corrective action
Produce the explicit missing-law list. For each missing law, classify as: intentionally deferred batch, text-missing (quarantine), or ingest gap requiring a fix. Schedule the ingest-gap and deferred-batch laws into a subsequent run.

### Owner
DO (corpus diff), BE (ingest fixes), PTPL (batch scheduling)

### Done when
A reconciled list exists: every one of the 232 laws is either present in extraction output or has a documented reason for absence.

---

## C-4 — 13 passages dropped silently *(telemetry / completeness)*

### Symptom
`agent_stats.passages_processed = 647 / 660`, but `run_summary.records_processed = 660 / 660` with `records_failed = 0`. The 13-passage gap is only visible by diffing the two files — it is **not surfaced as a failure anywhere**.

### Confirm first
Identify the 13 unprocessed passages and why they were skipped (short-text skip, error mid-run, or tail truncation — the run ended on `ND - TMP-ND-AIPOLITICALADV`).

### Corrective action
Two fixes: (a) account for and, if appropriate, re-process the 13 passages; (b) fix the telemetry so partial-passage gaps surface as a non-zero failure/skip count in `run_summary`, not just in `agent_stats`. Silent gaps violate the plan's intent that incomplete states be visible downstream.

### Owner
BE (telemetry), NLP (re-process if needed)

### Done when
`passages_processed` and `records_processed` reconcile, or the gap is reported as an explicit skip count with reasons.

---

## C-5 — Confidence distribution far below baseline *(quality investigation)*

### Symptom
Tier A+B is **11.9%** (A 2.4%, B 9.5%); C+D is **88.1%** (C 50.4%, D 37.7%). The plan's Phase 3 monitoring baseline is **A+B ≥ 70%** (§5.6). Tier banding itself is clean (A ≥ 0.85, B 0.70–0.85, C 0.50–0.70, D < 0.50).

### Confirm first
Determine whether the large D bucket is an **Orrick-Gate artifact** (auto-Tier D when no Orrick validation data exists — a mechanical assignment, not a quality signal) or **genuine model uncertainty**. Cross-tabulate Tier D against Orrick-data availability per law.

### Corrective action
- If Orrick-gating artifact: document it so Tier D is not misread as low quality, and confirm the rule-based crosswalks (1.H, 3.D) tolerate Orrick-gated inputs.
- If genuine uncertainty: this re-sizes Track 3.F from "targeted re-extraction" to ~88% of the corpus. Escalate to PTPL for re-planning before committing GPU budget.

### Owner
NLP + RPR (diagnosis), PTPL (re-planning if needed)

### Done when
The C/D skew has a documented cause, and the Phase 3.F scope estimate is updated accordingly.

---

## C-6 — `compliance_mechanism` abstention rate is an outlier *(agent reliability)*

### Symptom
`compliance_mechanism` abstained on **190 / 952 calls (20.0%)** — ~5× the next-highest agent — and carries the highest error rate (3.3%). Phase 3.D routes its output into the Level-1 crosswalk, where `compliance_mechanism (other)` already feeds a "needs human review" bucket; a 20% abstention rate enlarges that queue.

### Confirm first
Review a sample of the 190 abstentions: are they correct abstentions (passages genuinely without compliance content, correctly signal-gated) or false abstentions (the agent failing to extract present content)?

### Corrective action
If false abstentions are material, revise the compliance_mechanism prompt and/or its signal-gating threshold. Re-run the affected passages. If abstentions are correct, document the expected rate so it isn't flagged as a defect later.

### Owner
NLP (prompt/gating), RPR (sample review)

### Done when
Abstention rate is either reduced to an explainable level or documented as expected, with a sample-validated rationale.

---

## C-7 — Naming drift across agents, extraction types, and the plan *(taxonomy debt)*

### Symptom
Agent names, CSV `extraction_type` values, and plan vocabulary do not align 1:1:

| Agent (`agent_stats`) | CSV `extraction_type` | Notes |
|---|---|---|
| `definition_actor` | `definition` | name mismatch |
| `threshold_exception` | `threshold` (501) + `exception` (474) | one agent → two types |
| `preemption` | `preemption_signal` | name mismatch |
| `obligation` | `obligation` | aligned |
| `rights_protection` | `rights_protection` | aligned |
| `compliance_mechanism` | `compliance_mechanism` | aligned |

### Confirm first
Confirm these mappings are stable across runs (not run-specific drift).

### Corrective action
Write the agent → extraction_type mapping into an explicit, version-controlled lookup that the rollup normalization (Tracks 1.D, 2.B) consumes — rather than letting each normalization stage rediscover it. This is exactly the freetext-vs-controlled-vocab inconsistency the taxonomy redesign exists to remove.

### Owner
BE (rollup normalization), SDPA (vocab alignment)

### Done when
A single mapping artifact exists and rollup stages reference it; no normalization stage hard-codes the agent/type relationship.

---

## C-8 — Sparse / empty laws need triage *(coverage quality)*

### Symptom
- **21 of 138 laws have zero obligation extractions.**
- **6 laws have only a single extraction total.**
- **`enforcement` has only 15 extractions** corpus-wide; Phase 1.G's `enforcement_status` will rely almost entirely on the derived field (effective_date vs now()), with extracted enforcement signal contributing little.

### Confirm first
For the 21 obligation-empty and 6 single-extraction laws: is this correct (the law genuinely has little extractable content) or a processing miss?

### Corrective action
Triage each. Re-process processing misses. For the enforcement sparsity, confirm with SDPA/LKA that the derived-field-dominant design for `enforcement_status` is intended.

### Owner
DO + RPR (triage), NLP (re-process), SDPA + LKA (enforcement design confirmation)

### Done when
Each sparse/empty law is classified as correct-and-accepted or fixed-and-re-run; the enforcement design assumption is confirmed.

---

## Summary table

| ID | Issue | Severity | Owner(s) | Blocking? |
|----|-------|----------|----------|-----------|
| C-1 | `applicability_agent` did not run | Critical | NLP, DevOps, RPR | **Yes** |
| C-2 | Token/call telemetry mismatch (4×) | High | BE, DevOps | No |
| C-3 | 138 of 232 laws covered | High | DO, BE, PTPL | No |
| C-4 | 13 passages dropped silently | Medium | BE, NLP | No |
| C-5 | Confidence skew vs. baseline | Medium–High | NLP, RPR, PTPL | No |
| C-6 | `compliance_mechanism` 20% abstention | Medium | NLP, RPR | No |
| C-7 | Agent/type naming drift | Low (accruing) | BE, SDPA | No |
| C-8 | Sparse/empty laws + enforcement sparsity | Low–Medium | DO, RPR, NLP, SDPA, LKA | No |

*Tracks 1.A–1.G proceed in parallel regardless of the above.*
