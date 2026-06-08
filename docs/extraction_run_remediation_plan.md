# Extraction-Run Remediation Plan

**Subject run:** 2026-05-10 → 2026-05-11 (`output/extraction_runs/`)
**Drives:** the eight corrections in [`extraction_run_corrections_eng.md`](./extraction_run_corrections_eng.md) (C-1…C-8)
**Relationship to the redesign:** this plan **gates and feeds**
[`taxonomy_dev_plan.md`](./taxonomy_dev_plan.md). It does not replace it. C-1 *is* that
plan's Phase 0 prerequisite; the rest must clear before the run is "production-ready" and
before the budget-sensitive tracks (1.H, 2.B/2.C/2.D, 3.F) commit GPU time.
**Status legend:** ⛔ blocker · ◐ enabler · ○ hygiene

---

## 1. The core idea

The eight issues are **not** a flat to-do list — they have a dependency spine:

> You cannot **size** the work (C-5 scope, C-3 backfill, C-8 re-runs) until you can
> **trust the measurements** (C-2 telemetry, C-4 silent gaps). And you cannot let the
> taxonomy redesign proceed at all until the **bill-level layer exists** (C-1).

So the plan sequences by *what unblocks what*, not by raw severity. Cheap measurement
fixes come early precisely because every later sizing decision depends on them. Five
phases, R0–R4. R0 and R1 can run concurrently; R2–R4 depend on R1's trustworthy baseline.

```
R0 ⛔ Unblock        C-1  ──────────────┐ (gates the redesign's Phase 0 + Tracks 1.H/2.x/3.F)
R1 ◐ Trust the data  C-2, C-4 ──────────┤
                                         ▼
R2 ◐ Close coverage  C-3, C-8 ───────────┐
R3 ◐ Diagnose quality C-5, C-6 ──────────┤→ GO/NO-GO on re-extraction scope (PTPL)
R4 ○ Lock taxonomy    C-7 ───────────────┘ (feeds redesign Tracks 1.D, 2.B, 3.D)
```

---

## 2. Phase R0 — Unblock the redesign *(blocker)*

**Objective:** make the bill-level layer real, because the entire scope/sector/category
half of the redesign reads from it.

| Item | What | Exit gate |
|---|---|---|
| **C-1** ⛔ | Diagnose why `applicability_agent` has zero rows in this run (ran-but-not-exported / never-invoked / abstained-out), then export or re-run the bill-level pass | Every in-scope law has an `applicability_agent` row in `bill_level_extractions`, **or** is explicitly accounted for (text-missing → quarantine; Orrick-gated). NLP + RPR sign off a sample. |

**Sequencing note — do the diagnosis before any re-run.** The three root causes have very
different costs: branch (1) is a 10-minute export; branches (2)/(3) are a multi-hour
bill-level pass on the full corpus. Run the SQL check
(`SELECT agent_name, COUNT(*) FROM bill_level_extractions GROUP BY agent_name;`) and
confirm the agent roster in `config/agent_models.json` *first*.

> ⚠️ **Correction to the eng doc, fold in before executing:** C-1's remediation says
> "confirm Alembic migration `l8i4j0k2g713` is applied." That migration is
> `add_duration_ms_to_extractions`. The migration that creates the bill-level table is
> **`k7h3i9j1f612_add_bill_level_extractions`** — that is the one that must be applied
> before a bill-level pass can write rows. Apply both; `k7h3i9j1f612` is the gating one.

**Owners:** NLP (agent config + run), DevOps (migrations/infra), RPR (verification)
**Dependencies:** none — start immediately, in parallel with R1.
**Est. effort:** 0.5 day if branch (1); 1–2 days incl. a corpus pass if branch (2)/(3).

---

## 3. Phase R1 — Make the numbers trustworthy *(enabler, do concurrently with R0)*

**Objective:** before anyone sizes coverage or re-extraction, the telemetry must mean what
it says. These are cheap and unblock every downstream estimate.

