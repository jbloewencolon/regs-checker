# Run-1 Unified Plan v3 — Tracker-Grounded Data Quality

**Canonical plan.** Reframed by [`engineering_strategy_v3.md`](./engineering_strategy_v3.md), which merges v2's trust spine with the agent-extraction doc's compliance-concept layer, full-breadth normalization, metric schema, and per-agent refactors.
**Supersedes:** the v2 phasing previously in this file (`engineering_strategy_v2.md` is retained for history).
**Grounded in:** the May 2026 run (6,274 extractions); `actor_taxonomy_analysis.md` + `data/lookups/candidates/actor_value_to_code_full.csv`; and this session's verification-layer code investigation.
**Trust bar (decided):** clean, trustworthy data across all 232 laws, where *trustworthy = "matches Orrick/IAPP."*
**Status legend:** ✅ done · 🔧 in progress · ⏳ ready · 🔒 gated

---

## 1. The trust pipeline (the spine)

1. AI extracts (recall-first — over-extract, normalize later).
2. **Every fact links to a source** — a tracker entry or a verified evidence span.
3. **Normalize** to shared controlled vocabulary (Phase 3 / §6 of v3).
4. **Group** normalized fragments into **compliance concepts** (Phase 5 / §7).
5. **Compare** concepts to Orrick/IAPP (the trust check, Phase 4).
6. Match → `tracker_grounded`. Disagree → flag a human. Neither → `ungrounded` (shown only if labeled).
7. Confidence **recomputed after** the comparison.

The 88% Tier-C/D skew is most likely a symptom of steps 5–7 not running — confirmed in part by §2.

---

## 2. Verification-layer reality check (resolves v3 §12.1 — build vs. fix)

v3 still lists "does the verification layer exist?" as open. **I traced the code last session — it's built but partly disconnected.** This lets us skip the audit and target the fix.

| Component | State | Evidence |
|---|---|---|
| **Orrick alignment** | ✅ works — runs every extraction, feeds 0.30 weight, hard-gates Tier D | `src/core/orrick_validation.py:80-177`; `extractor.py:1179,2122,2534` |
| **Cross-validation agent** | ⚠️ exists but **orphaned** — runs post-extraction, writes `metadata_["cross_validation"]`, score never reaches `compute_confidence` | `src/agents/cross_validation.py`; `extractor.py:3026,3050-3061` |
| **`cross_validation_score` (0.25 weight)** | ❌ **dead** — always `None` → 25% of the model is absent | `confidence.py:74,160-161`; no call site passes it |
| **Gap detector** | ⚠️ exists, post-hoc, not integrated | `src/agents/gap_detector.py` |
| **IAPP** | ❌ **not ingested** — CSV in `static/`, status metadata only | `static/iapp_law_tracker.csv` |
| **`verification_results` table** | ❌ absent — ephemeral in `metadata_` | `src/db/models.py` |

**Consequence:** cross-validation (0.25) + evidence grounding (0.20) = **0.45 of the confidence model is under-delivering.** Wiring cross-validation back in (Phase 2b) is the cheapest big lever in the plan.

---

## 3. ⚠️ Open contradiction to settle before Phase 1a

v3 §8 A1 asserts applicability "confirmed not run." My C-1 analysis saw **472 bill-level rows**. These reconcile if 472 = enforcement + compliance_timeline only (2 agents × ~236), applicability = 0.
**Action:** `SELECT agent_name, COUNT(*) FROM bill_level_extractions GROUP BY agent_name;`. If applicability = 0, Phase 1a is a real extraction pass. My C-1 export fix (`_export_bill_level_extractions`) remains the prerequisite that lands the rows.

---

## 4. The phased plan

### Phase 1 — Foundation: trustworthy, measurable, non-destructive runs *(WS-A; now)*

| # | Task | v3 | Status |
|---|---|---|---|
| 1a | Settle applicability contradiction (§3); if 0, run applicability across all 232. Verify migration `k7h3i9j1f612_add_bill_level_extractions`. | A1 | ⏳ |
| 1b | Run versioning: `extraction_runs` table (`run_id, git_sha, prompt_versions, model_config, source_snapshot_hash, summary`) + `run_id` FKs; replace destructive purge with run-create + serving-run promotion. | A2 | ⏳ |
| 1c | **Metric schema** (proper C-2 fix): distinct counters — `llm_call_count`, `agent_invocation_count`, `successful_agent_invocations`, `extraction_item_count`, `abstention_count`, `error_count`, split `input/output/retry/verification/bill_level_tokens`; machine-readable per-run quality report. | A3 | ⏳ |
| 1d | Coverage 138→232: seed 135 text-ready laws; re-fetch SB 205 (priority) + SB_2966 (file missing). | A4 | ⏳ |

> 1c supersedes the earlier "add a scope label" patch — distinct named counters are the correct fix.

### Phase 2 — Cheap trust wins (no taxonomy dependency) *(front-loaded; chosen sequencing)*

| # | Task | Source | Status |
|---|---|---|---|
| 2a | ✅ E-1 verbatim evidence-span prompts (v1.1). **Run the 10–20 law test batch** (`_v2` suffix) to measure verified-span + A/B lift; capture baseline first. | E-1 | ✅ prompts; batch ⏳ |
| 2b ★ | **Wire cross-validation into confidence** — pass `cross_validation_score` from `metadata_["cross_validation"]` at the 3 `compute_confidence` call sites → resurrect the dead 0.25 weight. Make swallowed failures explicit. | §2, C1 | ⏳ |
| 2c | **Enforcement normalizer** (§6.7): aggregate enforcement from standalone rows + embedded `obligation.enforcement` + bill-level agents + Orrick/IAPP into one record per law. Fixes C-8 enforcement sparsity without re-running an agent. | §6.7 | ⏳ |
| 2d | **`legal_context` refactor** of `preemption_signal` (§6.8): typed categories (`true_preemption`, `agency_jurisdiction`, `cross_law_reference`…); hide low-value `other`. | §6.8 | ⏳ |

### Phase 3 — Full-breadth normalization substrate *(WS-B; gates the trust check)*

| # | Task | Status |
|---|---|---|
| 3a | B0 — align canonical codes to Orrick/IAPP's own categories first (~1 day). Defines "correct" for the rest. | ⏳ |
| 3b | B1.5 — clean the actor field (~5% non-actor/garbled, `INVALID_nonactor` in CSV); fix at parse layer, re-harvest. | ⏳ |
| 3c | B2 — two-tier dim model across **all** dimensions: actors (~10), `law_domain` (§6.3, new), covered systems (§6.4), obligation families (21, §6.5), rights (§6.6), enforcement (§6.7), `legal_context` (§6.8). Alias tables, all 3 DBs. | 🔒 after 3a |
| 3d | B3 — VC ratify; **defer the 4 LKA actor forks** (data_handler split, regulator-vs-gov, individual-as-protected, operator-vs-deployer). Fast-lane `modality_to_strength` (needs a strength vocab home). | 🔒 LKA |
| 3e | B4 — unified normalization passes in `rollup_matrix.py` reading `data/lookups/*`; idempotent; mismatches → `vocab_review_queue`. Migrate hard-coded maps (`payload_adapter.py:326-333`, `rollup_matrix.py:314`). Add `VocabReviewQueueItem`. | ⏳ |
| 3f | B5 — inject ratified enums into prompts + parse-time validation against `dim_*`. | 🔒 after 3d |
| 3g | B6 — re-harvest after Phase 1a; lock codes only when two prompt versions agree, pinned to `_prompt_hash`. | 🔒 after 1a |

### Phase 4 — Tracker alignment & confidence recompute *(WS-C; the trust check)*

| # | Task | Status |
|---|---|---|
| 4a | C2 — persist `verification_results` (per-item alignment/verification status + score). | ⏳ |
| 4b | C3 — ingest `static/iapp_law_tracker.csv` into DB; alignment pass vs **both** trackers → `tracker_grounded`/`orrick_aligned`/`iapp_aligned`/`tracker_conflict`/`extraction_only_claim`/`tracker_only_claim`; refine Orrick gate so IAPP-only laws aren't auto-Tier-D. | 🔒 after 3 |
| 4c | C4 — recompute confidence with v3's weight model (Orrick 30 / IAPP 20 / evidence 15 / citation 10 / cross-val 10 / gap 5 / analyst 10; redistribute when a tracker is absent). **Validate against gold-standard fixtures before it becomes the serving model.** | 🔒 after 4a,4b |
| 4d | C5 — enforce source linkage: every served fact carries a tracker ref or verified span, else `ungrounded`. | 🔒 after 2a batch |