| Item | What | Exit gate |
|---|---|---|
| **C-2** ◐ | Reconcile the ~4× token/call disagreement (`run_summary` 9.94M/2,043 vs `agent_stats` 38.55M/6,726). Find each writer in the run orchestration; pick one authoritative source or label each file's scope. | One documented per-run figure for tokens and calls; the two files agree or are each labeled. |
| **C-4** ◐ | Surface the 13-passage silent drop (`agent_stats` 647/660 vs `run_summary` 660/660, 0 failed). Identify the 13 (short-text skip / mid-run error / tail truncation at `ND - TMP-ND-AIPOLITICALADV`); fix telemetry so partial gaps appear as a non-zero skip/fail count in `run_summary`. | `passages_processed` and `records_processed` reconcile, or the gap is an explicit reported skip count with reasons. |

**Why first:** C-5's re-extraction scope and C-3's backfill are both *budgeted off token and
`duration_ms` figures*. A 4× telemetry error (C-2) mis-sizes the heaviest step in the whole
redesign (Track 3.F). C-4's silent drops mean "0 failed" is currently untrustworthy, which
corrupts C-3 and C-8 triage ("is this law missing, or silently skipped?").

**Owners:** BE (telemetry writers), DevOps (orchestration), NLP (re-process the 13 if needed)
**Dependencies:** none.
**Est. effort:** 1–2 days combined (mostly code-reading the writers).

---

## 4. Phase R2 — Close the coverage gap *(enabler)*

**Objective:** account for every one of the 232 authoritative laws.

| Item | What | Exit gate |
|---|---|---|
| **C-3** ◐ | Diff the 138 run laws against the 232-law `fact_laws` seed. Classify the ~94 absentees: intentional batch / text-missing (quarantine) / ingest gap. Schedule ingest-gap + deferred laws into a follow-up run. | A reconciled list: every law of 232 is present in output or has a documented reason for absence. |
| **C-8** ◐ | Triage the 21 zero-obligation laws and 6 single-extraction laws: genuine thin content vs processing miss. Re-process misses. Confirm with SDPA/LKA that the derived-field-dominant `enforcement_status` design (only 15 `enforcement` extractions corpus-wide) is intended. | Each sparse/empty law is classified correct-and-accepted or fixed-and-re-run; enforcement design confirmed. |

**Why after R1:** distinguishing "intentionally deferred" from "silently dropped" requires
the C-4 fix — otherwise a skipped law looks identical to an absent one. C-3 (whole-law
absence) and C-8 (present-but-thin laws) are the same triage muscle at two granularities;
run them together to reuse the diff tooling.

**Owners:** DO (corpus diff + triage), BE (ingest fixes), PTPL (batch scheduling), RPR (triage), SDPA + LKA (enforcement design)
**Dependencies:** R1 (C-4) for trustworthy skip accounting. Feeds redesign Track 1.H.
**Est. effort:** 2–3 days + one follow-up extraction run for the backfill batch.

---

## 5. Phase R3 — Diagnose quality and size re-extraction *(enabler — contains the key GO/NO-GO)*

**Objective:** explain the confidence skew and agent reliability, then make the single
biggest budget decision in the redesign.

| Item | What | Exit gate |
|---|---|---|
| **C-5** ◐ | Cross-tabulate Tier D against Orrick-data availability per law. Decide: **mechanical Orrick-Gate artifact** (auto-Tier-D when no Orrick validation exists — not a quality signal) vs **genuine model uncertainty**. | The 88% C/D skew has a documented cause and the Track 3.F scope estimate is updated. |
| **C-6** ◐ | Sample the 190 `compliance_mechanism` abstentions (20%, ~5× the next agent): correct abstentions vs false negatives. Tune prompt/gating threshold if false negatives are material; else document the expected rate. | Abstention rate reduced to an explainable level or documented as expected, sample-validated. |

> ### 🚦 Decision gate (PTPL owns)
> C-5's outcome forks the redesign's most expensive track:
> - **Orrick-Gate artifact** → Track 3.F stays "targeted re-extraction"; cost stays small.
> - **Genuine uncertainty** → Track 3.F balloons to ~88% of the corpus. **Do not commit
>   GPU budget until this fork is resolved.** This is why R3 sits before the redesign's
>   Phase 3 and why C-2's corrected token figures (R1) are a hard prerequisite here.

**Owners:** NLP + RPR (diagnosis), PTPL (re-planning + the gate decision)
**Dependencies:** R1 (C-2 corrected costs). Gates redesign Track 3.F.
**Est. effort:** 2–3 days diagnosis; the gate decision is a meeting, not engineering.

---

## 6. Phase R4 — Lock the taxonomy mapping *(hygiene, but do it before redesign normalization)*

**Objective:** stop every normalization stage from re-discovering the agent→type mapping.

| Item | What | Exit gate |
|---|---|---|
| **C-7** ○ | Confirm the agent→`extraction_type` mapping is stable across runs, then write it into one version-controlled lookup (e.g. `config/agent_type_map.json`) that rollup normalization consumes. Encodes the known fan-out: `definition_actor`→`definition`; `threshold_exception`→`threshold`+`exception`; `preemption`→`preemption_signal`. | A single mapping artifact exists; no normalization stage hard-codes the relationship. |

**Why last but not skippable:** it's low-urgency for *this* run but is a direct prerequisite
for redesign Tracks 1.D and 2.B (rollup normalization) and 3.D (Level-1 crosswalk). Landing
it now prevents the same drift being hard-coded three times.

**Owners:** BE (rollup normalization), SDPA (vocab alignment)
**Dependencies:** none technically; sequence before redesign Phase 1 Track 1.D.
**Est. effort:** 0.5–1 day.

---

## 7. Sequencing & ownership at a glance

| Phase | Items | Gate it clears | Blocks redesign? | Primary owners |
|---|---|---|---|---|
| **R0** ⛔ | C-1 | Redesign **Phase 0** | Yes — hard stop | NLP, DevOps, RPR |
| **R1** ◐ | C-2, C-4 | Trustworthy budgeting/triage | Indirectly (sizing) | BE, DevOps, NLP |
| **R2** ◐ | C-3, C-8 | Full-corpus coverage | Track 1.H | DO, BE, PTPL, RPR |
| **R3** ◐ | C-5, C-6 | **GO/NO-GO on Track 3.F scope** | Track 3.F | NLP, RPR, **PTPL** |
| **R4** ○ | C-7 | Mapping single-source-of-truth | Tracks 1.D, 2.B, 3.D | BE, SDPA |

**Critical path:** R0 (C-1) and R1 (C-2/C-4) in parallel → R2 + R3 → R3's PTPL gate →
redesign Phase 1+ proceeds. R4 slots in anytime before redesign normalization. Redesign
Tracks 1.A–1.G are independent and continue throughout.

**Definition of done for the whole plan:** all eight "Done when" gates in the eng doc are
met, the run is marked production-cleared, and the PTPL Track-3.F scope decision is
recorded in the Decisions Log.

---

## 8. Assumptions & open questions (label, don't bury)

- **Roles** (NLP, BE, DevOps, RPR, SDPA, LKA, PTPL, DO) are taken from the eng doc and the
  redesign plan; this doc does not assign individuals.
- **Effort estimates** are order-of-magnitude, assuming branch (1) is *not* the C-1 cause
  in the worst case. A full bill-level corpus pass is the dominant cost if a re-run is
  needed.
- **Model of record:** `config/agent_models.json` pins `google/gemma-4-26b-a4b`. Note this
  conflicts with `CLAUDE.md`, which still says `openai/gpt-oss-20b` — worth reconciling, as
  the eng doc's C-1 remediation assumes Gemma. (Not one of C-1…C-8, but adjacent.)
- **Unverified figure:** the 472 `bill_level_extractions` in `run_summary.json` is exactly
  what C-1 is investigating; treat it as a claim, not a fact, until R0 closes.