### Phase 5 — Compliance-concept layer *(WS-D; the product bridge)*

| # | Task | Status |
|---|---|---|
| 5a | D1 — `compliance_concepts` + `concept_extraction_links` + `concept_tracker_links` tables (§7). | 🔒 after 4 |
| 5b | D2 — dedup + concept-grouping pass: group normalized fragments into concepts; concept-level confidence; link to tracker refs + evidence. | 🔒 after 5a |
| 5c | D3 — concept review queue; concepts (not raw rows) are the hand-off unit to the deferred law-card builder. | 🔒 after 5b |

### Phase 6 — Human review *(WS-E)*

| # | Task | Status |
|---|---|---|
| 6a | E1 — analyst-review step + queue (C3 conflicts); reviewer identity from auth; schema-validated corrections; immutable audit log. | ⏳ |
| 6b | E2 — review priority rules (tracker conflicts, extraction-only obligations, D-tier items on a card, zero-extraction high-importance laws, high-risk domains, parse failures). | ⏳ |
| 6c | E3 — review UI surfaces Orrick + IAPP fields, evidence spans, conflict warnings, confidence breakdown. | ⏳ |

### Parallel track — Agent-specific refactors *(WS-F; runs throughout)*

| Agent | Refactor | Sequencing |
|---|---|---|
| obligation | Reduce fragmentation; require subject/action/object/condition; separate penalties from duties; flag passive obligations. | **now** (no taxonomy dep) |
| definition_actor | Long-definition handling; separate definitions from actor maps; retry long passages at lower budget. | **now** |
| threshold_exception | Keep combined at extraction, split downstream; normalize threshold units. | after 3c |
| rights_protection | Map to rights taxonomy (§6.6); link each right to a duty-bearer. | after 3c |
| compliance_mechanism | Tighten boundaries (20% abstention); split recordkeeping/reporting/audit/assessment/registration/incident. | after 3c |
| preemption → `legal_context` | Rename + classify (this is Phase 2d). | Phase 2d |
| enforcement | Post-extraction normalizer (this is Phase 2c). | Phase 2c |

---

## 5. Sequencing

```
Phase 1 (applicability run + versioning + metrics) ──┐
   Phase 2 cheap wins (E-1 batch, cross-val rewire, enforcement, legal_context) ─ parallel
                                                     ▼
Phase 3  3a align→trackers → 3b clean → 3c dims [4 forks gate] → 3e normalize → 3g re-confirm
                                                     ▼
Phase 4 (tracker alignment + IAPP + confidence recompute)  ◄── trust bar
                                                     ▼
Phase 5 (compliance concepts) ──► Phase 6 (human review of conflicts)
                                                     ▼
            DEFERRED: law cards · applicability product · API · productionization

Parallel throughout: WS-F agent refactors (taxonomy-touching ones wait for 3c)
```

**Critical path:** Phase 1 → 3 → 4 → 5. Phase 2 runs in parallel as quick wins; the agent-refactor track runs throughout.

## 6. Highest-leverage unblocked actions (now)

1. **Phase 1a confirm query** — settle the applicability contradiction (decides whether 1a is a re-run).
2. **Phase 2b — wire cross-validation into confidence** — pure code, resurrects 25% of the confidence model.
3. **Phase 2c — enforcement normalizer** — aggregates 4 sources; fixes C-8 sparsity.
4. **Phase 2a test batch** — measure the v1.1 verbatim-prompt lift.

## 7. Risks (from adopting v3's full scope)

- **Scope explosion** — v3 is large (7 normalization dims + concept layer + 7 agent refactors). Mitigation: Phase 2 front-loads dependency-free value; Phases 5–6 stay gated behind a solid substrate.
- **Confidence re-weighting is a calibration change** — the new weights must be validated against gold-standard fixtures (Phase 4c), not dropped in.
- **Agent-refactor / taxonomy interaction** — taxonomy-touching refactors (rights, compliance_mechanism, preemption) must land *after* Phase 3c or they'll be redone.

## 8. Deferred (confirmed)

Law-card data model, business applicability product, product API, productionization. The compliance-concept layer (Phase 5) is the hand-off boundary; product resumes once §10 of v3's done-criteria hold.
