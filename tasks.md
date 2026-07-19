# Regs Checker — Tasks

## Run-1 Unified Plan v3 — Tracker-Grounded Data Quality

> Full plan: [`docs/run1_unified_plan.md`](docs/run1_unified_plan.md). Reframed by
> `engineering_strategy_v3.md` (merges v2's trust spine + compliance-concept layer +
> full-breadth normalization + metric schema + per-agent refactors). Trust bar:
> *trustworthy = "matches Orrick/IAPP."* Product layer deferred.
> Status: ✅ done · 🔧 in progress · ⏳ ready · 🔒 gated.
>
> **Verification layer (build-vs-fix, resolved):** built and wired. Orrick alignment works
> (0.50 near-term weight). CV wired via `_recompute_confidence_with_cv()` at verify step
> (Phase 2b ✅). Near-term weights: Orrick 0.50 / evidence 0.35 / citation 0.15; CV phases
> in at 0.10. IAPP **not yet ingested** — Phase 4b. Phase 3 normalization substrate complete
> (actor B0 + B1.5 + V2–V4 vocab + B4 loader/queue). Phase 4 = tracker alignment + recompute.
>
> **✅ Contradiction settled (2026-07-06, via Supabase MCP):** `GROUP BY agent_name`
> on the RC Supabase returned **169 rows for each of the three bill-level agents**
> (applicability_agent / compliance_timeline_agent / enforcement_agent). The
> applicability agent HAS run with full parity; the old "472 = enforcement+timeline
> only" hypothesis is disproven for current state. 169-of-232 coverage gap = the
> 1d seeding backlog + 16 quarantined laws, not a missing agent.

### Phase 1 — Foundation: trustworthy, measurable, non-destructive runs (now)
- ✅ Model pin — NVIDIA primary: `openai/gpt-oss-120b` (heavy agents) + `meta/llama-3.1-8b-instruct` (triage/definition_actor/preemption); local Gemma fallback retained in `config/agent_models.json`. 6+3 agents.
- ✅ **1a** — resolved (2026-07-06, run via Supabase MCP against `wjxlimjpaijdogyrqtxc`): `applicability_agent` = **169 rows**, equal to `compliance_timeline_agent` (169) and `enforcement_agent` (169). Applicability has run with full parity — **no backfill needed**. Remaining gap to 232 is coverage (1d), not a missing agent. *(NLP, DevOps)*
- ✅ **1b** — run versioning: `ExtractionRun` model + Alembic migration `m9j5k1l3h814` + nullable `run_id` FK on `extractions`/`bill_level_extractions` + run creation/finalization in `run_extraction()`. Purge kept for now; query-filter refactor deferred to when serving-run queries land. *(SDPA, BE, DevOps)*
- ✅ **1c** — **metric schema** (C-2 fix): `TokenUsageSummary` extended with `clause_level_*`/`bill_level_*` token buckets, `abstention_count`, `error_count`, `extraction_item_count`, `llm_call_count`; `run_summary.json` now emits named counters with `scope` annotation; `agent_stats.json` emits matching `scope`/`scope_note`. All call sites updated. Tests updated + passing. *(BE)*
- ⏳ **1d** — coverage 138→232: seed 135 text-ready laws; re-fetch **SB 205** (priority) + **SB_2966** (file missing). *(checklist was in r1_findings_supplement.md, now archived)*

### Phase 2 — Cheap trust wins (no taxonomy dependency; front-loaded)
- ✅ **2a** — E-1 verbatim evidence-span prompts (4 prompts v1.1). ⏳ Run 10–20 law test batch (`_v2` suffix); capture baseline first.
- ✅ **2b ★** — cross-validation wired into confidence. The 3 extraction-time call sites can't see the CV score (CV runs post-extraction), so the fix lives in `run_verification_pass`: `_recompute_confidence_with_cv()` re-runs `compute_confidence` with the accuracy score and writes the updated `confidence_score`/`confidence_tier` back. CV result now persisted for **all** extractions (was flagged-only). Failed CV returns an empty results list → no silent neutral pass. `cv_tier_changes` counter logged. 4 regression-guard tests in `test_confidence.py::TestCrossValidationWiring`. **Recompute runs during the Verify step (`/api/verify`), not initial extraction.** *(NLP, BE)*
- ✅ **2c** — enforcement normalizer (`src/core/enforcement_normalizer.py`): merges embedded `obligation.enforcement` + bill-level `enforcement_agent` + Orrick (IAPP wired) into one record per law; field-level precedence orrick>iapp>bill_level>obligation; per-field `_provenance`. Fixes C-8 sparsity, no agent re-run. 12 tests. *(NLP, BE)*
- ✅ **2d** — `legal_context` classifier (`src/core/legal_context.py`): typed categories (`true_preemption`/`constitutional_limit`/`interstate_conflict`/`agency_jurisdiction`/`cross_law_reference`/`unclassified`); `display=False` hides low-value `unclassified`; layered on raw `conflict_type` (non-destructive), wired into `payload_adapter`. 17 tests. *(NLP)*

### Phase 3 — Full-breadth normalization substrate (gates Phase 4)
- ✅ Harvest done — `data/lookups/candidates/actor_value_to_code_full.csv` (209 values, ~10-code model). C-7 map committed.
- ✅ **3a** — B0 actor vocabulary: 13 canonical codes locked; 215-row aliases.csv; 162-row mapping_examples.csv; fork_decisions.md (F1–F4 split decisions); 48-row unresolved_terms.csv; 13-row crosswalk (Orrick AI Scope + IAPP scope codes). `docs/NORMALIZATION_VOCABULARY_RATIFICATION_PLAN.md` committed. *(RPR, LKA)*
- ✅ **3b** — B1.5 parse-layer clean: `src/core/actor_normalizer.py` (INVALID_NONACTOR_TERMS + garbled patterns); Pydantic `field_validator` on `subject_normalized`, `actor_type`, `right_holder_normalized`, `responsible_party_normalized`. 21 tests. *(NLP, BE)*
- ✅ **3c** — V2–V4 vocabulary artifacts (36 files, 6 per dimension) for all 6 dimensions: actor (V1), law_domain (V2), covered_systems (V3), obligation_family (V4), rights (V4), enforcement (V4), legal_context (V4). Two-tier model (canonical codes + alias tables). *(after 3a)*
- ⏳ **3d** — VC ratify; **`business` (122 mentions) PENDING_LKA ruling**. `modality_to_strength` deferred. *(human gate)*
- ✅ **3e** — B4 normalization infrastructure: `src/core/vocab_loader.py` (normalize/flush/get_canonical_codes + module-level cache); `VocabReviewQueueItem` DB model; migration `n0k6l2m4i915` (vocab_review_queue table + 3 indexes); `rollup_matrix.py` reads `get_canonical_codes("legal_context")`. 16 tests. *(BE)*
- 🔒 **3f** — inject ratified enums into prompts + parse-time validation. *(after 3d)*
- 🔒 **3g** — re-harvest after 1a; lock codes when two prompt versions agree.

### Phase 4 — Tracker alignment & confidence recompute (the trust check)
- ✅ **4a** — `ExtractionVerificationStatus` + `VerificationRunSummary` models + migration `o1p7q3r5s016` + write calls in `verification_runner.py` all complete. Tables created on `alembic upgrade head` (pending operator run). *(BE, SDPA)*
- ✅ **4b** — IAPP tracker ingested (`src/core/iapp_alignment.py`); `iapp_has_data` flag prevents auto-Tier-D for IAPP-only laws (caps at C instead); `_run_iapp_alignment_for_dv` writes per-extraction `iapp_status` to `ExtractionVerificationStatus`; `iapp_alignment_score` now blended into `tracker_alignment_score` diagnostic dimension (Orrick 60% + IAPP 40% when both present). `total_score`/tier unchanged until Phase 4c weight validation. `_recompute_confidence_with_cv` passes IAPP score on every verify recompute. 5 new tests in `TestIAPPAlignmentScore`. *(2026-06-10)*
- 🔒 **4c** — recompute confidence with v3 weights (Orrick 30/IAPP 20/evidence 15/citation 10/cross-val 10/gap 5/analyst 10). **Validate against gold-standard fixtures before serving.** *(after 4a,4b)*
- 🔒 **4d** — enforce source linkage: tracker ref or verified span, else `ungrounded`. *(after 2a batch)*

### Phase 5 — Compliance-concept layer (the product bridge)
- ✅ **5a** — `ComplianceConcept` + `ConceptExtractionLink` + `ConceptTrackerLink` models; migration `p2q8r4s6t017` (3 tables, `conceptreviewstatus` enum, deterministic grouping-key unique index). *(SDPA)*
- ✅ **5b** — `src/core/concept_grouping.py`: deterministic grouping keyed on `(dv_id, concept_type, regulated_actor_family)`. Obligations classified via ratified `obligation_family` aliases; mechanisms→family; rights→`right_<code>`. Enforcement + exceptions attach law-wide. Concept-level confidence = mean of anchors; grounding from tracker presence; idempotent re-runs. *(BE, RPR)*
- ✅ **5c** — `src/core/concept_review.py`: priority-banded review queue (tracker_conflict→flagged-D→flagged→ungrounded), `resolve_concept`, `concept_review_counts`. Runnable via `python -m src.scripts.group_concepts`. *(BE)*

### Phase 6 — Human review
- ⏳ **6a** — analyst-review step + queue (C3 conflicts); auth identity; immutable audit log. *(BE, RPR)*
- ⏳ **6b** — review priority rules (tracker conflicts, extraction-only obligations, D-tier on a card, zero-extraction high-importance, high-risk domains, parse failures). *(RPR, PTPL)*
- ⏳ **6c** — review UI surfaces Orrick + IAPP fields, evidence spans, conflict warnings, confidence breakdown. *(FE)*

### Parallel track — Agent refactors (WS-F; throughout)
- ⏳ **now** — obligation (reduce fragmentation; require subject/action/object/condition; separate penalties from duties); definition_actor (long defs; separate from actor maps; retry long passages). *(NLP)*
- 🔒 **after 3c** — threshold_exception (split downstream; normalize units); rights_protection (map to rights taxonomy; link duty-bearer); compliance_mechanism (tighten 20% abstention; split mechanism types).

### Highest-leverage unblocked actions
1. ~~**Merge `claude/brave-lamport-d9zgjx` → main**~~ — **done, confirmed 2026-07-12 (SFH-2a)**: merged long before this list was last touched (23 merge-commit refs on main, PRs #134–155).
2. **Run `alembic upgrade head`** on operator machine — migration `l8i4j0k2g713`.
3. **Selective triage reset + Extract All** — unblocks everything downstream.
4. **1a confirm query** — settle the applicability-row contradiction (`SELECT agent_name, COUNT(*) FROM bill_level_extractions GROUP BY agent_name`).
5. **4a** — persist `verification_results` table (code drafted; needs migration + run).
6. **4c confidence recompute** — full v3 weight model; validate against gold fixtures before serving.
7. **2a test batch** — measure the v1.1 verbatim-prompt lift.

### Deferred (confirmed)
Law-card data model, applicability product, API, productionization — resume on clean tracker-grounded data; the concept layer (Phase 5) is the hand-off boundary.

---

## Remediation Plan — Security & Data-Quality Audit (Phases 0–2)

> **Phase 2 (Review-Binding Data Path) — ✅ COMPLETE (2026-07-02)**
>
> All P2-1 through P2-7 completed and pushed to `claude/audit-ai-law-pipeline-f7alql`.
> Includes the production-readiness audit (Phase 0, P0-1 through P0-6) and the
> migrations & schema-truth work (Phase 1, P1-1 through P1-6). Full detail in
> `docs/phase0_completion_log.md`, `docs/phase1_completion_log.md`,
> `docs/phase2_completion_log.md`, and `docs/remediation_plan.md`.
>
> Summary: P2 gates the sync path on human review (RC approval + PN's own backup veto),
> purged the pre-existing 13,488-row stale table, de-ratcheted rollup aggregations,
> wired the update-propagation leg, fixed live drift on matview refresh + indexes,
> and added view freshness tracking to `/health`. Two product decisions confirmed
> mid-implementation; five pre-existing bugs fixed (column-name mismatches, missing
> eligibility filters, cast syntax, live schema drift, missing security hardening).

## Remediation Plan — Phase 3: Confidence-Only Publish Gate (2026-07-02)

> **Status: ⏳ planned, not started.** Product decision confirmed 2026-07-02: the sync
> pipeline should publish extractions to Policy Navigator based on **confidence tier
> alone (A/B/C, never D)** — the `review_status='approved'` requirement built in Phase 2
> (P2-1/P2-3) is being **removed**, not tightened. This is a deliberate reversal of the
> P2 review gate, applied consistently to both `sync_extractions.py` and
> `rollup_matrix.py`. Tracked as tasks #21–#27.
>
> **Open risk to flag before/while implementing:** removing `review_status` entirely
> (not narrowing to an allow-list) means extractions RC has explicitly marked
> `rejected` or `flagged` will sync to Policy Navigator too, as long as tier is A/B/C —
> tier and review are orthogonal signals. If that's not intended, P3-1 should filter
> to `review_status != 'rejected'` rather than dropping the column check entirely.
> Confirm before P3-1 ships.

- ✅ **P3-1** **[Done 2026-07-06]** Both legs in `sync_extractions.py` now gate on
  confidence_tier alone (A/B/C; D excluded) instead of requiring
  review_status='approved'. Added `review_status != 'rejected'` safety gate to
  prevent explicitly-rejected extractions from syncing (analyst veto mechanism).
  Module docstring and inline comments updated. *(sync strategy finalized)*
- ⏳ **P3-2** — Policy Navigator live migration: `CREATE OR REPLACE VIEW
  rollup_eligible_extractions` to drop its `review_status IN ('approved','verified')`
  condition (added in P2-3, migration `p2_3_rollup_eligible_extractions_view`) so it
  becomes a pass-through of `synced_extractions` (or is retired in favor of querying
  `synced_extractions` directly — decide during implementation). Tier filtering
  continues to live in Python in `rollup_matrix.py`, unchanged. Verify against a
  scratch Postgres schema before applying live, per the P2 pattern.
- ✅ **P3-3** **[Done 2026-07-06]** `sync_updates()` in `sync_extractions.py` updated:
  `is_eligible` now checks `confidence_tier in eligible_tiers and review_status != 'rejected'`.
  Docstring updated to reflect tier-only + rejection-gate design (no longer
  "RC leads, PN backs up" approval-gated). Paired with P3-1 in same commit. *(sync strategy finalized)*
- ⏳ **P3-4** — Dashboard: new panel/route for **Tier-D extractions** (permanently
  ineligible under the tier-only gate) so analysts have a queue of what still needs
  re-extraction or prompt/model tuning to reach C+. Mirror the existing
  `/api/low-confidence/export.csv` pattern in `src/api/routes/dashboard.py`.
- ⏳ **P3-5** — Dashboard: new **audit panel** listing `synced_extractions` rows in
  Policy Navigator whose `review_status` is not `approved`/`verified` — i.e., rows now
  live in the product without RC human sign-off. This is the visibility backstop for
  removing the P2 review gate; without it there's no way to see what shipped
  unreviewed.
- ✅ **P3-6** **[Done 2026-07-06]** Unit tests for P3 eligibility logic added to
  `tests/unit/test_sync_extractions.py`. 22 tests covering: tier-eligible helper,
  sync eligibility logic (tier + rejection gate), regression tests showing
  pending/flagged/verified at A/B/C now sync (vs P2's approved-only block),
  tier-D always ineligible, and analyst veto mechanism. All tests passing. *(test coverage finalized)*
- 🔧 **P3-7** — `docs/phase3_completion_log.md` created (2026-07-06) documenting P3-1,
  P3-3, P3-6 completion and P3-2/P3-4/P3-5 status. Still needed: forward-pointing
  addendum on `docs/remediation_plan.md`'s Phase 2 section noting gate was relaxed
  in Phase 3 (deferred until P3-2 ships for complete before/after). Apply live PN
  migration via operator's `apply_migration` call post-P3-2. *(docs partial)*

**Sequencing:** P3-1 and P3-3 (code) can land together first since they're pure RC-side
sync logic. P3-2 (live PN view) should follow, verified on scratch Postgres first — it's
the one live-database change in this phase. P3-4/P3-5 (dashboard) are independent and
can land in parallel with P3-2. P3-6/P3-7 close out the phase.

---

## Extraction Accuracy Plan (EA) — Legal-Defensibility Review Findings (2026-07-03)

> Source: full-stack extraction-architecture review (prompts, agents, confidence,
> verification, citation handling, eval harness). Goal: **hyper-accurate, auditable
> legal data** — precision, traceability, and defensibility over speed/cost.
> Status legend: ✅ done · 🔧 in progress · ⏳ ready · 🔒 gated. Severity in **[ ]**.
>
> **⚠️ Cross-plan contradiction #1 (decide before EA3):** EA3 proposes an
> **evidence-first** confidence model (tracker alignment demoted to corroboration,
> ≤0.20 combined; Tier-D hard gate replaced by an `uncorroborated` flag). This
> conflicts with the ratified trust bar (*"trustworthy = matches Orrick/IAPP"*,
> Run-1 plan header) **and** with the planned 4c weights (Orrick 30 + IAPP 20 =
> still tracker-dominant). Both models can't ship. Product owner must pick:
> (a) tracker-first = tier measures corroboration, new/uncovered laws are
> permanently down-tiered; (b) evidence-first = tier measures grounding quality,
> trackers flag disagreement. EA3 is written for (b); if (a) wins, EA3-1 shrinks
> to "fix the citation-format reward" only.
>
> **⚠️ Cross-plan contradiction #2 (sequencing hazard):** Remediation Phase 3
> makes confidence tier the **only** publish gate to Policy Navigator. Until EA0/EA3
> land, tier is inflated by topic-overlap Jaccard and rewards well-*formatted*
> fabricated citations (`_score_section_reference`) — i.e. P3 would ship data gated
> by a known-flawed signal. **Land EA0 + EA3 before or with P3**, or interim-gate
> P3 on `evidence_grounding >= threshold` in addition to tier.
>
> **Coupling note:** EA1 (eval substrate) gates every scoring/prompt change
> (EA3, EA4-4, EA6). EA0 and EA2 are bug-class fixes that need only unit tests
> and can land immediately. Do not tune weights (EA3-1) before the gold set
> (EA1-1) exists, or the tuning is unfalsifiable.

### Phase EA0 — Stop-the-bleeding defects (no eval dependency; land now)
- ✅ **EA0-1** **[Critical]** CV misattribution fixed. `run_cross_validation()`
  (`cross_validation.py`) now rejects any validation item with a missing,
  non-int/bool, out-of-range, or duplicate `extraction_index` (discarded +
  logged, never guessed via the old `len(results)` fallback); a batch where
  every item is unattributable returns `status="failed"` instead of a clean-
  looking empty pass. Missing `accuracy_score` no longer defaults to 1.0 —
  it's recorded as `0.5` with `score_missing: True` so it can never silently
  inflate confidence. Extractions with no matching validation item are
  surfaced via new `unmatched_extraction_ids` (they were never actually
  reviewed by CV — left alone, not treated as passing). Prompt tightened to
  require `extraction_index`/`accuracy_score` on every item, one per
  extraction, unique indices. 13 tests in `test_cross_validation.py`;
  existing `test_verification_agents.py` (21 tests) unaffected. *(NLP, BE)*
- ✅ **EA0-2** **[High]** Routing recall bug/doc mismatch — resolved as a
  **doc fix, not a code change**. Investigation found `routing.py`'s
  ≥1-signal threshold is deliberate, not accidental: `test_routing_recall.py`
  already pins it as tested behavior (`test_five_of_six_signals_returns_none`
  explicitly documents "threshold is len-1"), and `triage_recall_sample_rate`
  (5%, `config.py`) already exists as the compensating control for exactly
  this recall risk. Rewriting core routing logic without a live-model gold
  set to measure the tradeoff would be tuning-by-guess — the opposite of
  what EA1 exists to prevent. `architecture.md` corrected to describe actual
  behavior, cites the pinning test, and explicitly gates threshold *tuning*
  (not re-documentation) on the EA1 gold set. *(NLP)*
- ✅ **EA0-3** **[High]** Stale review priority fixed. New
  `_sync_review_priority()` in `verification_runner.py`, called after each
  CV recompute: re-derives `ReviewQueueItem.priority` from the post-CV tier,
  and forces max urgency (3) when any CV issue is `critical`/`high` severity
  regardless of tier — closing the case where a ~0.08-weight CV nudge at
  0.10 weight wasn't enough to cross a tier boundary but the underlying
  finding was serious. Escalates only (never lowers a priority another
  signal set higher). 11 tests in `test_verification_review_priority.py`.
  *(BE)*
- ✅ **EA0-4** **[High]** Bill-level silent input-truncation now visible.
  `BillLevelAgent.extract_bill()` (`bill_level_base.py`) computes
  `chars_dropped`/`input_truncated` from the pre-existing `full_text[:
  MAX_BILL_TEXT_CHARS]` slice — previously untracked — and threads them
  through `BillLevelResult` plus into the stored JSONB payload
  (`_input_truncated`/`_chars_dropped`, via `setdefault` so a model-produced
  field of the same name is never clobbered) on both the success and
  unrecoverable-failure paths; no migration needed since `BillLevelExtraction
  .payload` is JSONB. Distinct from the pre-existing `truncated` column,
  which only ever covered *output* truncation (`finish_reason=length`). 6
  tests in `test_bill_level_truncation.py`, including a spy-based test
  pinning that `get_prompt` only ever sees the truncated slice. **Dashboard
  surfacing deferred** — this session has no live app/browser to verify a UI
  change against (see CLAUDE.md environment note); the payload flag is
  queryable today via the JSONB column for anyone building the panel.
  Content-side fix (target the enforcement-pattern sections instead of the
  raw prefix) is still EA5-3. *(BE)*
- ✅ **EA0-5** **[Medium]** CV/gap model made config-driven. Added
  `cross_validation`/`gap_detection` entries to `AGENT_DISPLAY` +
  `_AGENT_MAX_TOKENS` (`model_config.py`) and to `config/agent_models.json`
  under both `local` and `nvidia` blocks (16384 max_tokens, matching the
  pre-fix provider-default token budget so behavior doesn't silently
  change). `_default_agents()` special-cases these two to `openai/gpt-oss-
  20b` under `nvidia` (preserving current behavior) and to
  `settings.local_extraction_model` under `local` (fixing the actual bug —
  the old hardcoded NVIDIA-only model name doesn't exist in LM Studio, so
  verification silently broke under the local provider). Stale
  "qwen3.5-9b" docstring replaced with a pointer to EA4-1, which owns the
  actual lineage-diversity question. *(NLP, BE)*

### Phase EA1 — Evaluation substrate (gates EA3/EA4-4/EA6 prompt+weight changes)
- 🔧 **EA1-1** **[Critical]** Gold set expansion: 33 fixtures / ~3 statutes (one
  vetoed) → stratified set of **8 laws** (size ruled 2026-07-12, SFH-2c — the
  EA amendment #4 solo-capacity floor, not the original 12–15 target below):
  ≥2 OCR-quality PDFs, ≥1 amendment-markup (engrossed) bill, ≥1 deepfake/
  likeness law, ≥1 tracker-silent law, per-agent expected extractions for
  **all 6 clause agents**, prioritizing the agents that feed the PN matrix
  (obligation, threshold_exception, enforcement_agent, applicability_agent)
  per amendment #4. Single annotation + strong-model adjudication on
  disagreement candidates (team-scale double-annotation dropped at 8 laws);
  expand past 8 only if EA1-3 variance shows the set too small to detect
  regressions. *(RPR, NLP)*
  - **Progress (2026-07-13):** Stratification plan + annotation worklist
    committed (`docs/ea1_gold_set_plan.md`) — measured current coverage
    (35 clause fixtures / 12 statutes + 2 bill fixtures), mapped the 8 laws
    to strata and committed sources, and turned the gaps into a
    priority-ranked worklist (Tier-1 source-verifiable now, Tier-2 needs DB,
    Tier-3 needs RPR). **Measured gaps:** preemption 0 positive fixtures,
    applicability_agent 0 bill, compliance_timeline_agent 0 bill;
    rights_protection + compliance_mechanism thin (2 laws each). Bill-level
    enforcement expanded 1→2 laws with a **second enforcement shape**:
    AZ SB1359 (civil, per-day) + **AR HB1877 (criminal, Class B felony** —
    verbatim from § 5-27-603, references the committed engrossed source via
    `bill_text_file`). Both conservatively annotated (omit unstated fields;
    notes say why). Preemption over-firing is **already measured** — the
    reworked harness scores the preemption agent as "should abstain" on all
    35 clause fixtures, catching the run-label id-9 §230 misclassification
    class without a new fixture; only a *positive* preemption fixture remains
    (RPR, Tier-3). **Still needs a live LLM / operator DB / RPR** for the
    rest — see plan §4.
- ✅ **EA1-2** **[Critical]** Harness now covers all 9 agents (2026-07-13).
  **Root mismatch fixed:** `extract()` returns an `ExtractionResult` (list of
  extractions + optional abstention), but the old harness scored it as a bare
  `dict | AbstentionResult` (`assert isinstance(actual, dict)`) — it would have
  raised on every real call. New `_score_extraction_result` /
  `_result_to_actual` reduce an `ExtractionResult` to a scorable actual:
  explicit abstention or empty list → abstention (detection TN/FN); otherwise
  the single **best-matching** extraction (field-overlap vs the fixture's
  expected payload) so a passage that legitimately yields several extractions
  (e.g. 3 definitions) isn't penalized for the ones the single-slot fixture
  didn't encode. `CLAUSE_AGENT_MAP` expanded from 3 → all 6 clause agents
  (added rights_protection, compliance_mechanism, preemption; key is the
  extraction TYPE, `definition`→`DefinitionActorAgent`). **Bill-level eval
  mode added:** new `BILL_AGENT_MAP` (enforcement_agent / applicability_agent /
  compliance_timeline_agent), `run_bill_level()` + `run_all()`, a separate
  `bill_level_gold_standard_dir` fixture subtree (config), `bill_text` inline
  or `bill_text_file` reference, and `_score_bill_case` (one payload per law,
  no abstention axis — errored/empty payload = detection FN + per-field FN).
  Fixtures may hold a LIST of expected payloads per type (forward-compat;
  list-vs-list alignment deferred, flagged in docstring). `EvaluationResult.
  to_baseline_dict()` + `write_baseline()` emit the deterministic per-agent
  per-field P/R/F1 artifact EA1-3 diffs against. Seeded one conservative
  bill-level fixture (`bill_level/az_sb1359_enforcement.json`) with two
  hand-verified enforcement facts (`penalty_per="day"`,
  `private_right_of_action=false`) to exercise the mode end-to-end. 29 new
  tests (42 total in `test_evaluation_harness.py`); full suite 1314 passing;
  CI hard gate green. **Unblocks EA1-3** (baseline capture — needs live LLM
  on operator's machine). *(NLP, BE)*
- ⏳ **EA1-3** **[High]** Baseline + regression gate: run harness on current prompts/
  models, commit per-agent per-field P/R/F1 baseline artifact; every prompt/model/
  weight PR reruns and diffs against baseline. Numerics scored exact-match;
  text fields scored by span overlap. *(NLP, DevOps)*
- ✅ **EA1-4** **[High]** Amendment-markup corpus audit — **confirmed live, not
  theoretical**, across 236 source files / 211 ingested (`output/law_sources/`,
  `output/law_texts/`). Two distinct encodings found, requiring separate
  detection: **(1) HTML strikethrough/underline styling** — 3 confirmed
  real cases after eliminating false positives (generic link-hover CSS in
  IL/MO, an audio-player "Listen Live" underline in RI, CCPA "deletion
  request" prose in CA misidentified as drafting markup by a naive keyword
  scan). The smoking-gun case: **2025 Wisconsin Act 69**
  (`TMP-WI-ESTATEADVERTIS.html`) flattens via `BeautifulSoup.get_text()` to
  `"...client , principal firm, or firm , without..."` — `", principal
  firm,"` and the trailing `","` are struck (no-longer-law) fragments sitting
  inline with zero distinguishing signal, immediately followed by a genuinely
  new underlined sentence (itself not effective until 2027-01-01 per the
  Act's own effective-date section) with no "newly added" marker either.
  FL (`TMP-FL-FLORIDAACTRELA.html`, `amendmentInsertedText`/
  `amendmentDeletedText` classes) and NY (`TMP-NY-NYCAIEMPLOYMEN.html`,
  inline `text-decoration: underline`) are insertion-only variants of the
  same defect class (lower risk — no stale-law contamination — but the new
  text still carries no "not yet settled" flag). **(2) Literal bracket
  deletion convention** — confirmed in `TMP-KY-AMENDMENTTOINT.txt` (PDF
  source; Kentucky prints deletions as literal `[bracketed]` text, e.g.
  `[deviant]`, `[beastiality]` — survives `pdftotext` since it's plain
  characters, not styling) and `TMP-NJ-RULESPERTAININ.txt` (NJ Register
  regulatory notice, 22 bracket markers in one document, e.g. `[SINGLE
  FAMILY] SINGLE-FAMILY`). This is the more dangerous encoding for PDF-heavy
  corpora because **pure visual strikethrough in a PDF (no literal
  brackets) is undetectable by any text-based heuristic** — would need
  font/color-run PDF parsing, out of scope here; the 4 PDFs matching
  `*AMENDMENT*` in filename with zero bracket hits (AZ, ND, OK) could not be
  ruled out on this basis. *(NLP)*

### Phase EA2 — Grounding: bind evidence to fields (bug-class; parallel with EA1)
- ✅ **EA2-1** **[Critical]** Deterministic numeric cross-check — **clause-level
  scope landed**. New `src/core/numeric_grounding.py` extracts candidate
  numbers (money, days, hours, months incl. year→month conversion, counts,
  FLOPS incl. caret/scientific/multiplication notation) from a payload's
  *verified* evidence spans and compares against each populated typed-numeric
  field (`max_civil_penalty_usd`, `cure_period_days`, `retention_period_months`,
  `incident_reporting_hours`, `employee_threshold`, `revenue_threshold_usd`,
  `consumer_data_threshold`, `compute_flops`, `assessment_frequency_months`),
  reporting `grounded` / `mismatch` / `unverifiable` per field (absence of a
  parseable number is "unverifiable", not "mismatch" — avoids penalizing
  spelled-out numbers our regex can't parse). Wired via a new
  `_apply_numeric_grounding()` helper into all three extraction insertion
  sites in `extractor.py` (`extract_single_record`, `run_retry_failed`,
  `run_recovery_extraction`): result stored in
  `extraction_meta["numeric_grounding"]` (informational — does not change
  `confidence_score`, that's EA3-1's job) and a confirmed `mismatch` forces
  review priority to max urgency (3), reusing the EA0-3 escalation pattern.
  33 tests in `test_numeric_grounding.py` + 5 wiring tests in
  `test_extraction_pipeline.py`.
  **Not yet covering bill-level payloads** (`enforcement_agent`,
  `applicability_agent`, `compliance_timeline_agent`): those agents don't
  produce a structured, verified `evidence_spans` list at all today — at
  most one free-text quote (e.g. `enforcement_text`) that's never run
  through span verification. Extending numeric grounding there depends on
  **EA5-1** landing first (per-field verified evidence spans for bill-level
  payloads); EA5-1 should call `numeric_grounding.check_numeric_grounding()`
  once that structure exists rather than re-implementing the check. *(NLP,
  BE)*
- ✅ **EA2-2** **[High]** Span provenance landed, with one honest scope
  decision. `text_grounding.py` gained index-map-aware normalization
  (`_normalize_unicode_with_map`, `_normalize_whitespace_with_map`,
  `_normalize_text_with_map`) that tracks each character in the normalized
  string back to its raw-passage origin (composing across Unicode
  substitution and whitespace-collapse, including correctly attributing a
  *collapsed multi-char whitespace run* to its full raw span, not just one
  character of it). **Tier 1/2** (`verify_evidence_spans`) now report
  `char_start`/`char_end` valid against the raw passage — safe to slice
  `passage[char_start:char_end]` directly for audit-UI highlighting; this is
  the actual bug fix. **Tier 3/4** (loose/artifact-stripped matches):
  scoped down to `char_start`/`char_end: None` rather than attempting to
  invert `strip_revisor_artifacts`'s compound regex transforms (dehyphenation,
  margin-number stripping, glyph repair) back to raw coordinates — that's a
  materially harder problem for an already-lower-trust match tier, and
  reporting *no* offset is safer than reporting a wrong one that silently
  mis-renders in a highlighter. `review.html` already null-checks
  `char_start`, confirmed no downstream breakage. Every verified span now
  carries `match_tier` (1–4) and `loose_match` (bool); unverified spans
  carry neither key (unchanged). `_normalize_text_with_map` returns `None`
  (safe fallback to the old norm-passage-only string, no raw offsets) when
  NFC normalization changes string length — rare combining-character
  sequences, uncommon in US legislative text; a real test constructs this
  case directly (`"café"` with a combining acute accent) rather than just
  asserting the fallback exists in theory.
  `reground_spans.py` (the backfill): needed two fixes, not just
  "reprocess more rows" — added `--backfill-provenance` to broaden the SQL
  filter to catch already-verified spans missing `match_tier`, **and** fixed
  `_reground_batch`'s write-decision, which previously only detected
  unverified→verified flips and would have silently skipped writing
  provenance to rows that were already fully verified (the bulk of the
  backfill's actual target). 27 new tests: 19 in `test_span_provenance.py`,
  8 in `test_reground_spans.py` (batch-logic tests against a mocked
  session — no live DB in this environment, so the SQL string itself is
  unverified against real Postgres; review the `--backfill-provenance`
  WHERE-clause addition before running it live). *(NLP, BE)*
- ✅ **EA2-3** **[High]** Truncation/repair honesty landed. New
  `was_repaired` field on `ExtractionResult` (`base.py`): `extract()` now
  compares the fence/think-block-stripped output against `_repair_json`'s
  output (both `.strip()`-normalized to avoid a whitespace-only false
  positive) — any actual repair (control-char strip, trailing-comma
  removal, truncated-JSON salvage, stringified-array unwrap, etc.) sets it
  True. New `cap_at_tier_c()` in `confidence.py`: when `result.truncated`
  OR `result.was_repaired`, caps an A/B-tier score+tier down into C's band
  (score and tier kept mutually consistent — never shows a high score next
  to a demoted tier); C/D extractions are left unchanged (never improved).
  Wired into all three extraction insertion sites in `extractor.py`.
  **"Force review" implemented as a max-urgency (3) `ReviewQueueItem`
  priority bump, not a publish-block** — Tier C alone is still
  auto-publish-eligible under the P3 confidence-only sync gate, so capping
  the tier alone wouldn't guarantee a human look; the priority bump is
  what actually surfaces it. `extraction_meta` now also records
  `was_repaired`/`truncated` explicitly (previously only `truncated`, and
  only when true). 15 new tests: 7 in `test_confidence.py::TestCapAtTierC`,
  8 in new `test_was_repaired_flag.py` (exercises the real `extract()` path
  end-to-end with `_call_llm` mocked, not just the static repair helper).
  *(NLP)*
- ✅ **EA2-4** **[High]** Parser strikethrough handling landed
  (`src/ingestion/parser.py`), scoped directly off the EA1-4 findings above.
  New `_strip_struck_content()` walks the BeautifulSoup tree **before**
  `get_text()` runs (the actual bug — `get_text()` cannot distinguish
  struck from live text after the fact) and `.decompose()`s only
  unambiguous deletion markup: tag name `strike`/`del`/`s`, or inline
  `style="text-decoration: line-through"` (never a `class=` name heuristic
  alone — too easy to false-positive-delete real statutory text). Verified
  against the actual Wisconsin Act 69 file from the audit:
  `"...client , principal firm, or firm , without..."` → `"principal firm"`
  now absent from the parsed passage, while the genuinely new
  `"...out-of-state broker..."` sentence (underline-marked, correctly
  *not* struck) is retained. Insertion markup (`<ins>`, `text-decoration:
  underline`) is detected but deliberately left untouched — `get_text()`
  already includes it correctly, it just wasn't flagged before. New
  `amendment_markup_detected` (+ `bracket_markers_count` /
  `struck_chars_removed` when applicable) written to
  `NormalizedSourceRecord.metadata_` for every affected passage — HTML-path
  detection is document-level (segmentation runs on already-flattened text
  with no DOM correspondence, so every passage from a flagged document
  gets the flag; reporting coarse-but-honest over fabricating a
  passage-level offset, same precedent as EA2-2's Tier 3/4 scope
  decision). Separately, a `_BRACKET_DELETION_PATTERN` regex
  (`\[[A-Za-z][^\[\]]{1,60}\}`, min-count 2 per passage) catches the
  KY/NJ literal-bracket convention uniformly across HTML/PDF/plaintext —
  **informational only, never auto-stripped**, since ordinary numeric
  citations (`[42 U.S.C. § 2000e-8]`) are also bracketed and a false-
  positive strip there would delete real law; requiring an alpha first
  character after `[` already excludes the numeric-leading citation
  pattern in practice. `_parse_html`'s return type changed from
  `list[tuple]` to `(list[tuple], html_markup_info | None)` — the one
  caller (`parse_and_normalize`) and the internal PDF-guard branch were
  both updated; `_parse_pdf`/`_parse_plaintext` signatures untouched. 24
  new tests in `test_parser_amendment_markup.py` (style-detection,
  strip-in-place behavior, end-to-end `_parse_html` including a clean-bill
  regression case and the mixed strike+underline Wisconsin-shaped case,
  bracket-pattern precision including the numeric-citation exclusion).
  **Not covered**: visual-only strikethrough inside a PDF with no literal
  bracket convention (e.g. AZ/ND/OK `*AMENDMENT*`-named PDFs, 0 bracket
  hits) — undetectable without font/color-run PDF parsing, out of scope;
  `extract_text_sample()` (the lightweight classification-only HTML
  sampler) intentionally left unchanged since its output never reaches the
  extraction pipeline. *(NLP, BE)*

### Phase EA3 — Confidence model v4 (gated on EA1 baseline + contradiction #1 ruling)
- 🔒 **EA3-1** **[Critical]** Evidence-first rebalance: demote Orrick+IAPP combined
  to ≤0.20 corroboration signal; evidence grounding dominant; replace Orrick
  Tier-D hard gate with `uncorroborated` flag (distinct axis, not a tier). Kill
  the Jaccard→1.0 saturation at 0.25 and the 0.3 floor-for-any-data
  (`confidence.py:199-205`). Validate on EA1 gold set: tier assignments must
  correlate with annotated correctness better than v3 weights before serving.
  *(NLP, product owner sign-off)*
- 🔒 **EA3-2** **[Critical]** Citation credit must require resolution: `_score_
  section_reference` rewards citation **format** (fabricated "§ 6-1-1703(2)(b)"
  scores 1.0). Make citation weight contingent on `citation_verifier` match;
  tighten verifier — remove number-only substring match
  (`citation_verifier.py:146-151`, "§ 3" currently verifies against "Section 13"),
  exact/subsection-prefix only, persist match method per citation. *(NLP, BE)*
- ⏳ **EA3-3** **[High]** Stop extracting known facts: inject `jurisdiction` and
  default `section_reference` deterministically from ingestion metadata /
  `section_path`; model may refine subsection detail but a fabricated ref that
  contradicts `section_path` is rejected. Removes a hallucination surface that
  EA3-2's scorer currently *rewards*. *(NLP)*
- 🔒 **EA3-4** **[High]** CV findings get teeth: confirmed `critical` CV issue →
  hard tier cap (C) instead of a ~0.08 nudge at 0.10 weight. *(NLP)*
- 🔒 **EA3-5** **[High]** Recompute + backfill: re-tier all extractions under v4,
  persist before/after (reuse `ExtractionVerificationStatus` pattern), coordinate
  with P3 publish gate so PN doesn't serve mixed-model tiers. *(BE, DevOps)*

### Phase EA4 — Verification independence & recall recovery
- ⏳ **EA4-1** **[High]** Real model diversity: CV/gap currently run gpt-oss-20b
  auditing gpt-oss-120b — same lineage (correlated blind spots), smaller model
  auditing larger (inverted). Route verification to a different-lineage model ≥
  extractor capability (Llama-70B-class or better on NVIDIA); config via EA0-5.
  Measure CV catch-rate on seeded-error fixtures before/after. *(NLP)*
- ⏳ **EA4-2** **[High]** Gap candidates become actionable: today they land in
  `VerificationRunSummary.gap_candidates` JSONB and die. High/medium-confidence
  candidates spawn targeted re-extraction (route the named agent to that passage)
  → new extraction rows enter the normal confidence + review path; verbatim
  `evidence_text` string-verified before accepting a candidate. *(NLP, BE)*
- ⏳ **EA4-3** **[High]** Triage discard audit: `not_relevant` and `quality_fail`
  passages are terminal — invisible to extraction, CV, and gap detection. Sample
  5–10% into a human audit queue (mirror `triage_recall_sample_rate=0.05`
  pattern); `quality_fail` routes to re-OCR/manual list, never silent drop.
  Track measured triage FN rate as a standing metric. *(NLP, RPR)*
- 🔒 **EA4-4** **[High]** Model reassignment (gated on EA1 baseline to prove the
  lift): `definition_actor` — anchors the whole bill's terminology — runs
  llama-3.1-8b at temp 0.2/top_p 0.7 (sampling on, weakest model);
  `preemption` — hardest legal reasoning — also 8B at 1536 tokens. Move both to
  gpt-oss-120b, temp 0. *(NLP)*
- 🔒 **EA4-5** **[Medium]** Dual-model agreement on matrix numerics only: second
  independent model extracts the typed numeric/boolean fields; deterministic
  field diff; disagreement → review. Reuses `model_agreement_count` (which today
  is incremented by same-model duplicate emissions — agreement-washing; rename or
  split the counter). Scope-limited to numerics to bound cost. *(NLP, BE)*

### Phase EA5 — Bill-level hardening & reconciliation
- ⏳ **EA5-1** **[High]** Bill-level evidence: require a verbatim quote per
  populated field in bill-level payloads; run quotes through
  `verify_evidence_spans` against the bill text; unverified quote → field flagged.
  Today `law_enforcement_details` (the most product-visible data) ships with one
  unverified ≤300-char quote and no confidence scoring. Once per-field verified
  spans exist, call `src/core/numeric_grounding.check_numeric_grounding()`
  (landed in EA2-1) against them for `max_civil_penalty_usd`,
  `cure_period_days`, `compute_flops`, `assessment_frequency_months`, etc. —
  do not reimplement the numeric cross-check. *(NLP)*
- ✅ **EA5-2** **[High]** Conflict detection landed in
  `normalize_enforcement()` (`src/core/enforcement_normalizer.py`) — with a
  scope correction the plan's premise got wrong. New
  `ENFORCEMENT_CONFLICT_FIELDS = (max_civil_penalty_usd,
  private_right_of_action, cure_period_days)` (exactly the plan's named
  fields, not all `ENFORCEMENT_FIELDS` — `enforcing_body`/`penalty_per`/
  `enforcement_text` differ cosmetically across sources far more often
  than substantively and would flood review with noise). For each
  conflict field, `_detect_enforcement_conflicts()` collects every
  source's non-null value (not just the precedence winner) and flags
  `_has_enforcement_conflict` + a per-field `_enforcement_conflicts` entry
  (`selected_value`, `selected_source`, `contributions` — every source
  that reported a value) when two or more sources disagree. Precedence
  behavior is unchanged (Orrick still wins) — this only adds visibility
  into what precedence was silently overriding, e.g. the existing
  `test_boolean_false_is_preserved` case (Orrick says no private right of
  action, an obligation row disagrees) now also surfaces as a conflict
  rather than resolving invisibly. **Scope correction discovered before
  writing this:** the plan describes `enforcement_normalizer` as an
  already-live reconciliation step ("the redundancy already exists;
  exploit it") — grepped the whole repo and found `normalize_enforcement`/
  `normalize_enforcement_for_law` have **zero callers** anywhere outside
  their own test file. The merge function itself was never wired into any
  pipeline, script, or dashboard route, so "emit `enforcement_conflict`
  review items" (i.e. write real `ReviewQueueItem` rows) isn't
  implementable yet — there's no call site that runs this per-law and
  persists a result. Landed the conflict-detection logic itself (pure,
  fully unit-tested, useful the moment this module is wired up), but
  wiring `normalize_enforcement_for_law()` into an actual per-law job (and
  deciding *when* it runs — on every extraction run? on-demand? a
  periodic reconciliation pass?) is a separate architecture decision, not
  something to unilaterally invent a call site for. **Also out of scope,
  flagged not silently dropped:** disagreement *within* the obligation
  source itself (many passage-level obligations stating different cure
  periods, collapsed to first-non-null by `_coalesce_obligation_enforcements`
  before it ever reaches conflict detection) — the plan's wording
  ("sources disagree") reads as the 4 named sources, not intra-source
  variance; a related but distinct signal. 8 new tests in
  `test_enforcement_normalizer.py` (20 total, up from 12). *(NLP, BE)*
- ✅ **EA5-3** **[Medium]** Enforcement-agent input targeting landed
  (`src/agents/enforcement_agent.py`). New `_build_bill_excerpt()`: for
  bills at or under `_TAIL_CHARS` (20,000 — the corpus median bill is
  ~11k chars), behavior is **unchanged** — sent in full, no truncation-
  bias risk either way. For longer bills, prefers `bill_context
  ["enforcement"]` (already computed by `build_bill_context()` in
  `src/core/bill_context.py`, and already passed into every bill-level
  agent's `context` arg by `extractor.py` — this agent just wasn't using
  it) since that's built by pattern-matching every passage in the bill
  regardless of length or position, so it has **no prefix bias at all**;
  plus a bounded raw tail (last 20k chars) as a catch-all for enforcement
  language the pattern matcher missed. When no enforcement-pattern
  passages were found anywhere in the bill, falls back to the tail alone
  rather than a raw prefix — strictly better, since the tail is the
  conventional location for enforcement sections and a prefix guarantees
  the opposite. 8 new tests in
  `test_enforcement_agent_input_targeting.py` pin exactly the failure
  mode being closed: a marker planted early in an oversized synthetic bill
  is confirmed absent from the final prompt (the truncation-bias case),
  while markers in the enforcement excerpt and the tail are both present.
  *(NLP)*
- ✅ **EA5-4** **[Medium]** Penalty-tier structure landed
  (`src/agents/enforcement_agent.py`), with an honest limit on what "landed"
  means here. New optional `penalty_tiers` field —
  `[{"condition": str, "amount_usd": int}, ...]` — requested only when the
  bill states different amounts for different conditions (negligent vs.
  willful, first vs. subsequent violation, etc.); the prompt explicitly
  forbids wrapping a single flat penalty in a one-item array, to avoid
  manufacturing false tier structure. `_coerce_penalty_tiers()` drops
  malformed entries (missing condition, unparseable amount) defensively
  rather than failing the whole extraction, matching the existing int/bool
  coercion style in this file. `max_civil_penalty_usd` — the flattened
  matrix column — now **self-heals** from the tiers: if the model leaves
  it null or reports it inconsistently lower than its own highest tier,
  it's corrected upward (never lowered), making "keep max for the matrix
  column" an enforced invariant rather than just a prompt instruction the
  model might not follow. Purely additive and backward-safe: a law where
  the model never populates `penalty_tiers` behaves byte-for-byte like
  before (field defaults to `None`, `max_civil_penalty_usd` untouched). 11
  new tests in `test_bill_level_agents.py::TestEnforcementAgentPenaltyTiers`
  cover the coercion and self-heal logic exhaustively — but that's the
  deterministic *parsing* half only. **What's unvalidated:** whether the
  model reliably populates `penalty_tiers` *accurately* against real bills
  (correct tier boundaries, no hallucinated conditions) has no ground
  truth to check against without EA1's gold set, and the "negligent vs.
  willful" tier-condition framing itself hasn't had RPR (legal reviewer)
  sign-off — this file's original role tag was `(NLP, RPR)`, and no RPR
  role exists in this sandboxed session. Treat the schema/self-heal as
  solid; treat model-side extraction quality of tier data as an open
  question for EA1. *(NLP; RPR sign-off still outstanding)*

### Phase EA6 — Prompt & schema legal-nuance fixes (gated on EA1 regression gate)
- 🔒 **EA6-1** **[High]** Implied rights defensibility: `rights_protection.yml:77`
  manufactures rights from obligations ("notice obligation implies notice right") —
  a contested legal inference stored at equal status with textual rights. Add
  `derivation: textual | implied_from_obligation` to `RightsProtectionPayload`;
  implied rows visibly badged in review + product. *(RPR ruling, NLP)*
- 🔒 **EA6-2** **[Medium]** Constrained decoding: NVIDIA NIM structured outputs
  (JSON schema) for clause agents; shrinks the 5-strategy `_repair_json` surface
  (repair chain retained as fallback for local provider). *(NLP, BE)*
- ✅ **EA6-3** **[Low]** Dedupe landed (`src/ingestion/extractor.py`) — was
  never actually gated on EA1 (it's pure deterministic post-processing on
  already-validated payloads, no prompt touched; the session note already
  flagged this, listing it in the "unblocked without live LLM access"
  bucket rather than the EA6 gate). New `_dedupe_interpretation_risks()`
  runs once per passage in `extract_single_record()`, right after both
  agents' results are collected but **before** they're persisted as
  separate `Extraction` rows — mutates `result.extractions` in place so a
  merged passage (multiple `source_records`) doesn't re-derive the same
  duplicate once per source_record. Keys on
  `(term.strip().lower(), risk_type)`; keeps the first occurrence in a
  **fixed agent-precedence order** (`obligation` before
  `rights_protection`), not thread-completion order — `agent_results` is
  populated via `as_completed()`, so without a fixed order the same
  passage could dedupe differently on different runs. Same term flagged
  under a genuinely different `risk_type` is intentionally NOT deduped
  (e.g. "promptly" as both `vague_term` and `temporal_ambiguity` are two
  distinct findings). Incidental but correct side effect: since the `seen`
  set is shared across one passage rather than reset per extraction item,
  two obligations from the *same* agent citing the same ambiguous term
  also collapse to one finding — the plan's wording named cross-agent
  specifically, but the same duplication logic applies within an agent
  too. 12 new tests in `test_interpretation_risk_dedup.py`. *(BE)*
- 🔒 **EA6-4** **[Low]** CV prompt trim: stop re-serializing evidence_spans +
  metadata into the CV payload dump (CV already has the passage). Token savings
  with zero signal loss. *(NLP)*
- ✅ **EA6-5** **[Medium]** Date parse status landed — pure deterministic
  post-processing, no prompt touched (like EA6-3, this was never actually
  gated on EA1 despite living under the EA6 heading). New
  `TimelineInfo.date_parse_status: dict[str, str]`
  (`src/schemas/extraction.py`) — a `model_validator(mode="after")`
  classifies each populated date field (`effective_date`,
  `compliance_deadline`, `sunset_date`) as `"parsed"` (matches
  `YYYY-MM-DD`, meaning `normalize_date()` succeeded) or `"unparsed"`
  (raw model text passed through unchanged); a field the model never
  populated is simply absent from the dict, distinct from "populated but
  unparseable". Scoping note: the raw text itself was never actually
  *lost* before this fix — `normalize_date(v) or v` already preserved it
  verbatim in the field on failure — so no separate raw-text field was
  added; the missing piece was purely the status marker distinguishing
  the two cases, which is what "store raw + normalized" cashes out to
  here. **"Unparsed dates excluded from deadline computations"**: found
  and fixed the one real computation site —
  `src/core/concept_grouping.py`'s obligation-bucket builder collects a
  `deadline` value into `bucket.deadlines` (used later for
  `sorted(bucket.deadlines)[0]`, an earliest-deadline lexicographic sort
  that's only meaningful for genuine ISO strings) — previously took
  whatever `timeline.get("effective_date")` held, ISO or free text,
  unfiltered. New `_is_iso_date()` checks the `YYYY-MM-DD` format
  directly (rather than trusting a stored `date_parse_status` key) so it
  correctly excludes bad data from **both** newly-written and
  already-existing extractions with no backfill required. 17 new tests:
  9 in `test_timeline_date_parse_status.py`, 8 in
  `test_concept_grouping.py::TestIsIsoDate`. **Found but out of scope,
  not fixed:** that same line reads
  `timeline.get("effective_date") or timeline.get("compliance_date")` —
  the schema field is named `compliance_deadline`, not `compliance_date`,
  so the second alternative is dead code and `compliance_deadline` values
  never reach `bucket.deadlines` at all today. Real, but a distinct bug
  from the one EA6-5 named (wrong field name entirely omitted vs. free
  text incorrectly trusted) — flagged here rather than silently bundled
  in, since fixing it changes what data flows into a concept's deadline
  and deserves its own look rather than a drive-by edit. *(BE)*

**Sequencing:** EA0 (all) + EA2-1/2-2/2-3 first — pure defect fixes, unit-testable,
no gating. EA1 starts immediately in parallel (annotation is the long pole; RPR
capacity is the constraint). EA3 blocks on EA1-3 baseline **and** the contradiction
#1 product ruling — do not let P3 (tier-only publish) ship before EA3 or without an
interim evidence-grounding gate. EA4-1/4-2/4-3 after EA0-5. EA5 parallel with EA4.
EA6 last, each item gated on the EA1 regression gate. Cost note: EA4-1/EA4-5 raise
per-law token spend — acceptable per the precision mandate, but capture actual
$/law in `run_summary.json` before/after so the trade is explicit.

**Step-back amendments (self-review, same day):**
1. **Baseline before behavior changes.** EA1-3's baseline must be captured on
   *current* code before EA0-2 (routing) lands, or the baseline measures the new
   routing and the lift is unmeasurable. Exact order: EA1-3-lite (run existing 33
   fixtures, commit scores) → EA0 → full EA1. Days, not weeks. **Resolved
   2026-07-03:** EA0-2 landed as a doc-only fix (see above) — no routing
   behavior changed, so this specific ordering hazard didn't materialize. The
   ordering principle still holds for any *future* task that changes routing
   or extraction behavior: capture/refresh the EA1 baseline first.
2. **EA3-1 additionally gates on EA2-1/EA2-2.** Evidence grounding today verifies
   *quoting*, not *support* (review finding #2) — promoting it to the dominant
   confidence weight before field-binding lands would swap one weak dominant
   signal for another. EA2 is a hard prerequisite for EA3-1, not a parallel track.
3. **Review-queue capacity budget.** EA0-3, EA4-2, EA4-3, and EA5-2 all *add*
   review volume; with a single reviewer an unbounded queue is a fake safety net.
   Each queue-feeding task must state expected items/run; cap total inflow (e.g.
   top-N by severity per run) and track queue age on the dashboard.
4. **Right-size EA1-1 to solo capacity.** 12–15 laws double-annotated is
   team-scale. Floor: 8–10 laws, single annotation + strong-model adjudication on
   disagreement candidates, prioritizing the agents that feed the PN matrix
   (obligation, threshold_exception, enforcement_agent, applicability_agent).
   Expand only if EA1-3 variance shows the set is too small to detect regressions.
5. **Consolidation spike (unscheduled, flag only):** the 6-clause-agent split is a
   small-local-model legacy; with 120b-class models + 131k context, a single
   section-level pass with unified schema + strong verifier might cut error
   surface and cost more than EA0–EA6 combined. Two-day spike on the EA1 gold set
   before committing to EA6-2 constrained-decoding work per-agent. Also: 4c's
   weight model and EA3-1 must merge into ONE confidence plan — whoever lands
   first absorbs the other; do not maintain two.
6. **Session note (2026-07-03):** Phase EA0 (5/5) and Phase EA2 (3/4 — EA2-1,
   EA2-2, EA2-3; EA2-4 correctly left gated) landed this session across 4
   commits (`ac31f32`, `9c003e4`, `899a6fe`, `bd4b6c1`) — 987/987 unit tests
   passing (877 pre-session baseline + 110 new). No live LLM required for
   any of it: every item this session was a pure code/config defect,
   deterministic post-processing, or offset-math fix, unit-tested with
   mocked providers/sessions. **EA1 (gold-set baseline capture) could NOT
   be run this session** — this execution environment has neither
   `NVIDIA_API_KEY` nor a reachable local LM Studio instance, and the
   harness (`src/evaluation/harness.py`) calls real model providers.
   EA1-3-lite (run the existing 33 fixtures, commit baseline scores) is
   the next step that requires the operator's machine via `python
   start.py` per CLAUDE.md. EA2-4 (parser strikethrough handling) is
   correctly gated on EA1-4 (a corpus audit of `output/law_sources/` for
   engrossed-bill amendment markup) — that's an investigation task, not
   assumed blocked by live-LLM access; worth a follow-up pass reading the
   actual source files rather than guessing prevalence.
   **What's still unblocked without live LLM access, for the next session:**
   EA5-2 (enforcement reconciliation — flag disagreement between
   clause-level/bill-level/tracker data; the redundancy already exists,
   nothing currently exploits it), EA5-3 (enforcement-agent input
   targeting — feed `bill_context.py`'s enforcement sections instead of
   the raw 128k-char prefix), EA5-4 (penalty-tier structure), EA6-3
   (dedupe `interpretation_risks` — deterministic post-processing, not a
   prompt change), EA6-5 (date parse status). **Not** unblocked: EA6-4 (CV
   prompt trim) touches the actual prompt sent to the model — per the same
   discipline applied to EA0-2, a prompt change without EA1 measurement is
   tuning-by-guess, not a safe pure-code fix, despite looking like one.
7. **Session note (2026-07-04):** EA1-4 (corpus audit) and EA2-4 (the parser
   fix it gated) both landed this session. The audit read the actual source
   files rather than estimating prevalence, per the note above: 236 source
   files / 211 ingested, 2 markup encodings confirmed live (HTML strike/
   underline styling — 3 real cases after discarding false positives;
   literal-bracket deletion convention — 2 real cases, one in a PDF), with
   one concrete smoking-gun example (2025 Wisconsin Act 69) showing a
   struck, no-longer-law fragment and a not-yet-effective inserted sentence
   both reading as plain current text pre-fix. EA2-4 fixed the HTML case
   at the DOM level (before `get_text()` flattens it — the only point
   where struck vs. live text is still distinguishable) and added an
   informational bracket-heuristic for the PDF/plaintext case; visual-only
   strikethrough inside a PDF with no literal brackets remains genuinely
   undetectable without font/color-run PDF parsing (flagged as out of
   scope, not silently dropped). 24 new tests, 1011/1011 passing. Both
   items were pure code/text-processing work, no live LLM needed — same
   pattern as EA0/EA2-1/2-2/2-3. **Phase EA2 is now fully landed (4/4).**
   Remaining unblocked-without-live-LLM work for next session, in priority
   order: EA5-2, EA5-3, EA5-4, EA6-3, EA6-5 (list unchanged from the prior
   session note — still not started). EA1-1/1-2/1-3 (gold-set annotation
   and baseline capture) remain the actual long pole and still require the
   operator's own machine (`python start.py`) per CLAUDE.md — nothing in
   this sandbox can substitute for a real model call.
8. **Session note (2026-07-04, continued):** all five remaining
   unblocked-without-live-LLM items landed the same day, in the order
   listed above — EA5-2, EA5-3, EA5-4, EA6-3, EA6-5, five commits
   (`c08397b`, `22ec59a`, `934322c`, `7a30ec3`, plus this plan update).
   57 new tests, 1068/1068 passing. Two items (EA6-3, EA6-5) turned out
   to have been mis-filed under the EA6 "gated on EA1" heading — both are
   pure deterministic post-processing with no prompt involved, and the
   session note above already knew this and listed them as unblocked;
   the tasks.md entries now say so explicitly rather than leaving the 🔒
   marker's implication uncorrected. Each item surfaced at least one
   scope-boundary worth recording rather than silently expanding past:
   EA5-2's `enforcement_normalizer` turned out to have zero callers
   anywhere (the plan assumed it was already wired into a live
   reconciliation step); EA5-4's prompt-side change is flagged as
   unvalidated for extraction *quality* pending EA1 and RPR sign-off,
   only the deterministic parsing half is claimed as solid; EA6-5 found
   a second, distinct bug in the same line it fixed
   (`compliance_date` vs. the schema's actual `compliance_deadline`)
   and flagged it rather than bundling an unrequested fix into the
   commit. **Phase EA5 is now fully landed (4/4). EA6-3 and EA6-5 are
   done; EA6-1/6-2/6-4 remain genuinely gated on the EA1 regression
   gate** (EA6-1 needs an RPR ruling on the `derivation` field's default
   framing, EA6-2 is a structured-decoding infra change, EA6-4 trims an
   existing signal from a live prompt — all three are real prompt/schema
   risk, not the false-gate pattern EA6-3/6-5 turned out to be). What's
   left everywhere in the EA plan now requires either the operator's own
   machine (EA1's gold-set annotation and baseline capture) or a product/
   RPR decision this sandbox can't make (contradiction #1 in the header
   above, EA3's confidence-model ruling, EA5-4's RPR sign-off). No further
   items are known to be safely actionable here without one of those two
   unblocks.

---

## Extraction Accuracy Plan v2 (EAR) — Architecture Re-Review Findings (2026-07-19)

> Source: full second-pass extraction-architecture review (2026-07-19) covering
> prompts (`prompts/*.yml`), agent base logic (`src/agents/base.py`), schemas
> (`src/schemas/extraction.py`), routing/triage, span grounding
> (`src/core/text_grounding.py`), confidence model (`src/core/confidence.py`),
> the orchestrator (`src/ingestion/extractor.py`), the verification layer
> (CV / gap / citation, `verification_runner.py`), model routing
> (`config/agent_models.json`), and the eval harness. **Successor to the EA
> plan above**: it verifies what EA landed, corrects four stale premises, and
> phases the remaining + newly-found work. Same goal: hyper-accurate,
> auditable legal data — precision, traceability, defensibility over
> speed/cost. Status: ✅ done · 🔧 in progress · ⏳ ready · 🔒 gated.
> Severity in **[ ]**.
>
> **Re-review verdict:** the skeleton is right — verbatim spans with
> deterministic verification, abstention protocol, fail-closed CV/gap
> statuses, truncation/repair tier caps + forced review, conservation ledger,
> per-extraction provenance. Remaining risk concentrates in four places:
> (1) model routing inverted vs. task difficulty (EA4-4's finding, confirmed
> still live in config); (2) confidence dominated by Orrick token-similarity,
> which is also **circular** (premise P-2 below); (3) spans are verified but
> **fields are not** — nothing binds subject/modality/action/condition to a
> verified quote; (4) several verification layers produce findings nothing
> consumes (gap candidates, unmatched CV extractions, citation issues never
> reaching the score).
>
> **Premise corrections to the EA plan (verified against code 2026-07-19):**
> - **P-1 (EA4-1 premise stale):** CV/gap no longer run gpt-oss-20b —
>   `agent_models.json` now routes both to `meta/llama-3.1-70b-instruct`
>   under `nvidia`. That fixes lineage overlap vs. the gpt-oss-120b heavy
>   agents, but (a) the capability inversion remains (70B judging 120B
>   output), (b) it creates NEW lineage overlap with the llama-8b agents it
>   audits (definition_actor, preemption, triage — Llama judging Llama), and
>   (c) the `local` provider still has ZERO diversity: the same Gemma model
>   extracts and validates itself. EA4-1's scope is refreshed as EAR-4-1.
> - **P-2 (EA3-1 under-scoped — circularity):** Orrick alignment isn't just
>   over-weighted; it's circular. `_build_context()` injects
>   `key_requirements` into agent prompts, and
>   `validate_extraction_against_orrick` then scores the payload against the
>   same text. The 0.50-weight signal partially measures **prompt echo**, and
>   a hallucination that parrots tracker vocabulary earns full credit while a
>   correct extraction from a provision the tracker summary skips is dragged
>   down. EA3-1/SFH-3a must add circularity mitigation (EAR-3-1).
> - **P-3 (fixture counts moved):** gold set is now 40 clause fixtures /
>   12 statutes + 2 bill-level (EA1-1 progress) — still CO-SB205-heavy,
>   still zero positive preemption fixtures, still zero bill-level
>   applicability/timeline fixtures.
> - **P-4 (QA-6 half-applied):** the schema description for
>   `related_authority` removed its example values because small models
>   parroted them verbatim (`extraction.py:709-715`) — but the prompt
>   actually sent still ships them (`prompts/preemption.yml:19`:
>   `"Dec 2025 Federal EO on AI"`, `"US Constitution Art. I § 8"`), on the
>   smallest model in the fleet. The parrot risk QA-6 diagnosed is still
>   live in the YAML. Fixed via EAR-1-5 (prompt change → goes through the
>   regression gate, not landed blind).

### Phase EAR-0 — Deterministic hardening (sandbox-actionable now; no prompt or weight changes)
- ⏳ **EAR-0-1** **[High]** CV scores become lower-only until verifier parity is
  proven. `_recompute_confidence_with_cv()` (`verification_runner.py`) currently
  lets a CV accuracy score *raise* confidence/tier — but the verifier is a
  weaker model than the extractor (P-1), so a rubber-stamp from a model that
  can't see the error inflates tier, and under the P3 tier-only publish gate
  that inflation ships to Policy Navigator. Clamp the recompute: score/tier may
  drop or hold, never rise. EA0-3's priority escalation is untouched. Remove
  the clamp only when EAR-4-1's catch-rate acceptance passes. Unit tests:
  raise-blocked, lower-preserved, tier-boundary cases. *(BE)*
- ⏳ **EAR-0-2** **[High]** Gap-candidate evidence gets the same guard as every
  primary agent (deterministic half of EA4-2, split out so it stops waiting on
  the re-extraction design). `gap_detector.py` demands verbatim
  `evidence_text` but never string-verifies it — the one layer whose whole
  purpose is recall has no hallucination guard. Run each candidate's
  `evidence_text` through `verify_evidence_spans()` against the passage before
  accepting; drop + count unverifiable candidates
  (`gap_candidates_unverified` in `VerificationRunSummary`). *(BE)*
- ⏳ **EAR-0-3** **[High]** Citation self-consistency + match-method
  provenance (deterministic telemetry half of EA3-2; the number-only *removal*
  stays in EAR-3-2 because it changes the verification rate the confidence
  coupling will read):
  (a) **NEW check** — compare the extraction's claimed `section_reference`
  against its own source record's `section_path`; we already know where the
  passage came from, and a claim contradicting it is the cheapest
  hallucination catch available. Doesn't exist anywhere today.
  (b) Tag every `_build_section_index` entry as heading-derived vs.
  body-derived — the index currently ingests section numbers scraped from the
  first 500 chars of `text_content`, so a passage that merely *references*
  "Section 5" makes a fabricated "Section 5" citation verify.
  (c) Persist the match method per citation (exact / prefix / substring /
  number-only) instead of a bare verified count, so EAR-3-2 can set its
  threshold from measured data. *(BE, NLP)*
- ⏳ **EAR-0-4** **[High]** Audit-trail stamps — reproduce-or-it-didn't-happen:
  (a) persist effective sampling params (temperature / top_p /
  reasoning_effort / max_tokens) per extraction in `extraction_meta` — today
  they live only in mutable config, so a challenged extraction cannot be
  re-derived after a config edit; (b) capture the provider's reported
  model/build identifiers from the response (hosted NIM models are not
  version-pinned; a stable model string does not mean a stable model);
  (c) raw-response retention: store the raw model output content-hash-keyed
  and compressed (e.g. `output/raw_responses/<sha>.json.gz`), referenced from
  `extraction_meta` — clause-level extractions currently keep only
  `model_reasoning[:2000]`; the bill-level path holds `raw_output` in memory
  and never persists it. Retention: per run, purge when a superseding run is
  approved. *(BE, DevOps)*
- ⏳ **EAR-0-5** **[Medium]** Verifier-lineage honesty flag: stamp
  `verifier_model_id` + `verifier_lineage_overlap` (same family as any audited
  agent / identical model) on `VerificationRunSummary` and surface in
  `run_summary.json`. A `local`-provider run where CV model == extraction
  model is self-validation and must say so instead of reading like
  independent review. *(BE)*
- ⏳ **EAR-0-6** **[Medium]** Fix the `compliance_date` dead-code bug EA6-5
  flagged-but-deferred: `concept_grouping.py` reads
  `timeline.get("compliance_date")` — the schema field is
  `compliance_deadline`, so real deadlines never reach `bucket.deadlines`
  today. Fix the field name; regression test that a `compliance_deadline`-only
  timeline contributes to the earliest-deadline sort. *(BE)*
- ⏳ **EAR-0-7** **[Medium]** Loose-match telemetry: per-run distribution of
  span `match_tier` (1–4), tier-3/4 match lengths, and per-agent loose-match
  rate into `run_summary.json`. The 15-alnum-char Tier-3 floor is a guess and
  formulaic statutory prose makes short coincidental runs cheap ("the
  developer shall" is 17). Measure before tuning (EAR-5-6). *(BE)*
- ⏳ **EAR-0-8** **[Low]** Docstring honesty: `schemas/extraction.py` and
  `base.py` claim "Pydantic v2 strict mode"; the models are deliberately
  coercive (list→str joins, null→[], str→[str], first-of-list). Correct the
  docstrings — an auditor reading "strict mode" over-trusts validation.
  *(BE)*

### Phase EAR-1 — Measurement substrate (operator-gated; still the long pole)
- ⏳ **EAR-1-1** **[Critical]** = **EA1-3** (pointer, unchanged): capture the
  per-agent per-field P/R/F1 baseline on the operator's machine **before any
  prompt/model/weight change in this plan** (EA amendment #1 discipline).
  Every EAR item marked 🔒-on-baseline blocks here. *(NLP, DevOps, operator)*
- ⏳ **EAR-1-2** **[High]** Gold-set gap closure = EA1-1 remainder,
  re-prioritized by this review (P-3): positive preemption fixture first
  (the agent is currently scored on abstention only), bill-level
  applicability + compliance_timeline fixtures (zero today), then
  rights_protection / compliance_mechanism breadth (2 laws each), then non-CO
  obligation depth. *(RPR, NLP)*
- ⏳ **EAR-1-3** **[High]** Seeded-error verification benchmark (new
  instrument): programmatically corrupt gold payloads in the failure modes
  that matter legally — flipped modality, wrong subject, dropped
  exception/condition, fabricated penalty amount, wrong section_reference —
  and measure CV catch-rate per error class. EA4-1 says "measure CV
  catch-rate before/after" but no instrument exists; this is it, and it
  doubles as the standing verifier-quality regression. *(NLP, BE)*
- 🔒 **EAR-1-4** **[High]** Drift canary (gated on EAR-1-1 baseline artifact):
  scheduled harness run (weekly + before each production extraction run)
  against the live provider; alert on per-agent F1 drop vs. the committed
  baseline; log EAR-0-4(b)'s provider build identifiers alongside so a drift
  alert can be attributed to a provider-side model update vs. our change.
  *(DevOps, NLP)*
- 🔒 **EAR-1-5** **[High]** First prompt fix through the gate (gated on
  EAR-1-1 + the preemption fixture from EAR-1-2): remove the parrot example
  values from `prompts/preemption.yml:19` (`related_authority`), bump
  template version, verify via baseline diff. P-4 has the full rationale —
  QA-6 fixed the schema description but the prompt actually sent still
  carries the examples, on the smallest model in the fleet. *(NLP)*

### Phase EAR-2 — Grounding v2: bind fields, not just spans (the hinge phase)
- ⏳ **EAR-2-1** **[Critical]** Material-field span binding — the single
  highest-leverage item in this plan. Today `evidence_grounding` = fraction
  of spans that verify, so one verified span + a hallucinated `action` scores
  **1.0**: the signal measures *quoting*, not *support*, and `field_name` on
  spans is optional so nothing ties `modality`, `condition`,
  `trigger_condition`, or `related_authority` to any quote. Define per-agent
  material fields (obligation: subject / modality / action / condition;
  rights: right_type / trigger_condition / duty_bearer; threshold: value +
  unit + condition; compliance: mechanism_type / responsible_party;
  preemption: conflict_type / related_authority). Deterministic post-pass:
  each populated material field requires a verified span with matching
  `field_name` (or whose text contains the field's normalized value); record
  `ungrounded_fields: [...]` in `extraction_meta` + surface in review UI.
  **Informational first** (EA2-1's landed pattern — no confidence change),
  then becomes the dominant evidence input to EAR-3-1. This is the EA
  amendment-#2 prerequisite made concrete: do NOT promote evidence weight
  while evidence measures quoting. EA2-1 (numerics) already did this for
  typed numbers; this extends it to the fields that change legal meaning.
  *(NLP, BE)*
- 🔒 **EAR-2-2** **[High]** Clause/bill enforcement separation (gated on
  EAR-1-1 — prompt + context change): `obligation.yml` and
  `_append_bill_context()` (`base.py:330-338`) instruct clause agents to
  populate enforcement fields **from the bill-context block** — text not in
  the passage, so those values are ungroundable *by design* (they can never
  span-verify, permanently depressing grounding or floating unverified).
  Remove the instruction; clause-level enforcement/timeline comes from
  passage text only; cross-section enforcement facts are the bill-level
  `enforcement_agent`'s job, merged per-law via
  `normalize_enforcement_for_law()` — which EA5-2 verified has **zero
  callers**. Wire it as the per-law reconciliation step (run at verification
  pass + before sync, idempotent) — this is the call-site decision EA5-2
  explicitly deferred, now answered. *(NLP, BE; product sign-off on merge
  precedence)*
- ⏳ **EAR-2-3** **[High]** = **EA5-1** (pointer, unchanged): per-field
  verified quotes for bill-level payloads, then
  `check_numeric_grounding()` against them. The most product-visible data
  (`law_enforcement_details`) still ships with one unverified ≤300-char
  quote. *(NLP)*
- ⏳ **EAR-2-4** **[Medium]** Input targeting for the other two bill-level
  agents: EA5-3 fixed `enforcement_agent`'s prefix-truncation bias;
  `applicability_agent` and `compliance_timeline_agent` still consume
  `full_text[:128k]` raw. Mirror the landed pattern — scope/definitions
  sections + bounded tail for applicability; effective-date/deadline pattern
  passages for timeline. Same "strictly better than a biased prefix"
  argument EA5-3 landed under. *(NLP)*

### Phase EAR-3 — Confidence v4 (= EA3 + SFH-3a, one merged plan; gated: EAR-1-1 + EAR-2-1 + contradiction-#1 ruling)
- 🔒 **EAR-3-1** **[Critical]** = **EA3-1 / SFH-3a** (pointer) with two spec
  additions from this review: (a) **circularity mitigation** (P-2) — tracker
  alignment must become a corroboration/`tracker_status` axis that can flag
  disagreement but never lift a tier, because the model is shown the tracker
  text it is later scored against (holding tracker text out of prompts is the
  alternative, but demotion is simpler and keeps the context value);
  (b) the evidence signal input switches from span-count ratio to EAR-2-1
  material-field coverage. Kill the ≥0.25-Jaccard→1.0 saturation and the
  0.3 any-data floor as already specified. This review is a further
  independent vote for evidence-first on contradiction #1: a signal cannot be
  the trust anchor while it is also prompt input. *(NLP, product owner)*
- 🔒 **EAR-3-2** **[Critical]** = **EA3-2** (pointer): citation credit
  contingent on citation-verifier resolution; number-only substring match
  removed **here** (its removal changes the verification rate the new weight
  reads — EAR-0-3's telemetry sets the threshold from data). *(NLP, BE)*
- ⏳ **EAR-3-3** **[High]** = **EA3-3** (pointer): inject `jurisdiction` +
  default `section_reference` deterministically from ingestion metadata;
  EAR-0-3(a)'s self-consistency check graduates from telemetry to hard
  reject here. *(NLP)*
- 🔒 **EAR-3-4** **[High]** = **EA3-4** (pointer): confirmed critical CV
  issue → hard tier cap, not a 0.08 nudge. *(NLP)*
- 🔒 **EAR-3-5** **[High]** = **EA3-5** (pointer): recompute + backfill under
  v4, before/after persisted, coordinated with the P3 publish gate. *(BE,
  DevOps)*

### Phase EAR-4 — Verification independence & recall (EA4 with refreshed premises)
- ⏳ **EAR-4-1** **[High]** = **EA4-1** refreshed per P-1. Requirement
  restated as a **matrix, not a model pick**: the verifier must be
  ≥-extractor-class AND different-lineage *per audited agent*. Llama-70B is
  acceptable for gpt-oss-120b output; it is NOT independent for the llama-8b
  agents' output (moot once EAR-4-4 moves those agents off Llama). `local`
  provider: single-model is accepted as a hardware reality, but EAR-0-5's
  flag must be set and CV must be excluded from confidence entirely under
  lineage overlap. **Acceptance test:** catch-rate on EAR-1-3 seeded errors,
  per error class, before removing EAR-0-1's lower-only clamp. *(NLP)*
- 🔒 **EAR-4-2** **[High]** = **EA4-2** remainder (gated on EAR-0-2):
  verified gap candidates spawn targeted re-extraction (route the named
  agent to that passage) → rows enter the normal confidence + review path.
  Review-volume budget per EA amendment #3: cap candidates/run, report
  inflow in run summary. *(NLP, BE)*
- ⏳ **EAR-4-3** **[High]** = **EA4-3** (pointer) + interim threshold raise:
  `triage_passage` (`section_triage.py`) trusts `not_relevant` at LLM
  confidence ≥ 0.4 from an 8B model — a terminal kill with no sampling
  audit (the 5% recall sampling audits *agent routing*, not triage
  rejections; nothing measures triage FNs today). Raise the trust threshold
  to 0.6 interim (recall-safe direction — more passages reach extraction;
  cost delta visible in run_summary) and land the 5–10% rejection-sampling
  audit. States regulate AI without saying "AI"; the `_ADJACENT_AI_KEYWORDS`
  set is exactly the vocabulary at risk. *(NLP, RPR)*
- 🔒 **EAR-4-4** **[High]** = **EA4-4** (pointer; gated on EAR-1-1 baseline).
  Re-review adds two aggravators to the original finding:
  (a) definition errors **propagate** — `defined_terms`/`bill_definitions`
  from definition_actor feed every other agent's context, so the weakest
  model anchors the whole bill's terminology; (b) temp 0.2 / top_p 0.7
  breaks re-run reproducibility — a defensibility cost independent of
  accuracy (the same document re-run should yield the same legal data).
  Move definition_actor + preemption to gpt-oss-120b, temp 0, top_p off.
  *(NLP)*
- 🔒 **EAR-4-5** **[Medium]** = **EA4-5** (pointer): dual-model agreement on
  matrix numerics; rename/split `model_agreement_count` first — today it is
  incremented by same-model duplicate emissions (extractor.py payload-hash
  path), which is agreement-washing, and exact-hash agreement essentially
  never fires across models on free-text payloads anyway. Field-level
  canonical comparison (normalized subject, modality, penalty ints, ISO
  dates) is the workable unit. *(NLP, BE)*
- ⏳ **EAR-4-6** **[Medium]** Unmatched-CV honesty: `unmatched_extraction_ids`
  (extractions the CV model returned no validation item for — never actually
  reviewed) are logged and then dropped; any "CV ran on this document"
  reading silently counts them as covered. Either re-run CV once for the
  unmatched batch or stamp `cv_status: unreviewed` on the extraction so
  coverage queries are honest. *(BE)*

### Phase EAR-5 — Prompt & schema hardening (eval-gated batch; after EAR-1)
- 🔒 **EAR-5-1** **[Medium]** Category-field vocabulary guards: `right_type`,
  `mechanism_type`, `threshold_type`, `exception_type`, `conflict_type`,
  `reference_type`, `consent_type` are free strings feeding the PN matrix —
  silent category forks ("opt-out" vs "opt_out") split aggregations. Add
  alias-normalizing validators (reuse the `actor_normalizer` +
  `vocab_loader` pattern); unknown values **pass through and enqueue** to
  `vocab_review_queue` — never reject, rejection drops legal data.
  Coordinate with 3f enum injection so prompts and validators cite the same
  ratified codes. *(NLP, BE)*
- 🔒 **EAR-5-2** **[Medium]** CV rulebook ↔ extraction canon alignment
  (+ EA6-4 trim, one measured prompt change): the CV prompt hard-codes
  "'shall' vs 'must' is NOT an error" while `_MODALITY_MAP` deliberately
  preserves the shall/must/may_not distinctions — extractor and verifier
  disagree about what a modality error *is*. Give CV the canonical modality
  equivalence table; land together with EA6-4's payload trim. *(NLP)*
- 🔒 **EAR-5-3** **[Medium]** Per-agent bill-context tailoring:
  `_append_bill_context()` sends definitions + scope + enforcement to all 6
  clause agents × every passage. Tailor: definition_actor needs the
  defined-terms list only; the enforcement block leaves the clause level
  entirely once EAR-2-2 lands; threshold/rights get scope. Token savings
  reported in run_summary; eval-gated because it changes model input.
  *(NLP)*
- 🔒 **EAR-5-4** **[Medium]** = **EA6-2 / SFH-3b** (pointer): NIM structured
  outputs; the 5-strategy repair chain becomes telemetry + local-provider
  fallback. *(NLP, BE)*
- 🔒 **EAR-5-5** **[High]** = **EA6-1** (pointer): implied-rights
  `derivation: textual | implied_from_obligation` field; RPR ruling
  required. *(RPR, NLP)*
- 🔒 **EAR-5-6** **[Low]** Tier-3/4 loose-match floor tuning, from EAR-0-7
  telemetry only (no guess-tuning); consider recording a similarity score
  per loose match so review can sort by match quality. *(NLP)*

### Phase EAR-6 — Reconciliation cleanups (low risk; anytime after EAR-0)
- ⏳ **EAR-6-1** **[Low]** Definition-dedup completeness reconciliation:
  QA-4's first-write-wins keeps the *first* copy even when a later
  near-duplicate is more complete (truncated-tail variants) — correct
  mid-run (payload-hash desync risk), wrong as a final state for legal
  reference data. Post-run pass promotes the most complete member of each
  near-duplicate cluster; safe once the run is finished. *(BE)*

**Sequencing:**
1. **EAR-0 lands first and entirely** — all eight items are pure code /
   telemetry / honesty fixes, unit-testable with mocked providers, no prompt
   or weight touched: the exact pattern EA0/EA2 landed under. EAR-0-1 is the
   most urgent (it closes an active tier-inflation path into the P3 publish
   gate).
2. **EAR-2-1 immediately after** (also sandbox-actionable, informational
   metadata only) and in parallel with EAR-1 — it is the hard prerequisite
   for EAR-3-1 and needs soak time on real runs before its signal drives
   weights.
3. **EAR-1 remains the long pole** and is operator-gated (live LLM + RPR
   annotation). Nothing in EAR-1-4/1-5, EAR-2-2, EAR-3, EAR-4-2/4-4, or
   EAR-5 moves before EAR-1-1. This is unchanged from the EA plan — two
   reviews in a row have now concluded the eval substrate is the binding
   constraint; treat that as settled.
4. **EAR-3 requires the contradiction-#1 product ruling** (tracker-first vs
   evidence-first), which now blocks three plans (EA3, SFH-3a, EAR-3).
   Escalate: this review adds the circularity finding (P-2) as a technical
   argument that tracker alignment *cannot* be the trust anchor while
   tracker text is also prompt input.
5. **Review-capacity budget** (EA amendment #3 applies): EAR-0-2, EAR-0-3,
   EAR-4-2, EAR-4-3, and EAR-4-6 all add review volume. Each states expected
   items/run at implementation time; total inflow stays capped (top-N by
   severity), queue age on the dashboard.
6. **Token-spend ledger:** EAR-4 raises per-law cost (bigger models on two
   agents, dual-model numerics); EAR-5-3 + EA6-4 claw some back. Capture
   $/law in `run_summary.json` before/after each change — the precision
   mandate covers the increase, but the trade stays explicit.
7. **Do-not list, carried forward:** no routing-threshold tuning without the
   gold set (EA0-2 discipline); no prompt edit without a baseline diff
   (amendment #1); no confidence reweighting before field-level grounding
   exists (amendment #2, now concretely EAR-2-1 → EAR-3-1).

---

## Law Card Dashboard Plan (LC) — Editable Per-Law Cards (2026-07-19)

> Full plan: [`docs/law_card_dashboard_plan.md`](docs/law_card_dashboard_plan.md)
> (bundle review, lifecycle trace, data model, per-phase a11y/testing/acceptance,
> decision table D-1…D-7). **Reactivates** the Run-1 plan's deferred "law-card data
> model" line item as a concrete feature: one editable dashboard card per extracted
> law — review the full extraction, edit fields with validation, compare phased
> runs, preserve source ↔ original ↔ edited lineage, usable by non-specialists.
> Status: ✅ done · 🔧 in progress · ⏳ ready · 🔒 gated. Effort in **( )**.
>
> **Bundle verdict (`Law Card Copy/`, reviewed 2026-07-19):** a read-only React 18
> component snapshot from ai-ethics-evaluator. It contributes a **design contract**
> (honest-unknown rules, paced disclosure, verbatim-vs-paraphrase semantics, status
> taxonomy, data-gap badges), **design tokens** (`lawcard-tokens.css`), and a
> **4-law test-fixture matrix** — but it has ZERO editing/validation/comparison
> code, does not run as shipped (broken internal imports: `../data/constants`,
> `../services/normalize`, missing `CoverageCard` + `supabase.js`;
> `priority.js` imports constants the bundle doesn't export), and its data shapes
> don't match ours. Decision D-2: port the design system into the existing
> Jinja2/HTMX stack; do NOT introduce a React island for one page.
>
> **Two pre-existing defects this plan owns fixing:**
> - **G-1 (Critical):** `POST /api/review/{queue_id}/edit` (`review_routes.py`)
>   mutates `extraction.payload` in place — the model's original output is
>   destroyed on first edit; `payload_hash` desyncs the dedup unique index; no
>   schema validation; spans never re-verified; edits sync to PN as if
>   model-produced. Fixed in LC-1 (immutable base + edit overlay).
> - **G-2 (Critical):** full runs purge all extractions (`run_extraction(purge=
>   True)`), which makes cross-run comparison impossible AND would delete human
>   edits. `run_id` + `is_serving` already exist for retention (Run-1 1b deferred
>   the query refactor). Fixed in LC-4 behind decision D-1.
>
> **Blocking decisions (LC-0 resolves; recommendations in the doc):** D-1 run
> retention (recommend: keep last 3, serving-run scoping) · D-2 stack (HTMX port)
> · D-3 edit storage (immutable base + `ExtractionFieldEdit` overlay +
> `effective_payload`) · D-4 edits never alter confidence tier (separate
> `human_review_state`; precedence applied at sync, human > orrick) · D-5 edit
> survival across runs (hash-match carry-forward, else orphan + review item) ·
> D-6 interim editor identity + CSRF (full auth stays Run-1 6a) · D-7 bill-level
> payloads read-only in MVP.

### Phase LC-0 — Repository alignment & technical discovery (S) — ✅ LANDED 2026-07-19
- ✅ **LC-0a** — `docs/law_card_decisions.md`: D-1…D-7 ratified with working
  resolutions; D-1/D-4/D-6 explicitly flagged as provisional pending
  product-owner sign-off (none present in the implementation session), with
  the rework blast-radius of each spelled out.
- ✅ **LC-0b** — `Law Card Copy/` → `reference/law_card_bundle/`;
  `lawcard-tokens.css` → `static/css/`; `fixtures/refLaws.js` → `tests/fixtures/
  law_cards/*.json` (converted via a Node dump script, byte-identical to
  source, not hand-transcribed).
- ✅ **LC-0c** — `docs/law_card_design_rules.md` written as 9 numbered,
  testable assertions. Corrects the plan's dark-scheme assumption: the real
  dashboard theme (`static/css/style.css`) is light, not dark — no
  reconciliation needed.
- ✅ **LC-0d** — card-JSON spike run against real committed data (`fact_laws
  .csv` row 48 + 11 CO SB205 gold-standard fixtures; no live DB reachable in
  the planning session). Findings folded into the plan doc and directly
  informed LC-1c: `status_id` blank for real in-force laws (confirms
  `DocumentVersion.temporal_status`'s own non-null default already solves
  this at the DB layer — no CSV-side inference needed in the assembler),
  `iapp_scope`/`iapp_section` need their own card surface, clause- vs.
  bill-level enforcement confirmed as genuinely separate data paths.

### Phase LC-1 — Law-card data model & API foundation (M) — ✅ LANDED 2026-07-19 (the keystone; fixes G-1)
> **Implementation note:** this phase was built and verified with a full
> local dev environment (venv + pinned deps + a real Postgres 16 instance)
> that no prior session in this repo's history had access to — every
> migration and every DB-touching code path below was verified against a
> live database (up/down/re-up round trips, real seeded data, real FastAPI
> `TestClient` calls), not just unit-tested with mocks. 1686/1686 tests
> passing across the phase (baseline 1549 + 137 new); CI's blocking `E9,F`
> ruff gate clean throughout.
- ✅ **LC-1a** — migration `72ad4147a628`: `extraction_field_edits`
  (canonical_key + extraction_identity for run survival, field_path,
  old/new JSONB, required reason, status proposed|applied|reverted|
  superseded|orphaned with a partial unique index enforcing one active
  edit per field, validation_report, editor, lock_token),
  `law_card_states` (per law × run rollup + invalidatable card cache),
  `extractions.effective_payload` + `human_review_state` (base `payload`
  now write-once) + `Extraction.current_payload` property. Live-verified:
  full 40-migration chain applied clean to a scratch DB, then this
  migration's own upgrade/downgrade/re-upgrade round-trip — which caught
  and fixed a real SQLAlchemy postgres-ENUM double-create bug
  (`create_type=False` needed when an enum is both pre-created and
  referenced in the same `create_table`). 20 tests.
- ✅ **LC-1b** — `src/core/field_catalog.py`: plain-language label/help/
  widget/material-flag registry for all 19 Pydantic models (6 clause
  payloads + nested) via schema reflection (`iter_schema_models`/
  `iter_schema_fields` walk `EXTRACTION_TYPE_SCHEMAS` through Pydantic's
  own `model_fields`, so coverage tracks the real schemas, not a mirror
  list). The reflection-based coverage test caught two real bugs while
  authoring: `EvidenceSpan`'s own fields weren't cataloged (added a new
  `READONLY` widget — evidence quotes are proof, never hand-edited to
  match a corrected fact) and `ObligationPayload.object_`'s alias mismatch
  (Pydantic exposes the Python attribute name, but `model_dump(by_alias=
  True)` — what's actually persisted — writes the key as `"object"`;
  `iter_schema_fields` now resolves each field's storage alias). Verified
  against real gold-standard extraction payloads with zero unresolved
  fields. `material_fields_for()` implements the EAR-2-1 material-field
  spec. 25 tests.
- ✅ **LC-1c** — `src/core/law_card_assembler.py` (assembles law + run +
  bill-level + extractions w/ field_catalog-annotated fields, per-field
  evidence honoring verification tier, `render_hint` computed server-side
  per Design Rule 7) + `src/core/edit_service.py` (propose → dry-run
  validate per-widget incl. `check_numeric_grounding` warnings → apply w/
  optimistic lock → revert; `effective_payload` always rebuilt from the
  immutable base + every applied edit replayed in order). Live-DB
  verification caught a real bug: `SessionLocal` runs with `autoflush=
  False` project-wide, so the recompute's own query for "applied" edits
  was running against pre-update DB state and silently missing the edit
  just applied in the same call — `apply_edit` reported `success=True`
  while the payload underneath never changed. Fixed with an explicit
  `db.flush()`; a unit-test-only (mocked-session) suite would not have
  caught this. 32 tests (19 unit incl. a parametrized drift-check pinning
  edit_service's and the assembler's independent
  `ExtractionType → catalog-model` mappings to agreement; 13 integration
  against real Postgres).
- ✅ **LC-1d** — `src/api/routes/law_card_api.py`: `GET /api/laws`, `GET
  /api/laws/{key}/card`, `POST .../extractions/{id}/validate` (dry-run
  "Check"), `POST .../extractions/{id}/edits` (propose+apply in one call —
  "Save"), `POST /api/edits/{id}/revert`. New module, mounted directly in
  `app.py` — `dashboard.py` (6,424 lines) was not touched.
  `_load_extraction_for_law` guards against a cross-law extraction id
  mismatch (400, not a silent wrong-law write). 12 integration tests
  against a real `TestClient` + live Postgres; required a fixture fix
  mid-authoring (unique canonical_key per test run, since the routes
  correctly `db.commit()` and a fixed key collided with the prior run's
  committed row).
- ✅ **LC-1e** — **both destructive edit paths killed** (a grep-audit found
  a second one beyond the plan's named target): `review_routes.py`'s
  `POST /api/review/{id}/edit` AND `internal.py`'s `POST /review/queue/
  {id}/action` (approve-with-corrections) both reimplemented on
  edit_service — same external contracts, zero prior test coverage on
  either before this. `apply_edit`/`revert_edit` now write the audit
  `ReviewAction` centrally (using the previously-unused `corrections`
  JSONB column) instead of each call site remembering to. Consumer sweep
  went beyond the three named targets after a full grep-audit of every
  `.payload` site in `src/`: `sync_extractions.py`'s 3 raw-SQL SELECTs →
  `COALESCE(effective_payload, payload)`; the 3 product-serving
  materialized views + dependency-tree query (`src/db/views.py` +
  migration `4457bebc03c0` — materialized views can't `CREATE OR REPLACE`,
  so this needed a real drop+recreate, live-verified end-to-end including
  firing the actual `trg_refresh_on_review` trigger and confirming
  `served_obligations` showed a corrected value with the stale original
  gone); `concept_grouping.py`, `condition_parser.py`,
  `summary_generator.py` (one-line `current_payload` switches);
  `enforcement_normalizer.py` (`func.coalesce()`, a raw-column SELECT not
  an ORM instance); `dashboard.py`'s 4 export/display sites. Explicitly
  scoped out and documented, not silently skipped: `verification_runner
  .py`'s CV/gap-detection reads (touches confidence-recompute semantics —
  a separate decision), `extractor.py`'s live dedup and `run_archiver.py`'s
  export snapshots (both are point-in-time records of what the pipeline
  produced, same reasoning as why `payload` itself stays write-once),
  `manual_extraction.py`'s CLI dedup check. `rollup_matrix.py` needed no
  direct fix — it reads Policy Navigator's own `rollup_eligible_extractions`
  view (lives entirely outside this repo, fed by `sync_extractions.py`),
  so the sync fix already makes it edit-aware transitively. 8 new
  integration tests (`test_g1_fix_e2e.py`).
  **Deferred to product/operator, not done here:** `edited_by_analyst`
  sync provenance stamp (P3-gate coordination) — the remote
  `sync_extractions.py` COALESCE fix requires the LC-1a migration applied
  to the Regs Checker Supabase target first, an operator action this
  sandbox can't perform.

### Phase LC-2 — Read-only law-card dashboard (M)
- 🔒 **LC-2a** *(after LC-1)* — `src/api/routes/law_card_routes.py` +
  `templates/laws.html` (list: search/filter via `law_card_states` rollup — no
  N+1; query-count test) + `templates/law_card.html` (tabs: Overview |
  Extractions | Runs-placeholder). Behind `law_cards_enabled` flag. *(BE, FE)*
- 🔒 **LC-2b** — ported design system as Jinja2 partials:
  `partials/lc_badges.html` (status taxonomy, tier chips, data-gap,
  truncation/repair, tracker-status, provenance line), `lc_extraction_panel.html`;
  evidence rendering honors verification tiers — Tier-1/2 spans as highlighted
  quotes (char offsets exist per EA2-2), Tier-3/4 marked "near match",
  unverified NEVER rendered as a quote. Bill-level panel read-only (D-7) incl.
  `_input_truncated` warning. `static/lawcard.js` (~100 lines vanilla:
  aria-expanded toggles + focus return). *(FE)*
- 🔒 **LC-2c** — template tests asserting the LC-0c design rules (null → gap
  badge, withdrawn → enforcement suppressed, honest-unknown throughout) against
  the four ported fixtures + real CO SB205 data; a11y: keyboard-complete
  disclosures, no color-only encoding, contrast ≥ 4.5:1, 200%-zoom reflow. *(FE, BE)*

### Phase LC-3 — Field-level editing & validation (M/L) — MVP completes here
- 🔒 **LC-3a** *(after LC-2)* — `partials/lc_field_editor.html`: widget per
  field_catalog (vocab selects w/ unknown → vocab-review enqueue, date input w/
  normalize-on-blur, number+unit, textarea; nested timeline/enforcement as
  grouped sub-forms — NO raw JSON for cataloged fields). HTMX flows: Check
  (dry-run → inline plain-language messages), Save (required reason, edited
  chip, view-original/revert), numeric-vs-span warning shows the quote. *(FE, NLP)*
- 🔒 **LC-3b** — editor identity + safety: reviewer-name session (D-6), CSRF on
  all mutating routes, optimistic-lock conflict UX ("someone else changed
  this…"), unsaved-edit navigation guard. `review.html` gains an "edited"
  filter; approval of an edited extraction records it covers the edited state.
  *(BE)*
- 🔒 **LC-3c** — acceptance gate: a non-specialist corrects a penalty amount, a
  date, a modality, and a nested enforcement field on real data using only
  on-screen guidance; originals recoverable; audit trail carries identity.
  Validation-message copy externally read-through. *(RPR/operator, FE)*

### Phase LC-4 — Phased-run comparison & change visualization (M/L; 🔒 gated on D-1)
- 🔒 **LC-4a** — retention refactor: full runs stop purging; serving-run scoping
  on dashboard stats / review / concepts / sync; `prune_runs(keep=3)`; behind
  `multi_run_retention` flag with pre/post count-audit script (highest-risk
  change in the plan — flips the "all rows are current" invariant). Finishes
  Run-1 1b's deferred query refactor. *(BE, DevOps, operator)*
- 🔒 **LC-4b** — `src/core/run_comparison.py`: cross-run extraction matching
  (type + canonicalized material fields; reuse `_payload_hash` canonicalization
  + QA-4 similarity) → added/removed/changed with field-level deltas; compares
  BASE payloads, edits overlaid as a separate annotation (model-change vs
  human-edit never conflated); match confidence surfaced, not overclaimed
  ("possibly the same requirement, reworded"). Adversarial tests: split 1→2,
  merged 2→1, reworded, retagged. *(NLP, BE)*
- 🔒 **LC-4c** — edit carry-forward (D-5) on run finalize: payload_hash match →
  carry edits to new row; changed → status=orphaned + review item ("law text
  changed — re-apply?"). Never silent-drop, never silent-apply. *(BE)*
- 🔒 **LC-4d** — Runs tab UI: run picker (date, model summary, serving badge),
  per-law change summary, `partials/lc_diff_row.html` in three states (icon +
  text + color, never color-only), "only changes" default, side-by-side values
  stacking at narrow widths. *(FE)*

### Phase LC-5 — Accessibility & non-specialist usability hardening (M)
- 🔒 **LC-5a** *(after LC-2/3)* — full WCAG 2.2 AA manual audit (keyboard, screen
  reader, zoom/reflow, contrast) with recorded checklist + fix list; `aria-live`
  validation announcements, focus-to-first-error, `prefers-reduced-motion` /
  color-scheme resolution per LC-0c. *(FE, operator)*
- 🔒 **LC-5b** — glossary layer from field_catalog (every specialist term gets a
  hover/expand definition; "What am I looking at?" panel per tab); plain-language
  pass over every label/help/error/empty state with external read-through; undo
  toast after apply (calls revert). *(FE, RPR)*

### Phase LC-6 — Testing, migration, rollout, monitoring (M)
- 🔒 **LC-6a** *(MVP rollout after LC-3)* — `src/scripts/backfill_law_cards.py`
  (idempotent `law_card_states` for serving run, all 232 laws); flag-on bake
  period with the analyst doing real review work in LC UI (≥3 laws end-to-end);
  legacy review edit form redirected to LC editor; then default-on. *(BE, operator)*
- 🔒 **LC-6b** — monitoring: edits/day, validation-failure rate by field (rising
  rate on one field = catalog or model problem), orphaned-edit count per run,
  card-assembly latency, lock conflicts — existing dashboard-stats pattern +
  `run_summary.json` where run-coupled. Data-integrity sweeps in CI: no
  `effective_payload` without applied edits, no applied edit without overlay,
  every edit resolvable to a law via canonical_key. *(BE, DevOps)*
- 🔒 **LC-6c** — docs: analyst guide (non-specialist voice), operator runbook
  (retention/prune/backfill), `architecture.md` section. *(BE)*

**Sequencing & MVP boundary:** LC-0 → LC-1 → LC-2 → LC-3 = **MVP** (every law
browsable + editable with validation/identity/audit/revert, serving run only) →
LC-6a rollout → LC-4 (comparison; needs D-1 + an operator run under retention) →
LC-5 polish → LC-6 full. **LC-1 is worth shipping even if the UI slips** — it
fixes the active G-1 destructive-edit defect. Deferred past MVP: run comparison,
bill-level editing (waits on EA5-1/EAR-2-3 per-field spans), triage-engine port,
role-based filtering, concept-layer cards, full authn/z (Run-1 6a).
**Coordination:** don't duplicate EAR-0-4 (provenance stamps) or EAR-5-1 (vocab
aliases) — the card consumes both when they land; `dashboard.py` is never grown
(split remains deferred); P3 tier-only sync gate is why D-4's
`edited_by_analyst` provenance stamp is non-optional.

> **Status (2026-07-19, implementation session):** LC-0 and LC-1 both fully
> landed and live-DB-verified (commits: LC-0 repo alignment, then four
> LC-1 commits — data model/field catalog, assembler/edit service,
> JSON API, consumer sweep). G-1 is fixed at both sites it existed. **Next
> up: LC-2** (read-only dashboard templates) is now unblocked — it's pure
> Jinja2/HTMX template work with no DB-migration risk, a natural next
> session's starting point. Two operator actions this session could not
> perform: (1) apply migrations `72ad4147a628` + `4457bebc03c0` to the real
> dev/prod database (`python start.py` → `alembic upgrade head`, per
> CLAUDE.md) — everything above was verified against a disposable local
> scratch Postgres this sandbox stood up itself, not the project's real DB;
> (2) product-owner sign-off on decisions D-1/D-4/D-6
> (`docs/law_card_decisions.md`), currently shipped under the documented
> provisional resolutions.

---

## Repository Cleanup Plan (RC) — from 2026-07-03 audit report

> Source: external cleanup-audit report (backups, archived code, docs sprawl,
> static/data mixing). The report itself flags most findings as needing
> verification ("likely safe but needs verification", "unknown / requires
> human confirmation") — before turning it into a plan, every specific,
> checkable claim was verified against the actual repo (import scans, git
> tracking status, CI config, template nav links, secret grep). Findings
> below are marked **confirmed** (verified this session), **corrected**
> (audit was imprecise or stale), or **unverifiable here** (needs live DB,
> external storage, or ops/deployment access this session doesn't have).
> Status legend: ✅ done · 🔧 in progress · ⏳ ready · 🔒 gated. Severity in **[ ]**.

### Verification corrections to the source report (read before acting on it)
- **`src/ingestion/_archived/` is not one bucket.** Individually import-scanned
  all 8 files: **5 are confirmed zero-import dead code**
  (`ambiguity_agent.py`, `connector.py`, `discovery.py`, `verification.py`,
  `web_search.py` — none imported from any live, non-archived path). **2 are
  confirmed load-bearing**: `pdf_tracker.py` (imported by
  `dashboard.py:1113`, `seed_pipeline.py:254,279`) and `iapp_pdf_tracker.py`
  (imported by `dashboard.py:4668`, `status_checker.py:277`). The audit's
  "treat each module as live until proven otherwise" was the right caution
  but left the actual per-file disposition undone — RC3-3 below does it.
- **`compare_models` is confirmed broken, not just suspicious.**
  `dashboard.py:4385` (`/dashboard/api/run/compare-models`, wired to a live
  HTMX button in `templates/analytics.html:99`) imports
  `src.evaluation.compare_models`, which does not exist —
  `src/evaluation/` only contains `harness.py`. The only `compare_models.py`
  is in `_archived/`, and it is **itself doubly broken**: it imports
  `AnthropicProvider` (fully removed per `llm_provider.py`'s own docstring —
  "The Anthropic API provider has been archived") and `AmbiguityAgent`
  (only exists in `src/ingestion/_archived/ambiguity_agent.py`, itself
  zero-import dead code). Restoring it is not a cleanup action, it's a
  from-scratch rewrite against the current 9-agent NVIDIA/local
  architecture — see RC2-1.
- **`prompts/dependency_graph.yml` is confirmed live**, not a deletion
  candidate — loaded by `dependency_builder.py:250`
  (`load_prompt_template("dependency_graph")`), called from
  `extractor.py`'s `run_dependency_graph`. The audit hedged correctly here
  ("weak-medium... dynamic/manual runs possible"); do not delete.
  `prompts/ambiguity.yml` by contrast is confirmed dead (no live agent has
  `agent_name = "ambiguity"`) — safe to remove.
- **`archive/` already has an index** (`archive/README.md`) mapping every
  retired doc/handoff to why it was retired and what superseded it — the
  audit's "heavy historical duplication... need an index" applies to the
  *active* `docs/` directory (18 files, no README), not `archive/`, which
  is already in good shape and a good model to copy.
- **Backups are schema-only, not data dumps** — grepped both `.sql` files
  for `INSERT INTO`/`COPY` (0 matches each). Still a real infra-detail leak
  (full table/RLS/grant structure, including an `api_keys` table with a
  `key_hash` column), just a smaller blast radius than a row-data leak.
  Both files are confirmed `git ls-files`-tracked, as is
  `output/law_texts_quarantine/` (30 tracked files). Neither path is in
  `.gitignore` today.
- **`static/` tracker files are confirmed live ingestion inputs**, not
  orphaned assets — referenced by `dashboard.py`, `seed_pipeline.py`,
  `iapp_alignment.py`, and `config.py`. Genuinely mis-located (mixed into a
  directory whose other job is serving web assets via the FastAPI static
  mount), not dead.
- **CI lint gate confirmed exactly as described**: `ci.yml` hard-gates only
  `--select E9,F` (excluding both `_archived` dirs); full `ruff check` runs
  with `|| true` (advisory, cannot fail the build).
- **All 7 dashboard templates confirmed live** — each is linked from
  `layout.html`'s nav bar to a real route. No template deletion candidates.

### Phase RC0 — Documentation and labeling (zero code risk; do first)
- 🔧 **RC0-1** **[Low]** `docs/README.md` written (2026-07-04) —
  evidence-based classification of all 18 files, mirroring
  `archive/README.md`'s table format. 12 classified **current** with the
  evidence stated per row (5 active plans genuinely cross-referenced from
  `tasks.md` — the others' single tasks.md "citations" turned out to be
  this RC0-1 entry's own file list, so content/companion evidence was
  used instead; 3 completion-log records; 4 reference/operational inputs,
  including `missing_laws_ingest_queue.csv` which is the still-open task
  1d worklist). 6 flagged **archive candidate, explicitly marked
  unconfirmed** rather than guessed: `pipeline_rebuild_plan.md` +
  `taxonomy_dev_plan.md` (working-draft proposals whose companion docs
  are already archived or absent — product call on whether the
  rebuild/redesign path is still live), `code_update_strategy_eng.md` +
  `vocab_harvest_spec_eng.md` + `actor_taxonomy_analysis.md` (pre-v3 /
  completed-decision inputs), `product_review_remediation_plan.md`
  (operator call: absorbed or still open?). Left 🔧 not ✅: the item's
  whole point was that ambiguous ones need a human ruling — the index
  exists and is useful now, but the 6 unconfirmed rows (plus the
  `data_dictionary.pdf` duplicate question) await confirmation, which
  then unblocks RC3-2. *(product/tech lead: confirm the 6 flagged rows)*
- ⏳ **RC0-2** **[Low]** Label one-off scripts with an owner-facing status
  comment (`# STATUS: active runbook` / `one-time, completed <date>` /
  `archive candidate`): `scripts/fix_csv_titles.py`,
  `scripts/fix_mismatched_sources.py`, `scripts/reset_pipeline.py`,
  `scripts/debug_pdf_tables.py`, `scripts/apply_pending_migrations.sql`.
  Static analysis can't tell one-time-completed from still-needed for these
  — needs the operator who ran them. *(operator input needed)*

### Phase RC1 — Safe removals (git-tracking changes; needs a retention decision)
- ⏳ **RC1-1** **[Medium — security-adjacent]** Untrack `backups/*.sql`
  (2 files, confirmed schema-only, confirmed git-tracked). **Blocked on a
  step this session cannot perform**: copying the files to private/secure
  storage first requires a destination outside this sandbox — the operator
  must do that copy. Once confirmed safe to lose from the working tree,
  `git rm --cached backups/*.sql` + add `backups/` to `.gitignore` removes
  them going forward. **Note the distinction**: this does NOT purge git
  *history* — the files remain retrievable from prior commits. A full
  history purge (`git filter-repo` / BFG) is a separate, much more
  destructive decision this plan does not recommend without explicit,
  separate confirmation — untracking going forward is the low-risk action;
  history rewriting is not. *(operator: secure storage step; explicit
  confirmation before any history rewrite)*
- ✅ **RC1-2** **[Low]** Executed (2026-07-04). All 30 files untracked via
  `git rm --cached` (working-tree copies untouched — nothing deleted from
  disk) and `output/law_texts_quarantine/` added to `.gitignore`.
  Pre-checked the ingest path: `local_ingest.py` only *writes* to the
  quarantine dir (`_quarantine_file()` moves bad files in and appends to
  `NEEDED_SOURCES.md`); ingestion reads exclusively from
  `output/law_texts/` / `output/law_sources/`, so nothing re-globs the
  quarantine path. Note: `NEEDED_SOURCES.md` (the operator-facing "these
  laws need replacement sources" ledger) was untracked along with the
  rest — it still exists locally and regenerates at runtime, but if it
  should stay version-controlled, re-add it with
  `git add -f output/law_texts_quarantine/NEEDED_SOURCES.md`. Reversible
  via `git revert`. *(done)*

### Phase RC2 — Broken-feature decisions (product input needed)
- ⏳ **RC2-1** **[Medium]** `compare_models` broken endpoint — confirmed
  live, user-facing, and broken (see verification note above). Two options,
  need a product call: **(a)** remove the button in `analytics.html:99`
  and the route in `dashboard.py:4382-4386` (low-risk, reversible, matches
  "prefer deleting broken feature entry points over restoring dead code"
  from the source report); **(b)** treat "compare local models against
  each other" as a live feature request and rewrite it fresh against the
  current NVIDIA/local 9-agent architecture + `EvaluationHarness` (a real
  scoped feature, not a cleanup task — would reuse `harness.py`'s
  precision/recall machinery rather than the archived ad-hoc comparator).
  *(product decision)*
- ✅ **RC2-2** **[Low]** Executed (2026-07-04). `prompts/ambiguity.yml`
  deleted. Re-verified before deleting: after RC3-3 removed
  `ambiguity_agent.py`, the only remaining `load_prompt_template()` callers
  are `base.py` (loads by live `agent_name` — none is "ambiguity") and
  `dependency_builder.py` (loads "dependency_graph" — the RC2-3 do-not-touch
  file). `ExtractionType.ambiguity` DB enum untouched, per the existing
  read-only decision. *(done)*
- ✅ **RC2-3** **[Info]** `prompts/dependency_graph.yml` — confirmed live,
  explicitly marked **do not touch**. No action; recorded here so it isn't
  re-flagged by a future pass.

### Phase RC3 — Consolidation (scoped multi-file changes)
- ✅ **RC3-1** **[Medium]** Executed (2026-07-04). All 6 source-data files
  moved (`git mv`, history preserved) from `static/` to `data/trackers/`;
  `static/` now contains only the real web asset (`css/style.css`). All
  references updated atomically in one commit, each verified
  exactly-one-occurrence before replacing: `config.py` (the two
  `*_pdf_path` setting defaults — note these settings have **zero readers**
  in live code, the modules hard-code their own constants; updated anyway
  since they're operator-overridable env config), `iapp_alignment.py`
  (`_IAPP_CSV`), `legacy/pdf_tracker.py` (`PDF_PATH`),
  `legacy/iapp_pdf_tracker.py` (`IAPP_PDF_PATH`), `seed_pipeline.py`
  (csv_path), `dashboard.py` (two operator-facing "place the PDF at ..."
  message strings), `scripts/debug_pdf_tables.py` (4 refs),
  `test_iapp_alignment.py` (comment), and `docs/remediation_plan.md`'s
  still-actionable P4-1 step (historical references in
  `run1_unified_plan.md` left as-is — that's a record of past state, and
  its "not ingested" status text is stale independently). Smoke test in
  lieu of a live ingest run (no DB in this sandbox): every moved path
  constant verified to resolve on disk via import + `.exists()`, plus
  `test_iapp_alignment.py`'s live-lookup tests, which read the actual
  moved CSV through `_IAPP_CSV`. Confirmed no template links or FastAPI
  static-mount consumers reference the moved files (the `/static` mount
  serves only css now). 1068/1068 tests passing. *(done)*
- ⏳ **RC3-2** **[Low]** Build `docs/README.md` (RC0-1) then move any
  docs/*.md confirmed superseded into `archive/docs/`, following the
  existing `archive/README.md` table pattern exactly (retired file → why →
  what superseded it). Gated on RC0-1's classification pass.
- ✅ **RC3-3** **[Medium]** Executed (2026-07-04) exactly as scoped:
  `pdf_tracker.py` and `iapp_pdf_tracker.py` moved to
  `src/ingestion/legacy/` (the codebase's existing old-but-still-used
  convention), all 5 call sites updated and re-verified importable from
  the new path, the 5 zero-import files + package `__init__.py` deleted,
  directory removed. Two things the scoping pass hadn't caught, found
  during execution: **(1)** the moved `pdf_tracker.py` carried an unused
  `xml.etree.ElementTree` import (F401) that was invisible while the file
  sat inside CI's lint-exclude but would have **failed the hard E9,F gate**
  the moment it moved — removed as part of the move; **(2)** separately,
  the hard gate was *already red* on 4 pre-existing errors in live code
  (unused imports in `base.py`/`orrick_facts_parser.py`/
  `reground_spans.py`, an F541 in `dashboard.py`) — verified against the
  pre-branch baseline via a temp worktree that all 4 pre-date this
  branch's work, fixed in their own commit so CI is green. Follow-on:
  `ci.yml`'s two `--exclude` flags dropped (`src/_archived` never existed
  under `src/`; `src/ingestion/_archived` is now gone — both were no-ops),
  and `CLAUDE.md`/`architecture.md`/`test_orrick_scraper.py` updated to
  stop pointing at the deleted directory. Moved-file docstrings now state
  their legacy-but-load-bearing status and who imports them. 1068/1068
  tests passing; exact new CI gate command verified passing on the full
  tree. *(done)*

### Phase RC4 — High-risk, environment-gated
- ✅ **RC4-1** **[High]** Executed (2026-07-06). Retired the four raw-SQL
  fallback helpers (`_ensure_extraction_enums` /
  `_ensure_failed_attempts_table` / `_ensure_pipeline_events_table` /
  `_ensure_triage_table`) and all call sites (`run_extraction`,
  `run_retry_failed`, and the two dashboard triage-reset endpoints
  `reset_triage`/`reset_triage_all`). **Supabase half of the schema-drift
  sweep done via Supabase MCP:** Regs Checker Supabase
  (`wjxlimjpaijdogyrqtxc`) is at Alembic head `25cffe678fbc` with zero
  drift — every enum value (`rights_protection`/`compliance_mechanism`/
  `preemption_signal`), table (`failed_extraction_attempts` incl. `run_id`,
  `pipeline_events`, `section_triage_results`), and triage enum
  (`triagedecision`/`triagemethod`) the helpers guaranteed is present.
  Policy Navigator Supabase (`aaxxunfarlhmydvohsrm`) is a separate product
  DB not on this repo's Alembic history — out of scope. **Local Docker
  Postgres** was unreachable from the sandbox, but `start.py` runs
  `alembic upgrade head` + verifies head before serving (the helpers were
  its documented "runtime patches as fallback"), so the local path is
  covered by real migrations. Operator chose to proceed on that basis
  (residual risk is a dev calling `run_extraction` outside `start.py` on a
  stale local DB — now a clear error instead of self-heal). 1071 tests
  passing; CI hard gate (`ruff check src/ --select E9,F`) green. *(done)*
- ✅ **RC4-2** **[High]** Executed (2026-07-06). Deleted
  `scripts/apply_pending_migrations.sql` — every migration it applied
  (`document_families.primary_source_url`/`orrick_reference_url`/
  `iapp_reference_url`, `ingestion_jobs.ai_suggested_url`, and the
  `requires_manual_review` enum value) verified already present on Regs
  Checker Supabase via the same MCP sweep. Historical retirement note added
  to the `bf74ef19697d` migration docstring so it doesn't point at deleted
  code. *(done)*
- ✅ **RC4-3** **[Low]** Executed (2026-07-06). `_archived/dagster_pipelines/`
  deleted after operator confirmed no external Dagster deployment points at
  this code (the missing piece static analysis couldn't see; repo-side was
  already a clean zero-reference grep across `src/`, `scripts/`, `.github/`,
  `docker/`). *(done)*

**Sequencing:** RC0 first (no risk, clarifies everything downstream). RC1-2
and RC3-3 are verified-safe enough to execute directly once acknowledged —
everything else in RC1/RC2/RC3 needs one external input (secure storage
destination, product decision, or a coordinated multi-file path update) before
acting. RC4 was originally deferred for live DB/ops access; the Supabase half
was ultimately reachable via Supabase MCP (see the 2026-07-06 note below).

**Execution note (2026-07-04):** RC1-2, RC2-2, and RC3-3 executed this
session (details on each item above), plus an unplanned fix for 4
pre-existing CI hard-gate lint failures discovered while verifying RC3-3's
lint-exclude removal. **Still open, with their blockers:** RC0-1/RC0-2
(need operator/product classification of docs and one-off scripts), RC1-1
(needs the operator to copy `backups/*.sql` to storage outside this repo
first; untracking is ready to run the moment that's confirmed), RC2-1
(product decision: delete the broken compare-models button vs. rewrite the
feature against the current architecture), RC3-2 (gated on RC0-1's
classification), RC4-1/4-2 (live DB schema sweep), RC4-3 (ops confirmation
no external Dagster deployment exists). RC3-1 executed in a follow-up pass
the same day (see its entry above) — with that, every RC item executable
in this sandbox is done; all remaining items need operator, product, or
ops input.

**Execution note (2026-07-06):** operator-directed follow-up session
cleared most of the remaining backlog. **RC2-1** (deleted the broken
compare-models button + endpoint), **RC0-1/RC3-2** (archived
`code_update_strategy_eng.md`, `actor_taxonomy_analysis.md`,
`vocab_harvest_spec_eng.md` to `archive/docs/` per operator classification),
**RC1-1** (untracked `backups/*.sql` — operator confirmed secured
externally), and **RC4-3** (deleted `_archived/dagster_pipelines/` — operator
confirmed no external scheduler) all executed. **RC4-1/RC4-2** unblocked by
running the schema-drift sweep through Supabase MCP rather than direct DB
connectivity: both Supabase projects verified, `start.py` covers the local
Docker path, operator approved proceeding — raw-SQL fallbacks and the manual
migration script retired. Remediation **P3** (tier-only publish) also shipped
with an explicit `review_status != 'rejected'` gate. **Still open:** RC0-1's
remaining doc classifications (`pipeline_rebuild_plan.md`, `taxonomy_dev_plan.md`,
`product_review_remediation_plan.md` — product call still pending), and the
local Docker Postgres leg of the RC4 sweep (informational only now — the
fallbacks are already gone; run `alembic current` locally to confirm head).

---

## Remediation Plan — Engineering Review Findings (RR)

> Synthesis of two independent reviews: the **agent-engineering review**
> (verification trust + normalization) and the **code-audit review** (idempotency,
> parser fidelity, API safety, pipeline state). The two are *complementary* — the
> first caught the runtime verification fail-open; the second caught the
> idempotency/purge/parser issues. Neither caught both. Treat as one backlog.
> Status legend: ✅ done · 🔧 in progress · ⏳ ready · 🔒 gated. Severity in **[ ]**.
>
> **⚠️ Coupling note (most important):** RR1a (idempotency) and RR1b (auto-purge)
> are **one change, not two**. Full runs (`limit=None`) purge-then-redo, so a
> *clean complete* full run masks the skip-missing-agents bug; the bug bites
> limited runs and interrupted full runs. Removing the purge (RR1b) **without**
> per-agent dedup (RR1a) makes full re-extraction a silent no-op, because the
> candidate query `Extraction.id.is_(None)` (extractor.py:1876) excludes every
> already-touched passage. Land them together.

### Phase RR0 — Already landed this session ✅
- ✅ **RR0.1** **[Critical]** Verification fail-closed. CV/gap agents unpacked
  `provider.call()` as a 4-tuple but it returns `LLMResponse` → `TypeError` on
  every call, swallowed by `except` → fake neutral passes wired into confidence +
  `extraction_verification_status`. Fixed: consume `response.text/.usage/.model_id`;
  added explicit `status` (completed/skipped/failed) to both summaries; caller
  gates on `status=="completed"`; `cv_passages_failed`/`gd_passages_failed`
  persisted (model + migration `q3r9s5t7u018`); tests now mock real `LLMResponse`
  + fail-closed regression + malformed-JSON test. *(NLP, BE)*
- ✅ **RR0.2** Phase 5 concepts UI: `/dashboard/concepts` page, Group-Concepts
  pipeline step (4.75), Verify step (4.25) wired to `/api/verify`, concept review
  queue + filterable table + resolve endpoint. *(FE, BE)*
- ✅ **RR0.3** **[Critical, follow-up]** Backfill/invalidate verification data
  written by the pre-RR0.1 broken path: `verification_run_summaries` /
  `extraction_verification_status` rows and confidence tiers recomputed against
  fabricated CV passes are stale. `src/scripts/backfill_verification.py` restores
  `confidence_before`/`tier_before` on each linked Extraction, then deletes all
  `ExtractionVerificationStatus` + `VerificationRunSummary` rows. Supports
  `--dry-run`. *(BE)*

### Phase RR1 — Pipeline integrity (do first; RR1a+RR1b coupled)
- ✅ **RR1a** **[Critical]** Fix extraction idempotency. Fixed candidate query
  (removed `Extraction.id.is_(None)` filter — all triaged-relevant passages
  now enter the pipeline). Fixed `existing_hashes`: now keyed on
  `(agent_name, passage_text)` per-extraction-type using `AGENT_EXTRACTION_TYPES`
  reverse map — only agents that actually produced an extraction are pre-populated,
  so partially-extracted passages get remaining agents filled in. *(BE, NLP)*
- ✅ **RR1b** **[Critical]** Stop destructive auto-purge. Added `purge: bool = False`
  to `run_extraction()`; purge block now gates on `if purge:` instead of
  `if limit is None:`. Callers must opt in explicitly — no accidental wipes. *(BE)*
- ✅ **RR1c** **[High]** Persist per-attempt agent run state
  (running/succeeded/failed/skipped). `ExtractionAttempt` model + migration
  `r4s0t6u8v019`. Three helpers: `_begin_attempt` (inserts `running` row, returns
  id), `_finish_attempt` (updates to terminal state + extractions_produced),
  `_skip_attempt` (inserts `skipped` row). Wired into `extract_single_record`:
  excluded agents → `_skip_attempt`; deduped agents → `_skip_attempt`; each
  submitted agent → `_begin_attempt` before submit; failed result → `_finish_attempt("failed")`; abstained → `_finish_attempt("succeeded", 0)`; succeeded
  → `_finish_attempt("succeeded", N)`. *(BE, SDPA)*
- ✅ **RR1d** **[High]** 5 regression tests in `TestRR1Idempotency`: reverse-map
  completeness, per-agent dedup correctness, buggy-logic documentation, purge
  default, multi-type agent coverage. *(BE)*

### Phase RR2 — Restore the safety net (tests / CI / lint)
- ✅ **RR2a** **[High]** Fix test collection. `pytest` fails at import on archived/
  removed modules (`AnthropicProvider`, `src.ingestion.connector`,
  `src.agents.discovery`, `src.ingestion.pdf_tracker`) + missing `httpx`. Deleted
  3 archived-code test files; lazy-imported `httpx` in `model_config.py`; removed
  `AnthropicProvider` from test_llm_provider; added `pytest.importorskip` for
  optional deps (bs4, httpx); updated stale constant/version assertions. 577
  passed / 2 skipped (importorskip) / 0 failures. *(BE)*
- ✅ **RR2b** **[High]** Added `.github/workflows/ci.yml`: `pytest tests/unit/` +
  `ruff check src/ --exclude _archived`. Python 3.11, pip cache, short tracebacks. *(DevOps)*
- ⏳ **RR2c** **[Medium]** Ruff cleanup on active `src/` (758 errors, mostly in
  archives/tests under the configured E,F,I,N,W,UP set). Exclude `_archived/`. *(BE)*
- 🔒 **RR2d** **[High]** Add the high-value missing tests the reviews list (after
  RR2a): bill-level agent payloads, signal-routing recall vs all-agent on gold
  fixtures, adaptive-token retry, JSON-repair strategies, retag endpoint. *(NLP, BE)*

### Phase RR3 — API safety (small; RR3b worsened by RR0.1)
- ✅ **RR3a** **[High]** Wire `verify_api_key` to the `/v1` router. Added
  `dependencies=[Depends(verify_api_key)]` to `include_router(v1.router, …)`
  in app.py. All `/v1/` routes now require a valid X-API-Key header. *(BE)*
- ✅ **RR3b** **[High]** Split `/v1/verification` into read-only GET (queries
  `verification_run_summaries` table, returns latest per document — no LLM calls)
  + `POST /internal/verification/run` (triggers `run_verification_pass`). *(BE)*
- ✅ **RR3c** **[Medium]** Fixed `confidence_tier IN :tiers` tuple bind: changed to
  `confidence_tier = ANY(:tiers)` with `list(confidence_tiers)` — works correctly
  with PostgreSQL array operators via SQLAlchemy raw text. *(BE)*
- ✅ **RR3d** **[Done 2026-07-06]** Created `docs/auth_posture.md` documenting the
  current three-route-group architecture: `/dashboard/` (unauthenticated, full
  pipeline access), `/internal/` (unauthenticated, review API), `/v1/` (API key
  required, published extractions). Clarifies this is appropriate for localhost
  analyst use but requires auth layer if deployed beyond. Includes security
  considerations and deployment recommendations. *(docs finalized)*

### Phase RR4 — Legal parsing & provenance fidelity (foundational; highest product value)
- ✅ **RR4a** **[High]** Stable section tree + subsection-aware paths. Parser
  `_segment_text` now returns 5-tuples `(section_path, text, start, end,
  included_section_ids)`. `included_section_ids` lists every section marker merged
  into the chunk (e.g. `["Section 3", "Section 4"]`). `parse_and_normalize` stores
  this list in `metadata_["included_section_ids"]`. *(BE, NLP)*
- ✅ **RR4b** **[High]** Raw↔normalized offset map. Fixed the merger bug: end offset
  was previously `chunk_start + len(merged_text)` (wrong after joining multiple
  sections). Now tracks `chunk_end` as the actual raw end of the last merged section,
  so `char_offset_start/end` correctly spans the artifact range. *(BE)*
- ✅ **RR4c** **[High]** Jurisdiction-aware citation normalizer:
  `src/core/citation_normalizer.py`. Per-state patterns for CO, CA, NY, TX, CT,
  IL, UT, and federal. `normalize_citation(ref, jcode)` → bare canonical form;
  `find_matching_section_path(ref, paths)` → best-match `section_path` via substring
  + token-overlap scoring. *(NLP)*
- ✅ **RR4d** **[Medium]** Parse quality scoring. `_compute_parse_quality(text)` →
  0.0–1.0 based on replacement-char ratio (weight 0.60) and legal-marker density
  (weight 0.40). Score stored in `metadata_["parse_quality_score"]`;
  `metadata_["requires_manual_review"] = True` when below threshold (0.30). *(BE)*
- ✅ **RR4e** **[Medium]** Blob table split. New `ContentBlob` model + migration
  `s5t1u7v9w020`: globally unique `sha256_hash` now lives on `content_blobs`;
  `raw_artifacts.sha256_hash` unique constraint dropped and replaced with a per-row
  `content_blob_id` FK. Backfill step in migration populates existing rows. *(SDPA, BE)*
- ✅ **RR4f** **[High]** Tests: 15 citation-normalizer fixtures (CO/CA/NY/TX/CT/IL/UT/
  federal + section-path matching), 5 section-tracking tests (5-tuple format, merged
  IDs, offset accuracy), 6 parse-quality tests, 10 orthogonal-dimension tests.
  604 passed / 13 skipped (parser tests skip if bs4 absent). *(NLP, BE)*

### Phase RR5 — Confidence model split (before tiers drive product; extends Phase 4c)
- ✅ **RR5a** **[High]** Split confidence into three orthogonal dimensions added to
  `ConfidenceBreakdown` (RR5a): `source_grounding_score` (evidence * 0.70 +
  section_ref_quality * 0.30), `tracker_alignment_score` (Orrick alignment; IAPP
  wires in at Phase 4b), `schema_completeness_score` (schema_validity * 0.50 +
  completeness * 0.50). All three are computed on every path including gated/IAPP
  paths. `total_score` is unchanged — these are separate axes. *(NLP, RPR)*
- ✅ **RR5b** **[Medium]** Three dimensions exposed in `ConfidenceBreakdownResponse`
  and in all four `confidence_breakdown` dict literals in extractor.py (initial
  extraction, retried extraction, CV recompute, and bill-level retry paths). *(BE, FE)*

### Phase RR6 — Reliability & observability hardening
- ✅ **RR6a** **[High]** Durable `pipeline_events` table (run/agent state transitions)
  — replace the in-memory monitor that clears between runs. `PipelineEvent` model +
  migration `t6u2v8w0x021` + `_persist_pipeline_event()` helper wired at
  agent_success/agent_error call sites in extractor.py. *(BE, SDPA)*
- ✅ **RR6b** **[High]** Per-model concurrency limits + backpressure; default LM
  Studio concurrency = 1. `max_concurrent_agents_per_model` setting; extractor caps
  `ThreadPoolExecutor` `max_workers` via `min(len(group), settings.max_concurrent_agents_per_model)`. *(BE)*
- ✅ **RR6c** **[Medium]** Enforce Alembic at startup; remove runtime DDL/enum
  patching except explicit dev-repair command. `start.py` compares `alembic current`
  vs `alembic heads`, warns when behind; `src/scripts/dev_repair.py` houses all DDL
  patching behind `--check` flag. *(BE, DevOps)*
- ✅ **RR6d** **[Medium]** Retry taxonomy: LLM-timeout / validation / DB-conflict /
  parse / sync — each with its own tenacity policy. `src/core/retry_policy.py`:
  `ErrorCategory` enum + `with_retry(category)` decorator + `classify_llm_error()`. *(BE)*
- ✅ **RR6e** **[Medium]** Sync robustness: ID-window cursor pagination. `SyncCursor`
  model + migration `u7v3w9x1y022` + `sync_to_supabase.py --incremental` flag reads
  `last_synced_id` per table, fetches only new rows, upserts cursor after each
  successful batch. *(BE)*

### Phase RR7 — Architecture & legal versioning (long-term)
- ✅ **RR7a** **[Medium]** Split `extractor.py`: extracted `PassageCoverage` /
  `DocumentCompleteness` / `compute_completeness_manifest` → `completeness.py`;
  extracted `VerificationResult` / `_recompute_confidence_with_cv` /
  `_run_iapp_alignment_for_dv` / `run_verification_pass` → `verification_runner.py`.
  extractor.py re-exports both via thin delegation wrappers. *(BE)*
- ✅ **RR7b** **[High]** Legal source versioning: `session_year`, `bill_number`,
  `retrieved_at`, `source_hash` columns added to `document_versions` (migration
  `v8w4x0y2z023`). `seed_from_csv` populates `bill_number` + `session_year`;
  `ingest_local_files` stamps `retrieved_at` + `source_hash` on first ingest. *(SDPA, RPR)*
- ✅ **RR7c** **[Medium]** Routing recall sampling. `triage_recall_sample_rate`
  setting (default 0.05); `_select_agents_for_passage()` bypasses routing and returns
  all agents for a random fraction of passages for recall measurement. *(NLP)*
- ✅ **RR7d** **[Medium]** Partial unique index on `extraction_runs.is_serving`
  WHERE `is_serving = TRUE` — DB enforces at-most-one serving run. Migration
  `v8w4x0y2z023` (combined with RR7b). *(BE, SDPA)*
- ✅ **RR7e** **[Low]** Pinned all core runtime deps to `==` exact versions in
  `pyproject.toml` (fastapi, uvicorn, sqlalchemy, alembic, psycopg2-binary, pydantic,
  httpx, structlog, tenacity, etc.). *(DevOps)*
- ✅ **RR7f** **[Low]** Single source of truth for default model: `src/core/config.py`
  sets `local_llm_model = local_extraction_model = extraction_model =
  "google/gemma-4-26b-a4b"` (matches CLAUDE.md). *(BE)*
- ✅ **RR7g** **[High]** Replace `existing_hashes` with attempt-state dedup. New
  `succeeded_attempts: dict[tuple[int,str], set[str]]` preloaded from
  `ExtractionAttempt` rows WHERE `status='succeeded'` — correctly skips agents
  that abstained (0 extractions) on prior runs. New `input_text_hash` column
  (sha256[:24]) on `ExtractionAttempt`; alembic migration `w9x5y1z3a024` adds
  column + partial index `ix_extraction_attempts_succeeded`. *(BE)*
- ✅ **RR7h** **[Medium]** Extract routing into `src/ingestion/routing.py` as pure
  functions (`is_boilerplate`, `route_by_signal`, `select_agent_names`) with zero
  SQLAlchemy/agent-object dependencies. Covered by 38 new unit tests in
  `tests/unit/test_routing.py`. Fixed recall-sampling ordering: definitions-header
  check now runs before the sampling gate (was intermittently non-deterministic).
  `extractor.py` delegates via thin wrappers for backward compat. *(BE, NLP)*

### RR sequencing (recommended)
1. **RR1a + RR1b together** (+ RR1c/RR1d) — blocking; unblocks reliable batches.
2. **RR2a** then **RR2b** — restore the regression net before further change.
3. **RR3a–RR3c** — small, and RR3b is worse post-RR0.1.
4. **RR0.3** — backfill stale verification/confidence data once RR1 is stable.
5. **RR4** — the foundational legal-fidelity lift (highest product value).
6. **RR5** — before tiers drive any product surface.
7. **RR6 / RR7** — hardening + long-term architecture.

---

## Active Tasks

> **Session summary (2026-07-14, QA round 2):** Reviewed the 2026-07-13 extraction
> run (790 rows, 16 laws — full findings in `docs/qa_r2_run_review.md`).
> **QA-2/QA-3/QA-4 verified clean on real output.** The run itself appears to have
> executed **without the QA-1 grounding fix** (its failing spans verify at Tier 4
> when replayed through current code) — operator must confirm the branch was pulled,
> then repair stored rows via `python -m src.scripts.reground_spans` +
> `recompute_confidence`. Two new fixes landed: **QA-6 (preemption over-firing)** —
> 81 signals on the run, ~60% deterministic junk (own-state codes as
> "cross_state_conflict", self-negating descriptions, prompt-example authorities
> parroted verbatim incl. two tier-A rows); credibility guard now drops these at
> extraction time, hides stored rows at sync time, prompt de-poisoned; replay: 49/81
> dropped, every grounded savings clause kept. **QA-7 (preamble-variant definition
> dupes)** — "As used in this subdivision, 'X' means…" copies scored 0.85-0.88, under
> QA-4's 0.9 threshold; preamble now stripped before comparison. Two new failure
> classes documented as open tasks: **QA-8** (CA parallel-version bills multiply
> extractions — SB 926 stores §647 four times → 178 rows) and **QA-9** (non-AI
> boilerplate flooding — 49/51 SB 926 obligations have no AI nexus; PN-matrix
> pollution risk). 1344 unit tests passing; CI green.
>
> **Session summary (2026-07-13):** Five quality-assurance fixes targeting the 2026-07-12
> extraction run output (37 extractions across AZ/AR bills) were fully implemented,
> tested, and pushed. **QA-1 (Tier-4 span verification ordering) — fixed; 32/37 spans
> now verify (was 3/37).** **QA-2 (definition actor hallucination guards) — dropped
> invented actors/NIST cross-contamination.** **QA-3 (responsible_party force-fit) —
> normalized via ratified alias table; repairs both live and stored rows.** **QA-4
> (cross-passage definition deduping) — law-level SequenceMatcher at 0.9 threshold
> eliminates duplicate emissions.** **QA-5 (EA1 gold-set seed) — 2 new fixtures +
> companion labels CSV documenting all 37 verdicts and error vocabulary.**
>
> **EA1-2 (harness rework) also landed this session:** the evaluation harness now
> consumes `ExtractionResult` (fixing the pre-rework `assert isinstance(actual, dict)`
> that would have crashed on every real call), covers all 9 agents (6 clause + 3
> bill-level), does best-match selection for multi-extraction passages, adds a
> whole-bill eval mode with its own fixture subtree, and emits a deterministic
> baseline artifact for the EA1-3 regression gate. Seeded one conservative bill-level
> fixture (AZ SB1359 enforcement). This unblocks EA1-3 baseline capture, which now
> requires the operator's machine (live LLM). 1314 unit tests passing; CI green.

### QA round 2 — open items (from `docs/qa_r2_run_review.md`)

> **Phased plan for QA-8/QA-9 written 2026-07-14:** `docs/qa8_qa9_phased_plan.md`.
> Both issues share one root cause — California re-enacts whole code sections on
> amendment (Cal. Const. art. IV §9), so SB 926 carries Penal Code §647 **eight
> times** (2³ enactment contingencies of AB 1874/AB 1962/SB 1414). QA-8 is the
> horizontal blowup (×8 copies), QA-9 the vertical one (whole restated section
> extracted, one AI-relevant subdivision). Measured while planning: the
> parallel-version detector regex finds exactly 3 affected laws corpus-wide
> (SB 926 ×8, AB 2355 ×2, SB 11 ×2 — zero false positives on 208 other sources),
> and a naive per-extraction AI-keyword filter is **disqualified** (would hide
> 98.4% of TMP-CA-EMPLOYMENTANDS, a genuine ADS law — relevance must be scoped to
> restated sections, never law-wide). Sequencing: Phase 1 (QA-8 collapse,
> deterministic, sandbox-actionable — **landed 2026-07-14**) → Phase 2 (QA-9a
> sync-time subdivision scoping — **engine + sync wiring landed 2026-07-14,
> gated OFF by `settings.qa9a_scope_filter_enabled` pending RPR
> ratification** — + QA-10 junk-definition guard, **landed 2026-07-14**) →
> Phase 2b (QA-9c parse-time scope annotation — **landed 2026-07-14**,
> mechanical/ungated: computes the scope map at ingest where the whole
> document is in hand, closing QA-9a's empty `added_section_numbers` TODO
> and becoming QA-9b's input) → Phase 3 (QA-9b pre-extraction scoping —
> **code landed 2026-07-14, gated OFF by `qa9b_prescope_enabled` pending
> the EA1-3 baseline measurement**) → Phase 4 (stress fixtures — **landed
> 2026-07-14** — + optional markup-preserving re-fetch of CA sources,
> still a product decision).

- [x] **QA-8 — parallel-version collapse (Phase 1 of the plan) — LANDED
  2026-07-14:** `_AMENDING_HEADER_RE` + `_group_parallel_versions()` in
  `src/ingestion/parser.py` detect amending-header groups at parse time
  (`Section N of the X Code[, as amended by ...], is amended to read:`),
  keyed by `(code, section)` so different "as amended by" qualifiers on the
  same target still group together. The last version in bill order is
  marked `parallel_version_representative: true` in `metadata_` (CA
  drafting convention: final restatement = most-merged contingency; every
  version carries the bill's own changes regardless of which is kept, so
  the choice is lossless). `_check_parallel_version()` in
  `src/ingestion/extractor.py` skips non-representatives before agent
  selection (sentinel -2, tracked in the conservation ledger as
  `skipped_parallel_version` and in run summary as
  `parallel_versions_skipped`, mirroring the existing jurisdiction-skip
  pattern). Verified against the real committed sources: SB 926 groups all
  8 §647 copies (indices 0-7, representative=7), AB 2355 groups its 2
  §84504.2 copies, SB 11 groups its 2 §3344 copies; AR HB1877 (different
  header shape entirely) produces zero groups — confirms the "3 affected
  laws, zero false positives" measurement from the plan. 23 new unit tests
  (`tests/unit/test_parallel_version_grouping.py`,
  `tests/unit/test_parallel_version_extraction_skip.py`); full suite green
  (1367 passed); `ruff check --select E9,F` clean. **Retroactive repair
  still needs the operator:** re-extract SB 926 / AB 2355 / SB 11 once a
  live pipeline run is available — sandbox has no DB connection to do this
  here. Acceptance target unchanged: SB 926 ~181 rows → ~25, §647 token
  spend ÷8.
- [~] **QA-9a — restatement-scoped relevance (Phase 2; code sandbox-actionable,
  rules need RPR sign-off) — ENGINE + SYNC WIRING LANDED 2026-07-14, GATED
  OFF PENDING RATIFICATION:** `src/core/restatement_scope.py` implements
  the scope trigger (Phase-1 grouped, or a single-version restatement
  ≥6K chars) and the subdivision in-scope test (AI/domain keyword;
  reference to a section this bill adds, checked at the enclosing
  top-level subdivision so AB 2355's keyword-free formatting paragraphs
  stay in scope via their parent's § 84514 citation; adjacency for shared
  lead-in prose). Validated against the real corpus (29 tests,
  `tests/unit/test_restatement_scope.py`): SB 926 keeps only `(j)(4)` in
  scope out of all 12 top-level subdivisions; AB 2355's formatting rules
  correctly stay visible (the over-filtering trap fact 0.3 caught);
  TMP-CA-EMPLOYMENTANDS never trips the scope trigger at all (0% hide
  structurally guaranteed). **Now wired into `payload_adapter.py`**:
  `adapt_payload_for_sync()` gained `passage_text` / `passage_metadata` /
  `added_section_numbers` parameters (all optional, backward-compatible);
  `_apply_restatement_scope()` sets `ai_nexus: false` → `display: false`
  on out-of-scope clause-level extractions (obligation, threshold,
  definition, rights_protection, compliance_mechanism, preemption_signal —
  bill-level agents skipped, no verified evidence structure yet per
  EA5-1); all six adapters now pass `ai_nexus`/`display` through instead of
  stripping them. `sync_extractions.py`'s three call sites
  (`_build_insert_row`, `sync_updates`, `_FETCH_COLUMNS_SQL`) fetch
  `nsr.metadata_` and pass it through. **Deliberately kept inert**:
  `settings.qa9a_scope_filter_enabled` (`src/core/config.py`) defaults to
  `False` and the function no-ops immediately when unset — RPR/product
  ratification of the in-scope rules (step 4) still hasn't happened and
  can't happen autonomously; this is a relevance judgment over what hides
  from the product surface, not a mechanical guard like QA-6/QA-10. A
  human flips `REGS_QA9A_SCOPE_FILTER_ENABLED=true` post-ratification.
  `tests/unit/test_payload_adapter_qa9a.py` (13 tests): the engine's
  wiring correctness with the flag explicitly enabled via an autouse
  fixture, PLUS a `TestFlagDefaultsOff` class that pins the real shipped
  default (unset → no hide) so an accidental flip is caught by CI. Also
  still needed before a ratified rollout: (a) a real hide-report against
  live DB rows (needs the DB this sandbox doesn't have — run with the flag
  temporarily enabled in a scratch/dry-run environment only); (b)
  `added_section_numbers` — wired as a parameter but every call site
  currently passes an empty set (marked `# TODO`), since populating it
  needs the bill's full text at sync time and today's query only fetches
  the single passage; **resolution: QA-9c below** (parse-time annotation —
  compute the scope map at ingest and let sync read stored metadata).
  Full suite: 1421 passed (up from 1419); `ruff check --select E9,F` clean.
- [x] **QA-9c — parse-time scope annotation (Phase 2b of the plan;
  mechanical/ungated) — LANDED 2026-07-14** (planned and implemented same
  day): the scope *computation* (not consumption) moved to
  `parse_and_normalize`, the only pipeline stage holding the whole
  document — which is exactly the context rule 2(b) needs and sync lacks.
  (1) Engine refactor (`restatement_scope.py`): new
  `annotate_restatement_scope()` classifies the whole subdivision tree in
  one pass (top-level/second-level/lead-in regions + shared preamble),
  plus `scope_for_offset()`, `annotation_is_current()`,
  `assess_with_annotation()`; `assess_extraction_scope` is now a thin
  wrapper over the annotation machinery, so exactly one implementation of
  rules (a)-(c) exists — the pre-refactor 29 tests passing unmodified is
  the parity proof. Keyword iteration made deterministic (sorted) so
  stored annotations are reproducible across processes. (2) Parser:
  `_restatement_scope_meta()` computes the document-level
  `added_section_numbers` once from the joined passage texts and writes
  `metadata_["restatement_scope"]` ({engine_version,
  added_section_numbers, regions[]} with offsets valid against
  `text_content` as stored) on every `is_restatement_passage()` hit — all
  parallel-version members plus ≥6K single-version restatements. Same
  JSONB column QA-8 writes; no migration. (3) Sync
  (`_apply_restatement_scope`, still flag-gated): prefers a current
  stored annotation via `assess_with_annotation`; absent/stale falls
  back to the on-the-fly path, so pre-annotation rows keep working.
  Closes the QA-9a `added_section_numbers=set()` TODO for re-ingested
  docs. (4) Staleness: `SCOPE_ENGINE_VERSION` stamped into every
  annotation; bump it on any rule/vocabulary change — version-mismatched
  annotations are treated as absent, never silently applied. Backfill
  script deferred (re-ingest covers the 3 affected laws, already operator
  work). (5) 39 tests in `tests/unit/test_restatement_annotation.py`
  against the real corpus: all 8 SB 926 §647 passages annotated with only
  (j)(4)-connected regions in-scope; AB 2355 carries
  added_section_numbers=['84514'] with formatting subdivisions in-scope
  via the reference rule; AR HB1877 + TMP-CA-EMPLOYMENTANDS get zero
  annotations (structurally untouched); parity of stored-vs-on-the-fly
  verdicts; sync prefer/fallback/flag-off; and the TODO-closure demo
  (on-the-fly with empty set over-hides the AB 2355 formatting rule, the
  stored annotation keeps it visible). **Not gated on ratification**:
  inert metadata changes no agent input and hides no row (annotation ≠
  activation — QA-9a's flag and QA-9b's baseline gate stay where the
  effects are). Full suite 1460 passing.
- [~] **QA-9b — pre-extraction scoping (Phase 3) — CODE LANDED 2026-07-14,
  GATED OFF pending EA1-3 baseline:** `build_inscope_excerpt()`
  (`restatement_scope.py`) builds the reduced agent input from a QA-9c
  annotation — one-line context header naming the section, in-scope
  regions verbatim in document order, `[...]` elision markers; returns
  None when nothing to trim or nothing in scope (conservative fallback to
  full text). `_prescope_agent_input()` (`extractor.py`) applies it in
  `extract_single_record` behind `settings.qa9b_prescope_enabled`
  (default False): routing still sees the FULL text, span verification
  still runs against the full stored passage (kept chunks are verbatim
  slices, so excerpt quotes still string-verify), offsets-vs-text
  mismatch disables prescoping rather than slicing wrong, and the
  retry/recovery paths deliberately keep full-context inputs. Extractions
  from a prescoped input carry `extraction_meta["prescoped_input"]` +
  `prescoped_chars_dropped` (EA0-4's input-honesty pattern). On the real
  SB 926 representative the excerpt is under half the full restatement.
  **Remaining gate is the measurement, not code**: capture the EA1-3
  baseline on full-passage inputs (live LLM, operator machine), flip the
  flag, rerun the harness with the SB 926/AB 2355/SB 11 stress fixtures,
  require no F1 regression before keeping it on.
- [x] **Phase 4 — EA1 stress fixtures (sandbox-authorable) — LANDED
  2026-07-14:** three gold-standard fixtures added to
  `tests/fixtures/gold_standard/`, picked up automatically by
  `EvaluationHarness.load_test_cases()`: `ca_sb926_sec647_computer_generated_
  image.json` (Penal Code §647(j)(4)(A)(ii) — the one AI-relevant clause in
  SB 926's restated section; expects the prohibition obligation, the
  under-18 threshold exception, and an ambiguity finding on the undefined
  "reasonable person would believe it authentic" standard), `ca_ab2355_
  sec84504_2_disclosure_formatting.json` (Government Code §84504.2(a)(1)-(2)
  — the over-filtering regression guard: a genuine formatting obligation
  with no AI keyword of its own, in-scope only via its lead sentence's
  citation to the bill's added §84514), and `ca_sb11_sec3344_digital_
  replica_definition.json` (Civil Code §3344(f) — the sentence duplicated
  verbatim across SB 11's two parallel §3344 restatements; QA-8 collapse
  keeps exactly one). Every `passage_text` verified byte-for-byte against
  the committed corpus files; every expected payload validated against the
  real `ObligationPayload` / `DefinitionActorPayload` /
  `ThresholdExceptionPayload` schemas; every fixture's scope classification
  cross-checked against `restatement_scope.assess_extraction_scope` directly
  (all match). One correction to the original plan text folded into
  `docs/qa8_qa9_phased_plan.md`: "agents abstain on loitering/prostitution
  subdivisions" isn't how the architecture works — clause agents extract
  real obligations regardless of AI-topicality; QA-9a's in/out-of-scope
  classification is a sync-time display decision, not an extraction-time
  abstention, and that classification is what's already regression-locked
  in `tests/unit/test_restatement_scope.py`. Full suite: 1411 passed, 9
  skipped; the 7 failed + 6 errors are all pre-existing DB/API integration
  tests (no live Postgres or auth backend in this sandbox) — confirmed
  unrelated to this change via git-stash bisection (identical failures with
  the new fixtures stashed out); `ruff check --select E9,F` clean.
- [x] **QA-10 — junk-definition micro-guard (rides with Phase 2) — LANDED
  2026-07-14:** `_is_bare_citation_term` / `_is_conditional_enactment_
  boilerplate` in `src/agents/definition_actor.py`'s `_postprocess_extraction`
  drop definitions whose term is a bare code-section citation ("Section 647
  of the Penal Code") or whose text is conditional-enactment boilerplate
  ("...incorporates amendments to Section 647 of the Penal Code proposed by
  this bill, Assembly Bill 1962..."), matching SB 926 ids 234/235 exactly.
  Mechanical (no ratification needed), same pattern as QA-2/QA-6. 12 new
  tests (`tests/unit/test_definition_boilerplate_guard.py`).
- [ ] **Operator — verify QA-1 was active + repair stored rows (Phase 0):** confirm
  the branch was merged/pulled before the next run; then
  `python -m src.scripts.reground_spans --dry-run` → apply →
  `python -m src.scripts.recompute_confidence`. The 53 stale 2026-07-12 rows
  (AZ SB 1359, AR HB1877, TMP-AZ) predate all QA fixes — re-extract or exclude.

---

## Run Output Visibility Plan (ROV) — Timestamping & Run Comparison Summary (2026-07-19)

> **Goal:** Enable analysts to compare extraction runs without manual cross-referencing of
> multiple output files. Every run export (CSV, JSONL, JSON) now carries a distinct
> date/time stamp and each run summary includes failures, per-agent performance metrics,
> total time, throughput, and other key signals needed to detect regressions.
>
> **Session summary (2026-07-19):** Run output timestamp and comparison summary feature
> fully implemented, tested, and pushed to branch `claude/legal-extraction-architecture-1exlem`.
> All 1473 unit tests passing; CI green.

### ROV-1 — Run header line (distinct timestamp on every export file) ✅ LANDED
- **Implementation:** `_run_header_line()` in `src/core/run_archiver.py` generates
  `# RUN: <date> <time> UTC | type=<run_type> | run_summary.json at <start> (started <date>)`
  — emitted as the first line of every CSV/JSONL export file so analysts can identify
  which run a sample came from at a glance.
- **Applied to:** `extractions.csv`, `by_agent/<agent>.csv`, `bill_level_extractions.csv`,
  `low_confidence_extractions.csv`, `low_confidence_extractions.jsonl`
- **Tests:** 4 tests in `test_run_archiver_run_comparison.py::TestRunHeaderLine` verify
  timestamp format, distinctness, and applicability across run types.

### ROV-2 — Run comparison summary block ✅ LANDED
- **Implementation:** `_build_run_comparison_summary()` in `src/core/run_archiver.py`
  computes and returns a consolidated block with:
  - `run_timestamp` (ISO 8601 + Z)
  - `run_type` (extract/retry/recover)
  - `total_duration_seconds`
  - `total_extractions`
  - `extractions_per_minute` (throughput metric)
  - `failures` object: `total_agent_errors`, `per_agent_errors` dict, `circuit_breaker_tripped`, optional `circuit_breaker_detail`
  - `avg_duration_ms_per_agent` (per-agent performance)
  - `avg_duration_ms_overall` (weighted average, precise via stored `total_duration_ms` in monitor)
  - `token_usage_total`
  - `conservation_ok` (boolean)
  - `confidence_tier_distribution` (A/B/C/D counts)
  
- **Wiring:** `finalize()` calls `_build_run_comparison_summary()` once and passes it to:
  - `run_summary.json` as `run_comparison_summary` block (peer to `started_at`)
  - `agent_stats.json` as `run_summary` block (same object for consistency)
  - Each CSV header line via `_run_header_line()`

- **No-divide-by-zero guards:** zero-duration and zero-extraction runs compute
  cleanly to 0.0 without raising exceptions.

### ROV-3 — Monitor enhancement (precise duration tracking) ✅ LANDED
- **Implementation:** `src/core/extraction_monitor.py` `AgentStats` class now carries
  `total_duration_ms` (cumulative, precise) in addition to `avg_duration_ms` (computed
  per-call). The snapshot dict includes both so consumers computing a weighted overall
  average can avoid rounding loss: `overall_avg = sum(agent.total_duration_ms) / sum(agent.calls)`.

### ROV-4 — Integration & validation ✅ LANDED
- **Test coverage:** 13 tests in `tests/unit/test_run_archiver_run_comparison.py`
  - 4 header-line tests (format, distinctness, multi-run tracking)
  - 5 comparison-summary tests (failures, per-agent errors, duration averaging, zero-division guards, circuit-breaker detail)
  - 4 end-to-end finalize tests (JSON consistency across run_summary/agent_stats, CSV headers on different output types)
  
- **Real integration:** populated via the real `ExtractionMonitor` singleton
  (not a mock), proving the actual run-to-summary flow works end-to-end.

- **Backward-compat:** existing output files unchanged except for the new header line
  prepended; `run_summary.json` gains a new top-level key (`run_comparison_summary`),
  non-breaking for consumers that ignore unknown keys; `agent_stats.json` mirrors the
  block as `run_summary` (new key, backward-compatible).

### ROV sequencing & next steps
1. **Live validation (operator machine):** Next extraction run will emit all new outputs
   with timestamps and comparison blocks. Operator should confirm:
   - Header lines are human-readable and appear on first line of all CSV/JSONL exports
   - `run_summary.json` and `agent_stats.json` contain the comparison block
   - Two runs at different times produce different timestamp values
   - Timestamp precision is sufficient for correlation (ISO 8601 second resolution)

2. **Dashboard integration (optional, Phase 2):** A future panel could render the
   comparison block to surface throughput, per-agent error rates, and run health in
   the UI — but the data is now queryable via the JSON exports for any consumer.

---

## NVIDIA Throughput & Provider Plan (NIM) — from NIM briefing review (2026-07-19)

> Source: operator-supplied briefing on NVIDIA `build.nvidia.com` free-tier limits,
> model options, monitoring, and alternative providers. Every load-bearing claim was
> verified against this branch before planning (same discipline as the RC/SFH plans).
> Status legend: ✅ done · 🔧 in progress · ⏳ ready · 🔒 gated.
>
> **Verification corrections to the briefing (read before acting on it):**
> (1) The "3-attempt / 4-second retry" claim is **stale** — `llm_provider.py` already
> does 6 attempts with 1/2/4/8/16s exponential backoff (2026-06-15 hardening, merged).
> Remaining real gaps: no jitter, no `Retry-After` honoring, ~31s cumulative cap.
> (2) "Spread agents across distinct models" is **largely already done** — three
> distinct NVIDIA models are assigned (8B: triage/definition_actor/preemption;
> `gpt-oss-120b`: 4 clause + 3 bill-level agents; 70B: CV/gap per SFH-1m). The real
> issue is **skew** (7 heavy agents on one model's budget), not absence of spreading.
> (3) The mislabel is **confirmed**: `llm_provider.py:609` logs `nvidia_quota_exhausted`
> for any retry-exhausted 429 without ever inspecting the body; `extractor.py:190`
> maps every 429 to `quota_error`. (4) **No proactive pacing exists** — only reactive
> backoff; `max_concurrent_agents_per_model` (RR6b) caps *concurrency* (VRAM), not
> *rate*. (5) The ~40 RPM figure and the §4 model catalog are blog-sourced, not NVIDIA
> docs — treat as configurable hypotheses to measure, never hardcoded constants.
> (6) **Tension with EA4-4**: EA4-4 consolidates definition_actor + preemption ONTO
> the strong model for accuracy; the briefing spreads across models for throughput.
> Synthesis: move them to a strong model that is *not* `gpt-oss-120b` (accuracy lift
> AND a separate per-model rate budget) — still EA1-gated like any model change.
>
> **Live-run evidence (2026-07-19 monitor snapshot, run in progress, 81/806
> passages):** the pipeline is **not currently rate-limited** — 1,294 agent calls
> over ~9h elapsed ≈ 2.4 calls/min aggregate against a reported ~40 RPM/model cap
> (~94% of the rate budget unused; `gpt-oss-120b` lane ~1.8 RPM, 8B lane ~0.6 RPM).
> Zero 429s observed; failure rate 0.2%. The binding constraint is **per-call
> latency × serialization** (obligation avg 195.7s/call, 462 calls ≈ 25h cumulative),
> projecting **~3.7 days** for the full 806-passage run. This reframes NIM-1: pacing
> is the *guardrail that lets concurrency be raised into the unused budget*, not a
> defense against current throttling.

### Phase NIM-0 — Measure and label honestly (sandbox-actionable; land first) ✅ LANDED 2026-07-19
- ✅ **NIM-0a** — new `src/core/llm_rate_telemetry.py`: thread-safe per-model
  `LLMRateTelemetry` singleton tracking `requests_total`, rolling trailing-60s
  RPM (`rpm_current` + all-time `rpm_peak`), `tokens_total`, and
  `rate_limited_seen` / `rate_limited_recovered` / `rate_limited_exhausted`
  counters. Written from the actual chokepoint — `NvidiaLLMProvider.call()`'s
  retry loop — so retries that are transparently absorbed (never surfacing as
  an exception to extractor.py) are still counted, not just terminal
  failures. Reset alongside `ExtractionMonitor.start_run()` (own singleton,
  own lock — read outside `ExtractionMonitor`'s lock to keep them
  independent). Surfaced in two places per the plan: (1) `ExtractionMonitor.
  HealthSnapshot.llm_rate_telemetry` for the live 2s-poll dashboard: (2) a new
  `llm_throttle_telemetry` key in `RunArchiver._build_run_comparison_summary()`
  or the persisted run comparison. No behavior change — pure observability;
  `LocalLLMProvider` left unwired (rate limits are NVIDIA-specific, no 429
  path exists locally). 14 tests in `test_llm_rate_telemetry.py` (rolling-
  window aging, peak tracking, reset, singleton, plus 3 tests against the
  real `NvidiaLLMProvider.call()` proving success/429-recovery/429-exhaustion
  all write through) + 1 wiring test in
  `test_run_archiver_run_comparison.py`. *(BE)*
- ✅ **NIM-0b** — new `_classify_429_body()` in `llm_provider.py`: keyword-reads
  a 429 body into `rate_limited_transient` / `allowance_exhausted` /
  `429_unclassified`. Deliberately does **not** treat a bare "quota" mention as
  decisive (RPM throttling is commonly phrased "queries per minute quota" too)
  — only specific phrases (`per minute`, `too many requests`, `credit`,
  `trial has ended`, etc.) tip the classification; ambiguous bodies honestly
  land unclassified rather than guessing. Replaced the `nvidia_quota_exhausted`
  log label (which asserted an exhausted balance never verified) with
  `nvidia_429_exhausted` carrying the classification + a body excerpt.
  `_classify_llm_error`'s existing `"quota_error"` bucket is **unchanged**
  (dashboard color-coding intact) — the finer-grained read is carried
  separately via a `nvidia_429_classification` attribute on the raised
  `httpx.HTTPStatusError`, read by new `_classify_429_detail()` in
  `extractor.py` and threaded into the `agent_error` pipeline-event details as
  `throttle_classification` (additive key, only present for NVIDIA 429s). 10
  tests for `_classify_429_body`, 5 for `_classify_429_detail`
  (`test_error_classification.py`), 4 for the `_RateLimited` exception's new
  fields, plus 2 end-to-end 429-classification tests against the real
  provider. *(BE)*
- ✅ **NIM-0c** — retry loop now reads `settings.nvidia_max_retries` (was
  hardcoded `_max_retries = 5`); new `_compute_backoff_seconds()` adds jitter
  (`settings.nvidia_retry_jitter_fraction`, default 0.25) and a hard ceiling
  (`settings.nvidia_retry_backoff_cap_seconds`, default 30.0) to the
  exponential curve, applied to both the transport-error and rate-limited
  retry branches. New `_parse_retry_after_seconds()` honors a numeric
  `Retry-After` header when NVIDIA sends one (takes precedence over the
  exponential guess, still capped); unparseable/absent header falls back to
  jittered exponential, since NVIDIA doesn't always send it. 5 new settings
  in `config.py`. 11 new tests covering exponential growth, cap, Retry-After
  precedence, jitter bounds, non-negativity, plus a retry-then-succeed
  end-to-end test proving the loop still recovers within budget. *(BE)*
- ✅ **NIM-0d** *(found via the live snapshot; rides with ROV)* — new
  `ExtractionMonitor._emit_deduped()`: collapses repeated `low_confidence`/
  `truncation` feed events for the same `(category, agent, record_id)` to one
  line per run instead of one per extraction item (root cause: `extractor.py`
  calls `record_agent_result` once per extraction item on the success path,
  so a 30-extraction passage previously wrote 30 near-identical feed lines).
  Repeats past the first are still counted in the severity total (`warnings`)
  and in a new `duplicate_warnings_suppressed` counter — the information
  isn't lost, just not flooding the bounded event ring buffer. Errors are
  untouched (never deduped) since the live-run problem was specifically
  hundreds of duplicate warnings burying 2 real errors. 7 new tests in
  `test_extraction_monitor.py`. *(BE)*
- All landed as pure code/config, no live LLM required (same discipline as
  EA0/EA2/SFH-1): 53 new tests, full suite 1526/1526 passing;
  `ruff check --select E9,F` clean on every touched file.

### Phase NIM-1 — Client-side pacing (sandbox code; operator-verified) ✅ LANDED 2026-07-19
- ✅ **NIM-1a** — new `src/core/llm_rate_limiter.py`: `RateLimiter`, a
  process-wide, per-model sliding-window limiter. `acquire(model, cap_rpm,
  sleep_fn)` blocks (if needed) until one more request for that model fits
  under `cap_rpm` in the trailing 60s window, then **atomically reserves
  the slot** before returning — the reservation happens inside the same
  lock acquisition as the capacity check, so concurrent callers can't all
  pass a check before any of them records a request (the standard
  check-then-act race a naive limiter would have). Config-driven via new
  `settings.nvidia_rpm_limit` (default `35.0`; `<= 0` disables pacing
  entirely, e.g. for a controlled benchmark). Wired into
  `NvidiaLLMProvider.call()`'s retry loop, called before **every** attempt
  (including retries, since each is a real HTTP request against the
  account's budget) — passes the existing `_sleep_cancellable` closure as
  `sleep_fn` so a pacing wait is interrupted within ~0.5s of a cancelled
  run, the same guarantee backoff waits already had. Reset alongside
  `ExtractionMonitor.start_run()` (own singleton/lock, kept independent of
  `LLMRateTelemetry`'s). Deliberately a **separate** deque from NIM-0a's
  telemetry — not merged — so NIM-0a's shipped "no behavior change,
  observability only" contract stays true; enforcement is opt-in-only via
  this new module. 8 unit tests on the limiter directly (reservation
  semantics, per-model independence, reset, disable-at-zero, a 20-thread
  concurrency test using a short *real* window + real `time.sleep` rather
  than a mocked clock — a no-op sleep with unmocked `time.time()` would
  have spun for up to 60s waiting for the window to age out) + 4 tests
  against the real `NvidiaLLMProvider.call()` (consults the limiter with
  the configured model/cap, disabled-at-zero never sleeps, cancellation
  during a pacing wait propagates `OperationCancelled`). *(BE)*
- ✅ **NIM-1b** — `pacing_wait_seconds_total` added to
  `llm_rate_telemetry.py`'s per-model stats + `record_pacing_wait()`;
  `NvidiaLLMProvider.call()` feeds the limiter's returned wait time into it
  whenever `> 0`. Surfaced in both the live dashboard table (NIM-visibility
  item below) and the persisted `llm_throttle_telemetry` block — pacing's
  throughput cost is now a measured number per model, not a guess. 3 new
  tests (accumulation, zero/negative no-op, per-model independence). *(BE)*
- ✅ **NIM-visibility** *(found while landing NIM-1; not in the original
  plan text)* — `get_extraction_monitor()`'s dashboard fragment
  (`src/api/routes/dashboard.py`, the "Live Extraction Monitor" HTML the
  2026-07-19 screenshot came from) rendered `HealthSnapshot.to_dict()` by
  hand-picking specific keys — meaning NIM-0a's `llm_rate_telemetry` and
  NIM-0d's `duplicate_warnings_suppressed` existed in the snapshot dict but
  were **never rendered anywhere an operator could see them**; all this
  session's new telemetry would have been invisible on the very next live
  run. Added: a new "LLM Rate Telemetry (per model)" table (requests, RPM
  current/peak against the configured cap — color-graded green/amber/red
  by proximity to the cap, or "N (pacing off)" when `nvidia_rpm_limit<=0`,
  tokens, 429 seen/exhausted, cumulative pacing wait) placed between Agent
  Performance and Issues; a "N duplicate warnings collapsed" info badge in
  the Issues row so a low Warnings count doesn't misread as "few problems"
  when many were actually deduped. `get_extraction_monitor()` has no
  FastAPI dependencies, so tests call it directly (no test client needed) —
  8 new tests in `test_dashboard_extraction_monitor.py` (this route had
  **zero** test coverage before this session, a gap this closes alongside
  the feature). Manually rendered the fragment against live-run-shaped data
  to confirm well-formed HTML (screenshot-equivalent verification — no
  browser in this sandbox per CLAUDE.md). *(BE, FE)*
- Note: pacing/concurrency changes alter timing, not model/prompt/inputs — they do
  **not** invalidate the EA1-3 quality baseline (step-back amendment #1 gates
  *behavior* changes, and these aren't).
- All landed as pure code/config, no live LLM required: 23 new tests, full
  suite 1549/1549 passing; `ruff check --select E9,F` clean on every
  touched file.

### Phase NIM-2 — Load-shape decision 🔒 (gated on NIM-0 telemetry from a real run)
- 🔒 **NIM-2a** — operator runs an extraction batch with NIM-0/1 in place; telemetry
  answers which lane saturates as concurrency rises, and whether any 429s classify
  as allowance rather than throttle.
- 🔒 **NIM-2b** — rebalance: pure pacing/concurrency knob changes land freely; any
  **model reassignment** stays EA1-3-gated (EA4-4's standing rule). Preferred shape
  to evaluate when the gate opens: definition_actor + preemption → a strong model
  that is not `gpt-oss-120b` (the EA4-4 synthesis above).
- 🔒 **NIM-2c** — briefing catalog names (`nemotron-3-super-120b`, `qwen3.5-122b`,
  etc.) verified against `build.nvidia.com/models` at decision time and benchmarked
  through the existing EA1 harness on the gold set — no model string wired in from
  a blog post.
- 🔒 **NIM-2d** — if allowance (not throttle) turns out to be the wall, pacing can't
  fix it → triggers NIM-3 immediately.

### Phase NIM-3 — Provider portability 🔒 (gated on NIM-2 outcome + operator sign-off)
- 🔒 **NIM-3a** — portability inventory (doc only): the provider layer is already
  OpenAI-compatible, but document the NVIDIA-specific assumptions in
  `NvidiaLLMProvider` (base-URL-includes-`/v1` quirk, `reasoning_effort` coercion,
  `_REASONING_MODEL_TAGS` idle-timeout heuristic, `reasoning_content` stripping,
  SSE stream shape) — that list is the real migration checklist.
- 🔒 **NIM-3b** — decision memo: stay free tier vs. per-token provider. Candidates
  per briefing: OpenRouter (routing/failover), Together (batch), Fireworks (guided
  decoding — directly serves **EA6-2**'s structured-outputs goal), Deep Infra
  (cheap triage). **Hard requirement, not tiebreaker:** contractual training-use
  exclusion + SOC 2/ISO 27001 + zero-retention terms — this is a compliance product.
  Adoption requires EA1 gold-set benchmark on the candidate + operator sign-off.
- 🔒 **NIM-3c** — cost accounting: NIM-0a per-model token totals make the EA plan's
  required "$/law in run_summary.json" computable the moment a price sheet exists.
  Live-run input: ~209k tokens/passage observed → **~170M tokens projected** for a
  full 806-passage run.

### Phase NIM-4 — Self-host / hybrid (flag only; no action)
- The briefing's "hybrid" already half-exists (LM Studio local fallback via
  `provider: "local"`). Self-hosted NIM containers (free ≤16 GPUs for
  developer-program members) recorded as a deferred option contingent on operator
  GPU capacity. GPU clouds (CoreWeave/Bitdeer/Lightning) ignored per the briefing's
  own advice.

### Explicitly not adopted from the briefing
- No "credits remaining" display (credits are retired; no balance exists to show).
- No immediate model swaps to catalog picks — EA1-gated.
- No RAG/vector-store work (the Vultr angle) — no RAG layer exists or is planned.
- "Eigen AI" — briefing itself couldn't identify the vendor; dropped.

**Sequencing:** NIM-0 → NIM-1 (both sandbox-actionable now); NIM-2 needs an operator
run; NIM-3 gates on NIM-2's outcome; NIM-4 is a standing note. NIM-0/1 can proceed in
parallel with EA1-1 fixture work without touching the EA1-3 baseline's validity.

---

### ⚠️ IMMEDIATE NEXT STEPS (updated 2026-07-19, after the EAR re-review)

**Status:** QA-1–QA-5 **and EA1-2** complete, tested, pushed to branch
`claude/legal-extraction-architecture-1exlem`. CI green (1314 unit tests). The
evaluation harness now consumes `ExtractionResult`, covers all 9 agents
(6 clause + 3 bill-level), and emits a deterministic baseline artifact.
**2026-07-19:** second-pass architecture re-review landed as the EAR plan
(above, after the EA plan) — it confirms EA's state, corrects four stale
premises (P-1–P-4), and adds a sandbox-actionable Phase EAR-0 batch
(CV lower-only clamp, gap-evidence verification, citation self-consistency,
audit-trail stamps, verifier-lineage flag, `compliance_date` bug,
loose-match telemetry) plus EAR-2-1 (material-field span binding) as the new
highest-leverage deterministic item.

**Remaining blockage:** EA1-3 (baseline capture) **requires a live LLM** —
this sandbox has no `NVIDIA_API_KEY` and no reachable LM Studio, and the
harness calls real providers. This is now the long pole and needs the
operator's machine. Two reviews in a row (EA 2026-07-03, EAR 2026-07-19)
have reached the same conclusion; treat the eval substrate as the settled
binding constraint.

**Sequencing (1→2, operator-gated):**

1. **EA1-3 (NEXT — operator machine) — Baseline capture on current prompts/models**
   - Run `EvaluationHarness().run_all()` against the gold_standard tree
     (35 clause fixtures + the seeded `bill_level/az_sb1359_enforcement.json`)
     with NVIDIA (or local LM Studio) configured
   - Persist via `harness.write_baseline(result, "evaluation/baselines/<date>.json")`
     — the method emits sorted, deterministic per-agent per-field P/R/F1
   - Commit the baseline artifact; every future prompt/model/weight PR reruns
     and diffs against it
   - Owner: operator (`python start.py` env + `NVIDIA_API_KEY`)
   - Acceptance: baseline artifact committed; EA3-1 + TA-8 become gatable
   - Note: bill-level ground truth is currently one law / one agent
     (enforcement). Expand applicability + compliance_timeline coverage during
     the EA1-1 annotation pass; the harness scores only agents that have
     ground truth, so the baseline grows monotonically as fixtures are added.

2. **TA-8 (unblocked after 1) — Threshold/keyword-list retuning**
   - Uses EA1-3's baseline as the regression gate
   - Tune the LLM 0.4 not-relevant cutoff, keyword confidence curve,
     `_ADJACENT_AI_KEYWORDS` promotion; measure delta against baseline
   - Owner: NLP (iterate with operator rerunning the baseline diff)
   - Acceptance: tuned settings committed; delta report showing measured F1 impact

**Parallel, still sandbox-actionable:** (a) **Phase EAR-0 (all eight items)**
— pure code/telemetry fixes, no prompts or weights touched, unit-testable
with mocked providers; EAR-0-1 (CV lower-only clamp) first, it closes an
active tier-inflation path into the P3 publish gate. (b) **EAR-2-1**
(material-field span binding, informational metadata) — the hard
prerequisite for the EAR-3/EA3-1 confidence rebalance; needs soak time on
real runs, so land early. (c) EA1-1 fixture expansion toward the 8-law
stratified set (SFH-2c) — more clause fixtures and bill-level ground truth
can be authored here from the committed `output/law_texts/` sources without
a live LLM (annotation, not extraction); EAR-1-2 re-prioritizes: positive
preemption fixture first, then bill-level applicability + timeline.

### ⚠️ MERGE REQUIRED BEFORE NEXT RUN
- **Merge `claude/brave-lamport-d9zgjx` → main** — contains 3 NameError crash fixes in `extract_single_record` (introduced by RR7g dedup refactor, would crash every passage on the next extraction run), the full **2026-06-15 NVIDIA-backend hardening** (429 + transport retry, reasoning_effort coercion, bare-array handling, evidence-span loosening, Re-triage Failed, archiver fix), plus lint cleanup, CI gate fix, repo cleanup. CI green (Unit tests + Ruff lint). **Do this before hitting Extract All.**

### Operator actions (need live machine + DB)
- **Run `alembic upgrade head`** — migration `l8i4j0k2g713` adds `duration_ms`, `input_tokens`, `output_tokens` to `extractions` table.
- **Selective triage reset** — Re-triage any passages that failed with `finish_reason=length`. Triage page → Reset Failed → re-run triage.
- **Run Extract All (Step 3)** — unblocks everything: bill_level_extractions, Phase 1.H, concept grouping, confidence recompute, taxonomy Phases 1–2. Provider: NVIDIA (`openai/gpt-oss-120b` + `meta/llama-3.1-8b-instruct`), 6 passage agents + 3 bill-level agents. 16 quarantined laws will be skipped (see `output/law_texts_quarantine/NEEDED_SOURCES.md`).
- **After extraction:** Run `SELECT agent_name, COUNT(*) FROM bill_level_extractions GROUP BY agent_name` (1a check), then Verify step, then `python -m src.scripts.group_concepts`, then sync (Steps 5→6).
- **Obtain correct source text for 16 quarantined laws** — Place correct bill text in `output/law_texts/<canonical_law_id>.txt`.

### Pending decisions
- **TN quarantine files contain TX bill content** — TX SB 1188, SB 2373, SB 815, SB 20, SB 1621 may be legitimate TX AI laws. Decide whether to add as new TX entries in `fact_laws.csv`.
- **`business` actor code (122 mentions)** — PENDING_LKA ruling to ungate Phase 3d→3f.
- **Per-agent CSV outputs (Phase B, see below)** — Decide whether per-agent export lands as a query param on existing routes (`?agent=obligation`) or as a dedicated bundler script (`export_by_agent.py`). Both are compatible; query-param is lower-effort and immediately usable in the dashboard.
- **Selective sync by agent (Phase C, see below)** — Decide: when `--agents` filter is active, should the cursor be per-agent (full isolation, slightly more state) or shared (simpler, but risks skipping deferred agents on future runs)? Recommended: per-agent cursor keyed `table+agent_name`.

---

## Integration Durability — Downstream Consumer (Policy Navigator) Requests (2026-06-22)

> **Background:** The Policy Navigator team reported that RC `document_families.id` was
> wholesale-reassigned after a refresh, corrupting their bridge mapping (8/8 sampled families
> now resolve to different laws). They want a stable `canonical_key` surfaced in
> `get_extractions_page`. Four additional data-quality items were flagged.
> **See handoff response drafted below for the full answer to send them.**

### DI-1 — Promote `canonical_law_id` to a first-class stable `canonical_key` column *(blocking durability — do first)*
✅ **Local code complete** (migration `a3b9c5d7e028` + ORM + `local_ingest.py`). Remaining operator steps:
- `alembic upgrade head` on operator machine (migration `a3b9c5d7e028`).
- Supabase migration (on `wjxlimjpaijdogyrqtxc`): alter `document_families` + update `get_extractions_page` RPC to return `canonical_key`, `jurisdiction_code`, `bill_number` alongside `family_id`. After deploy: `NOTIFY pgrst, 'reload schema';`.
- Add `canonical_key` to the `sync_extractions.py` payload so it flows to `synced_extractions` and the consumer can join on it.

**Acceptance:** Consumer can join `law_document_bridge` on `canonical_key` instead of `family_id`; bridge survives a DB wipe as long as `canonical_law_id` values in `fact_laws.csv` don't change.

### DI-2 — Fix family 114: SC law pointing at TX source URL
- Correct the `fact_laws.csv` row for South Carolina Real Estate AI Responsibility Law — replace the TX `capitol.texas.gov` URL with the correct SC legislature URL.
- 372 extractions against this family are currently un-mappable by the consumer; correct URL lets the bridge rebuild succeed.
- Longer-term: add a **seed-time** URL-vs-jurisdiction guard to `local_ingest.py` (domain of `primary_source_url` must match `jurisdiction_code`; warn/block on mismatch). `src/core/jurisdiction_check.py` already does text-level checks at extraction time; extend to URL at seed time.

### DI-3 — Strip jina.ai / Orrick URL wrappers
✅ **Done** — `_normalize_source_url()` added to `local_ingest.py`; strips `https://r.jina.ai/` prefix at seed time. Orrick PDF mirrors have no programmatic fix — flagged for manual curation. Takes effect on next `seed-local` run.

### DI-4 — Retire `ambiguity` extraction type in downstream docs
- The `ExtractionType.ambiguity` enum value still exists for legacy row compat but the ambiguity agent is archived (`src/ingestion/_archived/ambiguity_agent.py`). No new ambiguity rows are produced.
- Findings now live as `interpretation_risks` embedded on `ObligationPayload` and `RightsProtectionPayload` (`src/schemas/extraction.py:238-241, 504-509`).
- **Action for them:** migrate their snapshot handler from a top-level `ambiguity` type to reading `payload.interpretation_risks` on obligation/rights rows.
- **Action for us:** document this in the RPC / payload adapter so it's visible to any future consumer.

### DI-5 — Enable RLS on 11 exposed Supabase tables
- Tables currently readable/writable by anon key: `extraction_runs`, `vocab_review_queue`, `verification_run_summaries`, `extraction_verification_status`, `compliance_concepts`, `concept_extraction_links`, `concept_tracker_links`, `extraction_attempts`, `content_blobs`, `pipeline_events`, `sync_cursors`.
- Enable RLS + add read-only policies for the service role; block anon writes. Must add policies in the same migration as enabling RLS (enabling without policies blocks all access including service-role reads).
- Low urgency if the RC Supabase is internal-only, but worth doing before any broader access.
- **Eval set (ANALYSIS-1)** — 100-row lawyer-verified sample. Name a responsible person; gates Phase 4 + the trust bar.
- **Sync to Policy Navigator** — all extraction types or approved-only?

---

## Per-Agent Output, Selective Sync & Re-Run Refactor (2026-06-22)

> **Goal:** Make `agent_name` a first-class dimension so each agent's findings can be
> exported separately, synced selectively, re-run in isolation, and measured for accuracy.
> **Prerequisite:** DI-1 (canonical_key) is independent; this refactor is independent of DI
> but shares the same branch.

### Phase A — First-class `agent_name` column *(keystone — do first)*
✅ **Done** — migration `a3b9c5d7e028` adds `agent_name VARCHAR(100)` to `extractions`; backfills from type→agent map + ExtractionAttempt cross-check; all three Extraction creation sites in `extractor.py` now write `agent_name`. Operator: run `alembic upgrade head`.

### Phase B — Per-agent CSV/JSONL outputs
✅ **Done** — `?agent=<name>` (comma-separated) added to all three dashboard export endpoints (`/api/admitted/export.csv`, `/api/admitted/export.jsonl`, `/api/low-confidence/export.csv`). Exports now include `agent_name` and `canonical_key` columns. `src/scripts/export_by_agent.py` writes one CSV per agent + `all_agents.csv` to `output/exports/<date>/`. Supports `--agents`, `--include-needs-review`, `--dry-run`. (Fixed a broken `get_session` import in this script that didn't exist in `db/engine.py` — now uses `create_engine(settings.database_url)` + `Session`, matching the other operator scripts.)
- **Also done** — the pipeline itself now auto-emits per-agent CSVs every run, not just on-demand: `RunArchiver._export_by_agent()` writes `output/extraction_runs/active/by_agent/<agent>.csv` (one file per producing agent, full DB state, same as `extractions.csv`) at the end of every `finalize()` call. `_write_run_snapshot()` switched from a flat-file copy loop to `shutil.copytree(dirs_exist_ok=True)` so the `by_agent/` subfolder is preserved in per-run snapshots (`output/extraction_runs/run_<id>/`) too.

### Phase C — Selective sync by agent
- Add `--agents <name,...>` / `--exclude-agents <name,...>` flags to `sync_to_supabase.py` (Leg 1) and `sync_extractions.py` (Leg 2). Default = all agents (no behavior change).
- When an agent filter is active, use a **per-agent cursor** keyed on `(table, agent_name)` in `sync_cursors` — prevents a global cursor from silently skipping agents that were deferred to a later sync run.

### Phase D — Re-run individual agents
✅ **Done** — `src/scripts/rerun_agent.py --agent <name> [--law <canonical_key>] [--repurge] [--dry-run] [--limit N] [--force]`.
- Without `--repurge`: idempotent (attempt-state dedup skips already-succeeded passages) — reuses `extract_single_record(db, passage, agents={<name>: instance})` verbatim for full parity with the main pipeline's routing/scoring.
- With `--repurge`: `scoped_purge_agent()` deletes only `agent_name = <name>` extraction rows (+ FK dependents: ReviewAction/ReviewQueueItem/ObligationDependency/ApplicabilityCondition) and clears that agent's `ExtractionAttempt`/`FailedExtractionAttempt` dedup state — scoped, not a global wipe. `--dry-run` previews delete counts first.
- Rejects bill-level agent names (those already upsert in place via the normal Extract step) and refuses to run if an `extraction_runs` row is `status='running'` unless `--force` is passed (avoids racing a concurrent full run).
- Bill-level agents (enforcement/applicability/compliance_timeline) don't need this — they already upsert on `(document_version_id, agent_name)`.
- 6 tests in `test_rerun_agent.py` covering agent classification and CLI validation paths.

### Phase E — Per-agent accuracy metrics *(the tuning payoff)*
- `src/scripts/metrics_by_agent.py`: for each agent, compute and print:
  - Grounding rate (spans with `verified=True` / total spans)
  - Admission rate (`admitted` / total extractions)
  - Abstention rate (abstained passages / passages attempted)
  - Tier A/B/C/D distribution
  - Average confidence score
- Small dashboard panel or CLI report: "preemption grounds at 40%, obligation at 85% → tune preemption prompt".

### Phase A–E sequencing
1. **A** (the column) — unblocks B–E; must land first.
2. **B + D** — ship together, high immediate value, no inter-dependency.
3. **C** — has the cursor footgun risk; test with a single agent before rolling out.
4. **E** — run after first per-agent CSV round-trip to see the baseline accuracy numbers.

---

## PN Enrichment Plan (PNE) — Policy Navigator Extraction Asks (2026-07-06)

> Source: `RC_PIPELINE_EXTRACTION_ASKS_20260706.md` (PN data/taxonomy review) —
> 8 asks to emit more structure at extraction time. Full evaluation + RC response:
> `docs/pn_asks_response.md`. Status legend: ✅ done · 🔧 in progress · ⏳ ready · 🔒 gated.
>
> **Operator decisions (2026-07-06):** (1) RC enriches the `synced_extractions`
> payload only — PN's ingestion maps into their tables; RC does not write PN's
> internal schema. (2) RC's ratified vocabularies stay canon; PN values shipped
> via crosswalk (both codes emitted). (3) Prompt-change asks held behind the EA1
> gold-set baseline, per standing discipline.
>
> **Key findings that reshaped the memo:** RC's own `payload_adapter.py`
> whitelists fields and was *stripping data already extracted*
> (`interpretation_risks`, `safe_harbor`, `consent_requirements`, `object`,
> structured timeline) — fixing that beats new extraction. Coverage query
> (run via Supabase MCP; PN couldn't — 502s): **all 8 PN target tables at 0
> rows, `synced_extractions` itself 0 post-P2-purge** → next sync is
> greenfield, enrichment landed now ships in the first real rows.
> `fact_laws` booleans (`small_business_exempt`/`private_right_of_action`)
> are all-default `false` for 221 laws — fake data reading as an affirmative
> legal claim; PN advised to make them nullable. Ask 8 is moot (ambiguity
> agent retired; `interpretation_risks` embedded on obligation/rights rows —
> DI-4); RC-side fix is the adapter pass-through.

### PNE-1 — Stop dropping what's already extracted (deterministic, sync-layer)
- ✅ **PNE-1a** — adapter pass-through landed (2026-07-06, commit `0e4263b`):
  `_adapt_obligation` ships `object`, `safe_harbor`, `consent_requirements`,
  `interpretation_risks`, `preemption_signals`, and the structured timeline
  object as `timeline_structured` (with `date_parse_status`) alongside the
  flattened string; `_adapt_rights_protection` ships `interpretation_risks`.
  Backward-compatible (existing keys unchanged). *(BE)*
- ✅ **PNE-1b** — Ask 7 provenance landed (same commit): `provenance
  {content_hash, retrieved_at, section_locator}` attached to every synced
  payload from `document_versions.source_hash`/`retrieved_at` (both sync
  legs); pre-RR7b rows carry honest nulls, never fabricated values. *(BE)*
- ✅ **PNE-1c** — 14 tests (11 adapter pass-through, 3 provenance incl. JSON
  serializability); response memo committed. 1104/1104 passing. *(BE)*

### PNE-2 — Deterministic derivation (mapping code, no LLM)
> **Operator decisions (2026-07-06, second check-in):** crosswalks are
> **alias-aware** — when RC's canonical code is `deployer` but the raw term
> was "employer", emit PN `actor_role="employer"` (use the 215-row alias
> table to recover PN's finer value). All PNE-2 derivations computed
> **sync-time in the adapter** (retroactive on all stored rows, no
> re-extraction, revisable by re-sync). Execution paused pending operator
> review of PNE-1.
- ✅ **PNE-2a** — Ask 1 landed (2026-07-06, commit `2243de4`): new
  `src/core/pn_crosswalk.py` + `data/lookups/pn_actor_crosswalk.csv` (rc_code→
  pn_role) + `pn_actor_alias_overrides.csv`. `_adapt_obligation` emits
  `actor_role_rc` (RC 13-code via ratified alias table), `actor_role` (PN
  7-value, **alias-aware** — word-boundary match recovers employer/vendor/
  integrator from the raw subject; "employment agency" correctly does NOT match
  employer), and `enforcement_authority` (from `enforcing_body`, strictly
  separate). Enforcer/individual codes → null PN role so they never display as
  regulated actors. *(BE, NLP)*
- ✅ **PNE-2b** — Ask 2 landed (same commit): `pn_obligation_type_crosswalk.csv`
  (RC 22-family → PN 13-value). `_adapt_obligation` emits `obligation_family`
  (via the same `_classify_obligation_family` concept-grouping uses, so sync
  matches the concept layer) + `obligation_type` (PN). `obligation_general`
  → null PN type. *(BE)*
- ✅ **PNE-2c** — Ask 3a landed (same commit): `deadlines[]` in `_adapt_obligation`
  derived from `timeline_structured`, emitting one entry per date field marked
  `parsed` in `date_parse_status` (unparsed prose skipped, never used in a
  deadline_date). Per-cohort phasing stays PNE-4a (EA1-gated). *(BE)*
- ✅ **PNE-2d** — Ask 4b landed (same commit): `derive_trigger()` emits
  `{trigger_type, trigger_operator, trigger_value, trigger_condition_raw}` in
  `_adapt_threshold`. Operator parse keeps `gt`/`lt` distinct from `gte`/`lte`
  (so "more than 50" stays strictly >50, boundary not silently shifted); raw
  condition preserved so PN can fold to its 4-value enum without losing the
  exact phrasing. Unparseable values kept as string, never a fabricated number.
  Ask 4a (stable id) ships as `system_a_extraction_id` — documented in the
  module. *(BE)*
- ✅ **PNE-2e** — **finding, not a code change:** there is **no per-extraction
  `compliance_tags`/`domain_tags` field** anywhere in the RC extraction payload
  or sync path (grepped `src/`). The memo's hygiene item describes a *law-level*
  path (`map_law_scopes` scope codes → migration 025 → `fact_laws.domain_tags`
  on PN's side), which is outside the per-extraction payload-enrichment scope
  of this plan. Nothing to align in the adapter; flagged rather than fabricating
  a tag field (same discipline as Ask 8). *(BE)*

### PNE-3 — Law-level rollups (deterministic aggregation)
> **Operator decisions (2026-07-06, third check-in):** (a) rollup ships as a
> synthetic `extraction_type="law_summary"` row per law in the `synced_extractions`
> stream (fits the one-table contract; PN routes it to `fact_laws`); (b) authority
> classification is heuristics + review queue (confident label or `unknown` +
> `needs_review`, never a guessed label).
- ✅ **PNE-3a** — Ask 5 landed (2026-07-06, commit a071a9a): `src/core/law_summary.py` —
  `build_law_summary()` aggregates `min_employees`/`min_revenue`/
  `consumer_count_trigger` (smallest numeric trigger = applicability floor,
  reusing PNE-2d `derive_trigger`), `small_business_exempt` (exception-text
  markers), `private_right_of_action` (enforcement, obligation + bill-level).
  **Honesty rule:** booleans are `None` on absence, never False — directly
  avoids the legal-overclaim class the coverage audit found live in PN's
  all-`false` columns. Emitted via a new `sync_law_summaries()` leg with
  synthetic ids `LAW_SUMMARY_ID_BASE (2e9) + family_id`; `_get_cursor()` now
  excludes that range so the synthetic ids can't poison the MAX(id) watermark
  and starve real extractions (the Phase-C cursor footgun, avoided). Upsert, so
  every run refreshes each law's summary. *(BE, NLP)*
  — Note: the plan named `normalize_enforcement_for_law()` (EA5-2, zero
  callers) as the wiring target; the actual PROA signal was simpler to read
  directly off enforcement payloads, so that function stays uncalled — flagged,
  not silently worked around.
- ✅ **PNE-3b** — Ask 6 landed (2026-07-06, commit a071a9a): `src/core/authority_classifier.py`
  — deterministic `classify_authority(bill_number, title, source_url)` →
  `{authority_type, binding_effect, issuing_body, authority_confidence,
  needs_review}`. Bill number ⇒ statute/binding; title keywords (guidance,
  executive order, ordinance, regulation, court opinion) win when they name a
  non-statute; "proposed" downgrades binding→proposed; URL domain as fallback;
  no positive signal ⇒ `unknown` + `needs_review` (the manual queue = filter
  law_summary rows on `needs_review`/low confidence). Merged into the
  law_summary payload via `build_law_summary_payload()`. *(BE, operator review)*

### PNE-4 — Gated (EA1 baseline or design ruling)
- 🔒 **PNE-4a** — Ask 3b: per-cohort deadline extraction (prompt/schema change). *(after EA1)*
- 🔒 **PNE-4b** — Ask 4c: threshold→obligation `applies_to_obligation_id` linking —
  needs a design (same-passage co-location vs concept key vs model reference). *(design ruling)*
- 🔒 **PNE-4c** — Ask 6 (full): LLM classification pass if PNE-3b residue is too
  large. *(after EA1)*

**Sequencing:** PNE-1 now (greenfield window — land before the operator's next
Extract All + sync so the first synced rows carry the enrichment). PNE-2 next,
PNE-3 after. PNE-4 queues behind EA1, which remains the long pole.

---

## Silent-Failure Hardening Plan (SFH) — from external pipeline audit (2026-07-06)

> Source: 16-page external audit (`regs_checker_audit.pdf`, assessed **main** branch
> 2026-07-06). Every load-bearing claim was re-verified against **this branch**
> before planning — the audit is high quality (all spot-checks confirmed:
> `stop_reason='loop'` at `llm_provider.py:306` vs `base.py:358` `== "length"`,
> reparse delete at `pipeline.py:195–205`, confidence weights 0.50/0.35/0.15,
> nvidia triage temp 0.2/top_p 0.7, CV/gap on same-family `gpt-oss-20b`) — but a
> meaningful slice is stale because this branch is far ahead of main.
> Status legend: ✅ done · 🔧 in progress · ⏳ ready · 🔒 gated.
>
> **Operator decisions (2026-07-06, audit check-in):** (1) **Plan only for now** —
> no SFH code lands until the operator reviews this assessment; every ⏳ below is
> approved-in-principle but awaits the go signal. (2) **Pseudo-Orrick quarantine
> approved** (SFH-1f): honoring the `llm_generated` stamp in scoring is an honesty
> fix, accepted knowing tiers on enrich-orrick-only laws will drop to the gated
> path until the confidence re-architecture (SFH-3) gives tracker-silent laws a
> fair path. (3) **Triage temperature 0.2→0 approved; routing threshold change
> declined** — the threshold stays pinned per the EA0-2 resolution until the eval
> set prices routing's recall cost; SFH-1d's delta report supplies the data.
> (4) RC Supabase checked live: `ACTIVE_HEALTHY` (earlier timeout was transient),
> and the audit-P0 applicability query is **answered — 169/169/169, no backfill**
> (see Phase 1a above).
>
> **Audit findings already resolved on this branch (stale — no action):**
> SF-07 (runtime schema patching — RC4-1 retired the `_ensure_*` helpers
> entirely, stronger than the audit's "keep but loud"); the SF-08 provenance
> *stamp* (`orrick_enrichment.py:221` already writes `orrick_source='llm_generated'`
> — the scoring path ignoring it is the live half); B5's enforcement-agent
> tail-cut fix (EA5-3 — but only for enforcement_agent; the other two bill-level
> agents still head-truncate); B9 per-row provenance (PNE-1b, landed same day);
> repaired/truncated→tier-cap+forced-review (EA2-3, audit marks "verified good");
> CV fail-closed (RR0.1), the P3 publish gate, abstention-as-first-class,
> interpretation-risk dedup — all confirmed "keep" by the audit.
>
> **Explicitly deferred by the audit itself (ignorable now):** chunk-size tuning
> ("do not tune blind"), passage-offset strip-map ("low severity"), entailment
> tracker scoring ("only after weight re-architecture"), Dagster ("once
> versioning lands").

### Phase SFH-1 — Make failure visible ✅ COMPLETE (2026-07-11, operator go given)

> **Execution note (2026-07-11):** all 14 items landed across 6 commits
> (`aa1bb7c` 1a+1b, `292a90d` 1c+1k, `d071410` 1f, `95edcf5` 1h/1i/1m/1n,
> `7d92f0c` 1d+1e, `9ebe513` 1g, `c923161` 1j+1l). 1212/1212 tests passing
> (+49 new this phase); CI hard gate green throughout. Two migrations added
> (`3f8a2b9c1d04` sync_skips, `4a9b3c8d2e15` sync_runs) — **operator: run
> `alembic upgrade head`**. Notable finds during execution: SFH-1k's schema
> guard caught a LIVE crash in the merged a7f723d enrichment (dv.canonical_key
> UndefinedColumn + three INSERT columns that don't exist on PN's table —
> reconciled to payload-only, fixed in 292a90d); the SF-08 provenance stamp
> already existed (orrick_enrichment.py:221) — only the scoring path ignored
> it; the truncated-JSON salvage turns out to only repair bare-array shapes
> (envelope-shape cuts are unrepairable by the current chain — documented in
> tests, feeds the SFH-3b structured-outputs case).
- ✅ **SFH-1a** **[High]** SF-04 loop-truncation bypass: treat `stop_reason in
  ('length','loop')` as truncated at both consult sites (`base.py:358` truncation
  flag; `base.py:~481` retry-with-doubled-budget condition); record `stop_reason`
  in extraction metadata; count loops per agent in `agent_stats.json`. Closes the
  one truncation path that today sails through with full confidence eligibility. *(BE)*
- ✅ **SFH-1b** **[High]** SF-06 passage-conservation check: run-end invariant
  `selected == extracted + abstained + failed + skipped_boilerplate + skipped_dedup`,
  each term emitted in `run_summary.json`, hard alert with residual ids (set
  difference) on any mismatch. Kills the 660-vs-647 class of silent loss. *(BE)*
- ✅ **SFH-1c** **[High]** SF-03 sync-skip persistence: `sync_skips` table
  (extraction_id, doc_family_id, reason, run_ts; Alembic migration) + persist on
  every bridge-miss in both legs + `--resync-skips` replay mode + alert naming the
  unmapped families. Today the id cursor advances past unmapped rows forever. *(BE)*
- ✅ **SFH-1d** **[Medium]** SF-02 routing recall delta: tag sampled passages
  (`routing_bypassed=true` in metadata), compute at run end which extractions came
  from agents routing would have skipped, emit delta + false-narrowing rate in
  `run_summary.json`, alert over threshold. The 5% sampling cost currently buys
  zero monitoring value. *(BE)*
- ✅ **SFH-1e** **[Medium]** SF-05 salvage accounting: count array elements
  pre/post `_repair_truncated_json`, store `items_dropped_by_repair` in extraction
  metadata, aggregate per-strategy repair hits into `run_summary.json`, alert when
  run repair rate exceeds ~3%. *(BE)*
- ✅ **SFH-1f** **[High]** SF-08 remainder (quarantine approved): Pydantic-validate
  tracker metadata keys at read time (fail loud — kills the
  'enforcement'-vs-'enforcement_penalties' drift class); make the scoring path
  honor the existing `orrick_source='llm_generated'` stamp — generated summaries
  score as tracker-absent (triage keyword seeding only); per-run counts of laws
  scored against generated vs. real tracker data. **Known consequence, accepted:**
  enrich-orrick-only laws drop to the gated/capped path until SFH-3. *(BE, NLP)*
- ✅ **SFH-1g** **[Medium]** SF-09 sync observability: `sync_runs` row per
  invocation (leg, started, finished, synced, skipped, updated, error) + freshness
  check in `sync_monitor.py` (alert when newest `synced_at` exceeds cadence, or a
  run syncs 0 with pending cursor rows). *(BE)*
- ✅ **SFH-1h** **[Medium]** SF-10 reparse lineage guard: within-version re-parse
  requires explicit `--force-reparse` (logs count of extraction rows orphaned);
  never delete across versions — text change ⇒ new `DocumentVersion` with
  `predecessor_id`. **Prerequisite for the entire SFH-4 live-data phase.** *(BE)*
- ✅ **SFH-1i** **[Low]** SF-11 + B8 meta-monitoring: count triage-warning write
  failures (the `except Exception: pass` at `section_triage.py:67`) and
  summary-generation failures in `run_summary` — never raise, never invisible. *(BE)*
- ✅ **SFH-1j** **[Medium]** B5 remainder: extend the EA5-3 input-targeting pattern
  (pattern-located sections + bounded tail, no raw head-truncation bias) from
  enforcement_agent to **applicability_agent + compliance_timeline_agent** —
  deadlines and applicability clauses also live in bill tails. Same
  strictly-better-input class EA5-3 landed under. *(NLP)*
- ✅ **SFH-1k** **[Low]** B9 schema-drift guard: startup assertion that the sync
  INSERT column list matches `synced_extractions` information_schema (five lines
  that would have caught the months-long "INSERT never succeeded" episode). *(BE)*
- ✅ **SFH-1l** **[Medium]** B10 process: one-passage end-to-end CI smoke test
  (triage → routing → one agent with stubbed provider → persistence) so wiring
  errors fail CI; fix README vs `architecture.md` provider drift
  (`config/agent_models.json` is authoritative). *(BE, DevOps)*
- ✅ **SFH-1m** **[Medium]** EA4-1 config flip (audit B7 concurs): move
  `cross_validation`/`gap_detection` from `openai/gpt-oss-20b` to a
  different-lineage model ≥ extractor capability (e.g. `meta/llama-3.1-70b-instruct`)
  in `config/agent_models.json`; catch-rate measurement on seeded-error fixtures
  stays with the operator (needs live LLM). *(NLP)*
- ✅ **SFH-1n** **[Low]** Triage determinism (approved): nvidia triage
  `temperature 0.2 → 0`, `top_p → null` in `config/agent_models.json` — variance
  reduction on a binary gate. (Routing threshold explicitly NOT changed — see
  operator decision 3.) *(NLP)*

### Phase SFH-2 — Operator actions ✅ COMPLETE (2026-07-12)
- ✅ **SFH-2a** — merge `claude/brave-lamport-d9zgjx` → main: **confirmed already
  merged**, and not recently — 23 merge-commit references on `origin/main`
  (PRs #134–155, oldest well before this SFH phase started). Verified via
  `git merge-base --is-ancestor 8ff0f2c origin/main` (the branch's merge
  commit) → true. This branch (`claude/legal-extraction-architecture-1exlem`)
  already carries main's history through its own `80f4750` merge-from-main
  commit, so the BUG-7 NameError fixes have been live throughout SFH-1. No
  action needed; the tasks.md top-item note describing it as unmerged was stale.
- ✅ **SFH-2b** — applicability confirm query: **done 2026-07-06 via Supabase MCP**
  (169/169/169 — no backfill; see Phase 1a).
- ✅ **SFH-2c** — eval-set size ruled (2026-07-12, operator decision): **8 laws**.
  Audit suggested 20–30; EA amendment #4 floored it to 8–10 for solo annotation
  capacity; operator picked the floor. Unblocks EA1-1 annotation — 8 laws,
  single annotation + strong-model adjudication on disagreement candidates,
  prioritizing agents that feed the PN matrix (obligation, threshold_exception,
  enforcement_agent, applicability_agent), per EA amendment #4's own criteria.
  Expand only if EA1-3 variance shows 8 is too small to detect regressions.

### Phase TA — Triage audit & efficiency hardening (2026-07-12)
> Full read-through of `src/agents/section_triage.py` (799 lines) + `run_triage`/
> `run_retry_failed_triage` in `extractor.py` + the triage dashboard endpoints,
> prompted by fixing the `DocumentFamily.label` AttributeError in Triage Results.
> Baseline from the last full run: 805 relevant / 154 uncertain / 265 not_relevant
> of 1,224 passages — triage filters only ~22% of the corpus (uncertain also goes
> to extraction), so precision matters as much as the recall-first design intended.
- ✅ **TA-1** — `_extract_orrick_terms` generic phrase regex removed. The
  `\b[a-z][a-z\s\-]{4,30}\b` sweep over OCR-garbled `key_requirements` text was
  auto-marking passages `relevant` (conf ≥0.75, **no LLM ever sees them**) on
  noise phrases from scrambled OCR text — undermining the intra-bill filtering
  triage exists to do. Kept: `ai_scope`/`iapp_ai_topic` splitting (controlled
  vocabulary) and the curated single-word regulatory-term whitelist.
- ✅ **TA-2** — concurrent LLM triage. `run_triage` now builds all per-record DB
  context serially (unsafe to share a Session across threads) then fans the
  DB-free `triage_passage()` LLM calls out to a `ThreadPoolExecutor`, mirroring
  the existing `_run_agent`/extraction concurrency pattern exactly. New setting
  `triage_concurrency` (default 3; lower to 1 for single-GPU LM Studio, same
  caveat as `max_concurrent_agents_per_model`).
- ✅ **TA-3** — bill-context blocks in the triage prompt trimmed: definitions
  30K→6K chars, scope 20K→5K chars. ~15K input tokens/call → ~6-7K for what's
  ultimately a binary relevance call. *(Soft-gated: re-verify against the EA1
  gold set once it exists, in case trimming ever flips a real decision.)*
- ✅ **TA-4** — `quality_fail` confidence semantics fixed. Was storing the raw
  PDF-quality score as `decision` confidence (a 0.1-quality passage read as
  "10% confident it's not_relevant" — backwards). Now stores confidence in how
  certain the *decision* is (high — "this is unreadable" is an easy call) and
  keeps the raw quality score only in `pdf_quality_score`. Pure bug fix, no
  threshold/routing behavior changed — safe outside the SFH-3a gate.
- ✅ **TA-5** — passages under `MIN_PASSAGE_LENGTH` (150 chars) now get a real
  `SectionTriageResult` row (`not_relevant`/`quality_fail`/`too_short` flag)
  instead of being silently dropped from the triage loop. Fixes two things:
  the pipeline tracker's "Triaged X/Y" bar could never reach 100% (denominator
  included passages that would never get a row), and there was no way to tell
  "not yet triaged" from "excluded as too short."
- ✅ **TA-6** — text-hash dedup for triage. `output/law_texts_quarantine/NEEDED_SOURCES.md`
  documents 12 byte-identical same-bill duplicate pairs; identical passage text
  was being triaged (and would be extracted) twice. `triage_passage` results are
  now cached by a hash of `(text, ai_scope, key_requirements)` within a run.
- ✅ **TA-9** — parser bug found while spot-checking real `too_short` rows against
  ground-truth source (`TMP-MA-AMENDMENTTOTHE`): `_segment_text`'s section-marker
  regex can't tell "this bill's own section marker" from "a cross-reference to
  the code being amended." `"SECTION 7. Chapter 272 of the General Laws is
  hereby amended..."` is one continuous clause in real MA-style amendment
  bills, but `"Chapter 272"` also matches the marker pattern, so the lookahead
  stopped right after `"SECTION 7."` — producing an empty stub AND mislabeling
  Section 7's real ~3,900-char body under `"Chapter 272"` instead (content
  wasn't lost, but citations/section_path were wrong). New
  `_splice_marker_only_stubs()` in `src/ingestion/parser.py` merges any
  marker whose captured body is empty into its successor before the size-based
  chunk-merge pass runs; handles chains of back-to-back empty markers too.
  Verified against the real file: 9 chunks → 7, all four affected sections
  (2/3/7/8) now correctly attributed. 4 new regression tests.
- ✅ **TA-10** — "last updated" timestamps added to the checker panels audited
  this session, via a new shared `_format_last_updated()` helper (absolute
  UTC + relative "Xm/h/d ago") in `_dashboard_helpers.py`: Triage Results
  ("Last triaged"), Triage Warnings ("Last warning"), Pipeline Tracker ("Data
  as of", MAX across fetch/parse/triage), Failed Documents (per-row "Updated"
  column + "most recent" summary using `IngestionJob.updated_at`), Browse
  Documents (per-row "Parsed" column using `parse_completed_at`). Lets an
  operator tell at a glance whether a panel reflects the run they just
  kicked off or stale data from days ago.
- ✅ **TA-11** — extraction stall + Terminate diagnosis, prompted by a real
  16-minute stuck extraction run where clicking Terminate had no effect.
  Root causes: (1) `NvidiaLLMProvider.call()` used `stream: false` — one
  blind blocking request with zero signal between "still generating" and
  "actually stuck," a 300s per-attempt timeout, and 5 retries, so a truly
  stalled call could silently run ~25 min before failing; (2) cancellation
  (`request_cancel()`) was only checked between passages, never inside an
  in-flight LLM call or its retry/backoff loop, so Terminate couldn't
  interrupt a stuck call at all; (3) background extraction runs in a
  `daemon=True` thread invisible to uvicorn's `reload=True` (used by
  `start.py`) — a worker restart can orphan that thread, which keeps
  retrying against a fresh process's un-set cancel flag (observed directly:
  retry logs continued after `"Finished server process"`).
  Fixes: switched `NvidiaLLMProvider.call()` to `stream: true` with a 60s
  per-chunk idle timeout (httpx's `read` timeout applied to streaming reads)
  — steady chunk arrivals now prove it's genuinely working; silence for 60s
  (not 300s) means it's actually stuck. New `src/core/cancellation.py`
  (`is_cancelled`/`OperationCancelled`, re-exported from `extractor.py` to
  avoid a circular import with `src/agents/base.py`) is checked before each
  retry attempt, between every streamed chunk, and during backoff sleeps
  (broken into 0.5s increments) — a stuck call now aborts within seconds of
  Terminate instead of only after the full retry storm finishes.
  `agents/base.py`'s outer "retry once" wrapper re-raises `OperationCancelled`
  immediately instead of attempting a second call. Preserves the exact same
  `LLMResponse`/retry/backoff/error-message behavior on the non-cancelled
  paths — verified via 11 new tests mocking `httpx.stream` (happy path,
  missing usage data, reasoning-content stripping, malformed chunks, 429 and
  transport-error retry-then-raise, transport-error-then-recover, cancel
  before/during/mid-stream with a timing assertion). The pre-existing
  `TestNvidiaLLMProvider` suite in `test_llm_provider.py` mocked the old
  `stream:false` path via full `sys.modules["httpx"]` replacement, which
  silently produced a `TypeError` on the new code that only looked like a
  pass because the tests asserted `pytest.raises(Exception)`; rewrote all 9
  to mock `httpx.stream` properly, preserving each one's original
  regression-guard intent (double-`/v1`, bearer header, temperature default,
  empty-content, 401, 429, reasoning_content, model_override).
  The orphaned-daemon-thread risk (3) is a `reload=True` + background-thread
  interaction, not something fixed by this change — flagged to the operator
  as "check Task Manager for a stray python.exe if Terminate doesn't work."
  Not yet verified against the live NVIDIA endpoint (sandbox has no network
  access to it) — the gated `tests/integration/test_nvidia_provider.py`
  suite (`NVIDIA_API_KEY=... pytest tests/integration/ -v`) is the way to
  confirm on the operator's machine.
- ✅ **TA-12** — fixed a false-positive stall detection introduced by TA-11,
  found from a real extraction stuck 44+ minutes on `nvidia_transport_error_exhausted
  error='The read operation timed out'` for `openai/gpt-oss-120b` (a reasoning
  model, run with `reasoning_effort: None` for `threshold_exception`/
  `compliance_mechanism` in `config/agent_models.json` — no cap on NVIDIA's
  own default reasoning effort). Root cause: TA-11's 60s idle-chunk timeout
  assumed silence always means "stuck," but reasoning models can legitimately
  go quiet server-side for well over a minute while "thinking" before their
  first streamed byte, and NVIDIA's hosted endpoint does not appear to
  stream any interim signal during that phase. The old pre-TA-11 blind 300s
  whole-response wait tolerated this invisibly; TA-11's 60s idle detector
  killed and retried calls that were working fine, and since every retry hit
  the same reasoning-latency wall, the call never succeeded no matter how
  many times it retried — explaining the repeating, non-recovering timeout
  pattern read at first as an NVIDIA outage. Fix: `NvidiaLLMProvider` now
  picks the idle timeout by model — reasoning models (tag match on
  `deepseek-r1`/`qwen3`/`gpt-oss`) get `_IDLE_TIMEOUT_REASONING_SECONDS =
  180.0`; everything else keeps `_IDLE_TIMEOUT_SECONDS = 60.0`, since
  instruct models stream almost immediately and a stall there still means
  stuck. 2 new tests assert the selected `httpx.Timeout.read` value per
  model class; full suite (1241 tests) green. Retry count/backoff and the
  redundant "retry same model" layer in `agents/base.py::_call_llm` were
  deliberately left untouched this round — tightening those was the
  originally-proposed fix but was superseded once the timeout-miscalibration
  theory was confirmed as the more likely cause; revisit only if 180s still
  proves insufficient on the live endpoint.
- 🔒 **TA-7** — extraction-yield feedback loop (deferred, not gated but bigger
  lift): record whether each `uncertain` passage produced any extractions
  across all 6 agents. Zero-yield uncertain passages are free FN/FP evidence —
  feeds directly into EA1/EA4-3. Needs a join between `SectionTriageResult` and
  `Extraction` plus a report; scoped as its own item rather than folded in here.

### Phase QA — first-real-run output audit fixes (✅ COMPLETE 2026-07-13)
> Driven by the operator's export of all 37 extractions from the 2026-07-12
> run (AZ SB1462, AZ SB 1359, AR HB1877) — the first real batch after the
> TA-11/TA-12 streaming fixes. Every finding below was verified against the
> committed source files in `output/law_texts/`, not just the CSVs.
>
> **Execution note (2026-07-13):** All five items implemented, tested, and
> pushed to `claude/legal-extraction-architecture-1exlem`. Commit c5b0678
> contains full QA suite. Unit tests: 1285/1285 passing; CI hard gate clean.
> No errors on first pass; systematic approach (understand root cause via real
> data → implement → test with real fixtures → verify full suite) avoided rework.

- ✅ **QA-1** — Tier-4 span-verification ordering bug: `verify_evidence_spans`
  computed its Tier-4 input as `strip_revisor_artifacts(norm_passage)`, but
  norm_passage is already whitespace-collapsed, so the line-anchored margin-
  number/hyphen-break regexes could never fire — Tier 4 was silently a no-op
  (and had zero test coverage, which is how it shipped). Perfect formatting
  correlation in the run: clean-text SB1462 verified 12/12 spans; the two
  line-numbered bills 3/37. Fix: strip on the raw line-structured text BEFORE
  collapsing, symmetrically for passage and span. Replay of the failed spans
  against real sources: 3/37 → 32/37; the 5 still failing are genuine model
  fabrications (text absent from the bill — correct rejections). 10 tests in
  `test_tier4_margin_numbers.py` using the real AR/AZ formatting. *(NLP, BE)*
- ✅ **QA-2** — definition_actor hallucination guards: llama-3.1-8b invents
  actors ("Developer" on a definition naming no actor) and cross-contaminates
  framework_refs (NIST on definitions that never mention it). New
  `_postprocess_extraction` hook on BaseExtractionAgent (default no-op);
  DefinitionActorAgent drops actors/framework_refs not grounded in the
  definition context (term+definition_text+scope, loose-normalized, half-of-
  significant-tokens rule for partial quoting). Definition-scoped on purpose:
  the observed hallucinations DO appear elsewhere in the passage. 11 tests in
  `test_definition_actor_grounding.py`. *(NLP, BE)*
- ✅ **QA-3** — responsible_party_normalized force-fit: the compliance_mechanism
  prompt offers only 4 buckets, so "person who acts as a creator" came back
  "developer". New `reconcile_normalized_actor()` (actor_normalizer): keep the
  LLM value only when the raw phrase lexically contains it or the ratified
  alias table maps both to one code; else the alias table's code for the raw
  phrase (genuine hits only); else null → routes to vocab review (B4). Applied
  at extraction (ComplianceMechanismPayload model_validator) AND at sync
  (_adapt_compliance_mechanism) so stored rows repair retroactively. Prompt
  now says use null rather than forcing the nearest bucket. 12 tests in
  `test_reconcile_normalized_actor.py`. *(NLP, BE)*
- ✅ **QA-4** — cross-passage definition dedupe: HB1877 produced 14 definition
  rows for ~6 terms (overlapping passages re-extracting the same code
  section). Existing payload-hash dedup is single-record + exact-equality
  only. New law-level (document_version) check for definition extractions:
  dupe when loose term matches AND texts are near-identical (equal / prefix /
  ≥0.9 SequenceMatcher — measured on the real rows: true dupes 0.94–0.98,
  distinct-section same-term definitions 0.74). First-write-wins; skips
  logged with the surviving extraction id. 11 tests in
  `test_definition_cross_passage_dedupe.py`. *(NLP, BE)*
- ✅ **QA-5** — EA1 gold-set seed: 2 new fixtures
  (`az_sb1359_sec16_1023_deepfake_disclosure`, `ar_hb1877_sec1_csam_definitions`)
  with passage text copied verbatim INCLUDING bill margin numbers and the
  mid-definition page break — they double as end-to-end Tier-4 grounding
  regressions (all expected spans verify at Tier 1 post-QA-1), and satisfy
  EA1-1's deepfake-law + engrossed-markup + OCR-quality strata. Plus
  `run_labels/2026-07-12_extraction_run_labels.csv`: hand-checked verdicts
  for all 37 run extractions (correct/partial/incorrect/duplicate + error
  vocab), including the negatives the fixture format can't express — the
  preemption A-tier misclassification (SFH-3a exhibit), 4 fabricated-quote
  rows, the duplicate cluster, and the normalization force-fit. Plus
  `run_labels/README.md` documenting verdict vocabulary, error_types, and
  notable rows. All fixtures pass structure validation; every expected evidence
  span verifies at Tier 1 post-QA-1. ~~NOTE for EA1-2: `harness.py` still calls
  the pre-ExtractionResult agent API~~ — **resolved: EA1-2 landed 2026-07-13**,
  harness now consumes `ExtractionResult` and covers all 9 agents; these
  fixtures produce a baseline once EA1-3 runs on the operator's machine.
  *(RPR, NLP, BE)*
- 🔒 **TA-8** — any threshold/keyword-list retuning (the LLM 0.4 not-relevant
  cutoff, keyword confidence curve, `_ADJACENT_AI_KEYWORDS` promotion). **Hard-
  gated on the EA1 gold set baseline capture (EA1-3)** per SFH-3c — measure
  before tuning.

### Phase SFH-3 — Trust model (🔒 gated: EA1 gold set + product ruling)
- 🔒 **SFH-3a** — confidence re-architecture: **merge EA3 + Phase-4c weights +
  audit B6 into ONE plan** (per EA amendment #5 — whoever lands first absorbs the
  other; the audit is now a third independent vote for evidence-first: its
  proposed shape evidence 0.40 / CV 0.25 / numeric+citation 0.20 / tracker ≤0.15
  ≈ EA3-1). Includes: tracker → separate `tracker_status` axis
  (confirmed/silent/conflict/generated-only), auto-Tier-D replaced by
  "unvalidated → priority review". Validate on gold set before serving;
  contradiction #1 product ruling required. *(NLP, product owner)*
- 🔒 **SFH-3b** — structured outputs (= EA6-2, audit B4 concurs): NIM
  `response_format` guided JSON; repair chain becomes telemetry. After the EA1
  regression gate exists. *(NLP, BE)*
- 🔒 **SFH-3c** — routing threshold + triage-model A/B: tune against the eval set
  using SFH-1d's delta data (EA0-2 discipline holds). *(NLP)*
- 🔒 **SFH-3d** — B2 subsection map into agent context (changes model input;
  eval-gated); citation-granularity lift for merged chunks. *(NLP)*

### Phase SFH-4 — Live data (🔒 gated: SFH-1h prerequisite + operator/product sign-off, API keys, cost)
- 🔒 **SFH-4a** — LegiScan change-hash delta ingestion (cron MVP: daily status,
  text-change ⇒ new DocumentVersion via `source_hash` compare); Congress.gov for
  federal; weekly Orrick/IAPP refresh + `status_checker.py` scheduling. *(BE, DevOps, operator)*
- 🔒 **SFH-4b** — diff-driven re-extraction: per-ordinal text_hash diff vs
  predecessor passages; extract only new/changed; copy-forward unchanged with
  lineage link; supersede (never delete). *(BE, NLP)*
- 🔒 **SFH-4c** — bounded passage-level concurrency (4–8 in flight; semaphore
  around provider; session-per-thread care) — required before re-extraction
  cadence makes runs frequent. *(BE)*
- 🔒 **SFH-4d** — registry reconciliation (fixes the 232/243/236/211 count drift)
  + weekly missing-text/tracker/bridge report. *(BE, operator)*
- 🔒 **SFH-4e** — later: two-hop DB topology decision; Dagster asset graph
  (Section-A alerts become asset checks); extractor.py/dashboard.py
  decomposition; RR2d test backfill in change-risk order. *(product, BE)*

**Sequencing:** SFH-1 items are independent and land in severity order
(1a/1b/1c/1f first) once the operator gives the go. SFH-2a/2c (both ✅
2026-07-12) were the operator week-one actions. SFH-3 stays behind EA1 —
which both the audit ("the single highest-leverage investment") and the EA
plan agree is the true long pole; EA1-1 is now unblocked on set size (8
laws) and just needs the annotation pass on the operator's machine.
SFH-4 starts only after SFH-1h and an explicit live-data sign-off.

---

### Merge backlog
- `claude/onboard-government-project-3bq7i` (Phase 7M) and `claude/onboard-government-project-PyyB9` (Phase 8) — review and merge after extraction validates on main.

---

### Phase 8 — Export Bugs + Gemma Model Fixes + Low-Confidence Persistence (COMPLETED ✓ 2026-05-23)

All sub-fixes applied and tested; 16,322 rows synced. Export buffer rewind, Gemma
token doubling + reasoning_effort caching, channel-thought recovery, tab-key JSON
repair, low-confidence persistence to disk, Supabase sync schema alignment, and
README/architecture.md reconciliation. **Full breakdown: `completed_tasks.md` (2026-05-23 entry).**

---

## Quality Improvement Backlog

### Phase 1 — DONE (2026-04-05)

- ~~BUG-4: Unicode normalization in evidence span verification~~ — Fixed. `_normalize_unicode()` + `_normalize_text()` added to `BaseExtractionAgent`. 27 tests.
- ~~IMPROVEMENT-1: Tighten ambiguity agent routing signals~~ — Superseded by Phase 1B (agent retired).
- ~~IMPROVEMENT-2: Expand triage keyword list~~ — Done. `_BASE_AI_KEYWORDS` expanded from ~50 to ~65 entries. `_ADJACENT_AI_KEYWORDS` documented constant added.

---

### Phase 1B — Pipeline Restructure: Retire Ambiguity Agent — DONE (2026-04-05)

**Goal:** Retire the standalone ambiguity agent. Embed ambiguity findings as `interpretation_risks`
annotations directly on obligation and rights_protection payloads. Zero additional LLM calls, zero
additional review queue rows, findings attached to the obligation they affect.

#### RESTRUCTURE-1a: InterpretationRisk schema + ObligationPayload + RightsProtectionPayload — DONE
#### RESTRUCTURE-1b: Update obligation and rights_protection prompts — DONE
#### RESTRUCTURE-1c: Remove ambiguity from extraction pipeline — DONE
#### RESTRUCTURE-1d: Update downstream systems — DONE
#### RESTRUCTURE-1e: Archive ambiguity agent — DONE (`src/agents/ambiguity.py` → `src/ingestion/_archived/`)
#### RESTRUCTURE-1f: Dashboard inline display — DONE (2026-04-07). Review queue shows risk cards with severity badges.

**Definition of done:** No new `ambiguity`-type rows after extraction. `interpretation_risks` populated
on obligation/rights rows where relevant. Existing `ambiguity` rows in DB still display. Tests pass. ✓

---

### Phase 2 — Analysis Tasks (human judgment required) — DONE where automatable (2026-04-05)

#### ANALYSIS-1: Build 50–100 row ground-truth eval set
Sample ~100 extractions across tiers/types, have a lawyer verify each.
Record in `data/eval_set.csv`. Gates Phase 3 + 4.

#### ANALYSIS-2: Investigate 856 genuinely non-matching spans
After Unicode fix deployed and extraction re-run: query zero-evidence rows with spans. Sample 20,
categorize failure pattern (adjacent passage? paraphrase? fabrication?).

#### ANALYSIS-3: Gap analysis on keyword-triaged "not_relevant" passages
Query `section_triage_results` where `method='keyword'` and `decision='not_relevant'`. Scan for
AI-adjacent terms not in `_BASE_AI_KEYWORDS`. Feed confirmed gaps to IMPROVEMENT-2 follow-up.

#### ANALYSIS-4: Check Orrick alignment for same Unicode issue — DONE
Confirmed `re.findall(r"[a-z0-9]+", text.lower())` in `orrick_validation.py` is immune to Unicode
typography variants. No fix needed.

---

### Phase 3 — Score Quality — DONE (2026-04-05)

#### IMPROVEMENT-3: Span length penalty in evidence grounding — DONE
Penalizes verified spans >50% of passage length in `src/core/confidence.py`.
- >50%: 20% penalty on evidence_score (×0.80); `broad_spans=True` in breakdown
- >75%: 40% penalty on evidence_score (×0.60); `broad_spans=True` in breakdown
- Only verified spans count; unverified spans and absent `passage_text` skip penalty gracefully
- `broad_spans` flag propagated through both Orrick-gated (Tier D) and normal paths

#### IMPROVEMENT-4: Section reference quality sub-signal — DONE
`_score_section_reference()` scores specificity of `section_reference` field (0.0–1.0):
- 1.0: § + subsection detail (e.g. `§ 6-1-1702(3)(a)`) or nested paren notation
- 0.6: § symbol or clear numeric citation without subsection
- 0.3: generic label only (Section X, Part Y, Article Z)
- 0.2: unrecognized non-empty pattern; 0.0: empty/absent
Blended into completeness at 20% weight — no weight-sum changes.
`section_ref_quality` reported in `ConfidenceBreakdown`.
23 tests in `tests/unit/test_confidence_improvements.py`.

---

### Phase 3B — Dashboard Model Configuration — DONE (2026-04-07)

New `/dashboard/models` page for runtime agent ↔ model assignment:
- Scans LM Studio `/v1/models` for available models
- Per-agent controls: model, max_tokens, context_length, temperature
- Persists to `config/agent_models.json`, reloads agents immediately
- Reset to Defaults button
- `BaseExtractionAgent` gains `max_tokens_override` + `temperature_override`
- `_get_agents()` reads config at instantiation; `reload_agents()` for hot-reload

---

### Phase 4 — Model & Prompt Improvements (requires eval set)

#### IMPROVEMENT-5: Model comparison on eval set
Now easy to A/B test via the Models page — load two models in LM Studio, assign different agents, compare output.
#### IMPROVEMENT-6: Few-shot examples in prompts — `prompts/*.yml`

---

### Phase 7 — Product-Aligned Extraction (Multi-phase Restructure)

**Problem:** The pipeline extracts legal provisions (obligations, definitions, thresholds) but the
Policy Navigator product needs compliance decision-support data (does this apply to me? what do I
have to do? what penalty if I don't?). Empty/sparse product tables: `law_enforcement_details` (0
rows), `law_triggering_thresholds` (28 partial), `law_obligation_flags` (56, none derived from
extractions). Root cause: per-passage agents can't see cross-section context (e.g. the obligation
text references a penalty defined in another section the agent never sees).

**Strategy:** Add **bill-level agents** that run once per law with full bill text, producing one
structured record per law mapped directly to product tables. Layer on top of existing per-passage
agents — don't replace them.

#### Phase 7A — Enforcement Context Injection — DONE (2026-05-08)
Injects bill enforcement/penalty sections into obligation agent context block.
- `src/core/bill_context.py`: `_ENFORCEMENT_PATTERNS` + `_ENFORCEMENT_SECTION_PATH` regexes,
  collects enforcement passages into `bill_context["enforcement"]`, budgeted at 10k chars
- `src/ingestion/extractor.py`: maps `bill_context["enforcement"]` → `ctx["bill_enforcement"]`
  in both context-building paths
- `src/agents/base.py`: new `BILL ENFORCEMENT & PENALTIES` block in `_append_bill_context()`
- Decision gate: measure non-null rate on `obligation.enforcement.max_civil_penalty_usd` after next run

#### Phase 7B — Bill-Level Agent Infrastructure — DONE (2026-05-08)
- `src/agents/bill_level_base.py`: `BillLevelAgent` abstract base + `BillLevelResult` dataclass;
  reads model config from `agent_models.json`; LLM calling, JSON repair, retry logic
- `src/db/models.py`: `BillLevelExtraction` model keyed by `(document_version_id, agent_name)`
  with unique constraint (one row per law per agent, re-runs upsert)
- `alembic/versions/k7h3i9j1f612_add_bill_level_extractions.py`: migration creating the table
- `src/ingestion/extractor.py`: `_get_bill_level_agents()` lazy-imports agent classes;
  `_run_bill_level_agents()` assembles full text, runs agents, upserts; called after each dv loop

#### Phase 7C — Enforcement Agent — DONE (2026-05-08)
`src/agents/enforcement_agent.py` — `EnforcementAgent` (1024 max_tokens)
- Extracts: `enforcing_body`, `max_civil_penalty_usd`, `penalty_per`, `cure_period_days`,
  `private_right_of_action`, `criminal_penalties`, `enforcement_text`
- Maps to `law_enforcement_details`

#### Phase 7D — Applicability Agent — DONE (2026-05-08)
`src/agents/applicability_agent.py` — `ApplicabilityAgent` (2048 max_tokens)
- Extracts: `covered_entity_types`, `covered_sectors`, `ai_system_types_in_scope`,
  `size_thresholds` (revenue/employees/data/FLOPS), `geographic_scope`, `key_exemptions`,
  `government_only`
- Maps to `law_triggering_thresholds`, feeds `anonymous_audit_profiles` matching

#### Phase 7E — Compliance Timeline Agent — DONE (2026-05-08)
`src/agents/compliance_timeline_agent.py` — `ComplianceTimelineAgent` (2048 max_tokens)
- Extracts: `law_effective_date`, `enforcement_start_date`, `key_deadlines[]`,
  `impact_assessment_frequency_months`, `consumer_request_response_days`, `cure_period_days`
- Maps to `law_obligation_flags` + LawCard deadline view

#### Phase 7F — Threshold Agent Restructure — DONE (2026-05-08)
Additive approach — no DB migration needed; existing 28 rows remain valid (sub_type: null).
- `threshold_sub_type: "scope"|"temporal"|"exemption"|"other"` added to `ThresholdExceptionPayload`
- `revenue_threshold_usd`, `employee_threshold`, `consumer_data_threshold` (typed int fields)
  replace buried free-text values for scope thresholds
- `threshold_type` demoted to specific type within sub_type (numeric, compute, carve_out, etc.)
- Prompt restructured around three-category framework with examples
- `_determine_extraction_type` in extractor routes on `threshold_sub_type` when present,
  falls back to legacy heuristic for existing rows without it

#### Phase 7G — Safe Harbor + Missing Data Types — DONE (2026-05-08)
Added to `src/schemas/extraction.py` + updated all affected prompts:
- **`SafeHarbor`** model (framework, conditions, protection, evidence_text) → `ObligationPayload.safe_harbor`
- **`ConsentRequirement`** model (consent_type, timing, method, subject_matter) → `ObligationPayload.consent_requirements`
- **`protected_categories: list[str]`** → `RightsProtectionPayload` (consumer, employee, candidate, student, patient, minor, tenant, borrower, job_applicant)
- **`retention_period_months: int`** + **`retention_subject: str`** → `ComplianceMechanismPayload` alongside existing `record_retention_period` text field
- **`CrossLawReference`** model (reference_type, law_name, section, description) + **`cross_law_refs: list`** → `PreemptionSignalPayload`
- **`incident_reporting_hours`** already in schema — prompt now explicitly surfaces X-hour/X-day windows
- `preemption.yml` gained a full `system_prompt` (was missing); documents cross_law_refs vocabulary
- All new fields are optional (None/[]) — existing extractions remain valid

#### Phase 7H — Pre-flight Bug Fixes — DONE (2026-05-09)
- `src/agents/base.py`: `_resolve_extraction_prompt()` now calls `_append_bill_context()` after YAML rendering (bill context was silently dropped for YAML-prompt agents)
- `src/agents/bill_level_base.py`: `__init__` only applies config overrides when agent explicitly in `cfg_store.agents` (prevented crash on absent agent keys)
- `src/core/bill_context.py`: Added `_BILL_CONTEXT_VERSION = "v2"` and version-gated cache check (stale v1 cache was returned without rebuild)
- `alembic/versions/g3d9e5f7b208_*`: Removed manual `DO $$ BEGIN CREATE TYPE ... END $$` blocks that collided with SQLAlchemy enum DDL; let `sa.Enum(create_constraint=False)` own type creation

#### Phase 7I — Gemma 4 Thinking Model Support — DONE (2026-05-09)
- `src/core/llm_provider.py`: Added `"gemma"` to `is_reasoning` tag list so Gemma 4 26B-A4B gets `max_tokens × 2` (reserves half for `<think>` block)
- `config/agent_models.json` + `src/core/model_config.py`: Updated all agent token budgets to correct pre-doubling values for Gemma (obligation/rights_protection/compliance_mechanism: 8192 → 16384 effective; definition_actor/threshold_exception/preemption/triage: 4096 → 8192 effective)

#### Phase 7J — Per-Agent Timing + Error Export — DONE (2026-05-09)
- `src/db/models.py`: Added `duration_ms`, `input_tokens`, `output_tokens` columns to `Extraction` model
- `alembic/versions/l8i4j0k2g713_*`: Migration adding those three columns to `extractions` table (pending `alembic upgrade head`)
- `src/ingestion/extractor.py`: `_run_agent()` returns 3-tuple with `duration_ms` via `time.perf_counter()`; all callers updated; value stored on `Extraction` row
- `src/core/extraction_monitor.py`: `AgentStats` gains `total_duration_ms` + `avg_duration_ms` property; `record_agent_result()` accepts `duration_ms` param
- `src/api/routes/dashboard.py`: Agent Performance table shows "Avg Time" column with color-coded latency
- `src/api/routes/dashboard.py`: Added `GET /api/triage-warnings/export.csv` and `GET /api/failed-extractions/export.csv` download endpoints
- Triage Warnings table: "Download CSV" link + "Copy to Clipboard" JS button (`navigator.clipboard.writeText`)
- Failed Extractions widget: "Download CSV" link alongside "Retry Failed" button

#### Phase 7K — Setup Documentation — DONE (2026-05-09)
- `SETUP.md`: Comprehensive setup guide (prerequisites, venv, .env, Docker, migrations, LM Studio, multi-PC, troubleshooting)
- `QUICKSTART.md`: 2-minute fast path for returning developers
- `setup.ps1`: Windows automated setup (checks Python 3.11+, Git, Docker; creates venv; installs deps; copies .env; starts Docker; runs migrations)
- `setup.sh`: macOS/Linux automated setup (same flow)
- `SETUP_ISSUES_AND_OPTIMIZATIONS.md`: Issues found during setup review + Tier 1-3 optimization roadmap

#### Phase 7L — Extraction Efficiency Improvements — DONE (2026-05-09)
- `src/agents/base.py`: Added `call_max_tokens: int | None` parameter to `extract()` and `_call_llm()` for per-call token budget override (thread-safe; doesn't mutate agent state)
- `src/ingestion/extractor.py`: Added `_scale_tokens_for_passage(passage_len, configured_max)` — scales budget 25/50/75/100% for passages <400/800/2000/∞ chars, floor 1024 tokens
- Per-call scaled budget passed through `executor.submit()` so short passages don't burn GPU time on unused token headroom
- Fast-path dedup: before building context or running agents, check if all agent content hashes are already in `existing_hashes`; skip passage entirely if so (speeds up re-runs)
- Removed stale "Setup instructions" `<details>` block from dashboard Extract tab

#### Phase 7M — Orrick Metadata Enrichment + JSON Repair + Adaptive Token Retry + Agent Routing Optimization — DONE (2026-05-09)

**Orrick Metadata Enrichment (Phase 7M-A & M-B):**
- Created `src/ingestion/orrick_enrichment.py` with two-phase enrichment:
  - **Phase 1 (backfill)**: Combines split CSV columns `key_requirements_raw` + `enforcement_penalties` into single `orrick_summary` field; prevents data loss when extraction context builder only finds one column
  - **Phase 2 (LLM generation)**: For laws with no Orrick data, loads ingested law text, calls local LLM via `get_discovery_provider()` to produce structured summary, stores result to break auto-Tier-D confidence gating
- Module exports `run_orrick_enrichment(db, limit=None, llm_enabled=True, on_progress=None)` returning stats dict
- Integrated into `seed_pipeline.py` with `--mode enrich-orrick` and optional `--no-llm` flag
- Uses `_load_law_text()` with 12k-char budget, `_parse_llm_json()` with markdown fence stripping, `_build_orrick_summary()` for safe concatenation
- Updated `src/ingestion/local_ingest.py` to write combined `orrick_summary` at seed time (prevents Phase 1 re-runs)

**JSON Truncation Repair (Phase 7M-C):**
- Fixed `_repair_truncated_json()` Strategy 2 in `src/agents/base.py` to properly close unterminated strings before closing brackets
- Added `suffix = '"'` when `in_string=True` at end of scan; produces valid JSON like `{"key": "value"}` instead of malformed `{"key": "value}}`
- Idempotent: already-valid JSON passes through unchanged
- Fixed root cause of `threshold_exception` agent crashes with `Unterminated string starting at: line N column M` errors on truncated output

**Adaptive Token Retry on Truncation (Phase 7M-D):**
- Made `current_max_tokens` mutable in `extract()` loop in both `src/agents/base.py` and `src/ingestion/extractor.py`
- When `response.stop_reason == "length"` (token exhaustion), calculates `_doubled = min(_prev * 2, _cap)` and retries at higher budget
- Max retry is `self.max_retries` attempts; each retry doubles budget up to `settings.local_extraction_max_tokens` cap
- Semantic: model runs at dynamic scaled budget for short passages, escalates only on exhaustion (adaptive efficiency)
- Prevents perpetual timeout loops: hard cap prevents runaway escalation

**Extract 5 (Test) Button Data Loss Fix (Phase 7M-E):**
- Fixed auto-purge logic in `run_extraction()` in `src/ingestion/extractor.py` (lines 1634-1657)
- Changed from unconditional `db.execute(sa_delete(Extraction))` to gated `if limit is None:` block
- Semantic: full runs (unlimited) purge to reset state; test/triage runs (with limit) preserve previous results
- Root cause: user clicked "Extract 5 (Test)" and ALL previous extractions disappeared due to unconditional purge

**Agent Routing Optimization (Phase 7M-F):**
- Removed redundant unconditional `signaled.add()` calls in `_route_agents_by_signal()` in `src/ingestion/extractor.py`
  - Removed: `signaled.add("definition_actor")`
  - Removed: `signaled.add("obligation")`
- Both agents are in `_SIGNAL_MAP` with keyword patterns (`_DEFINITION_SIGNALS`, `_OBLIGATION_SIGNALS`); unconditional adds inflated call counts
- Verified remaining safety nets are legitimate:
  - `if not signaled: return None` — runs all agents when no signals match (recall safety for unusual phrasing)
  - `if len(signaled) >= len(all_agents) - 1: return None` — now only fires when 5+ of 6 agents genuinely signaled, not artificially inflated
- Expected impact: `definition_actor` call count drops from ~27 to ~5-8; overall pipeline time reduction ~20%
- Performance analysis confirmed: `threshold_exception` 43.9%, `definition_actor` 36.8% of agent time; redundancy was artificial doubling

**Files modified**: `src/ingestion/orrick_enrichment.py` (created), `src/ingestion/local_ingest.py`, `src/ingestion/extractor.py`, `src/agents/base.py`, `src/scripts/seed_pipeline.py`

#### Sequencing & Decision Gates
- 7A is independent, ship first.
- 7B is a prerequisite for 7C, 7D, 7E (do it once, three agents reuse it).
- 7C/7D/7E are independent of each other after 7B — can parallelize if desired.
- 7F and 7G are layered enhancements; defer until bill-level pattern is validated.
- 7H-7L completed as pre-flight fixes and efficiency work ahead of the first full extraction run.
- After each new agent ships, measure product-table population rate before proceeding to the next.

---

## Blocked Tasks
- **Cross-validation scoring** — Needs extraction to complete.
- **Phase 4** — Requires eval set (ANALYSIS-1).

## Questions / Clarifications Needed
- Sync to Policy Navigator: all types or approved-only?
- Is MinIO/S3 actually needed? Pipeline works without it.
- Who performs lawyer review for eval set (ANALYSIS-1)?

## Immediate Next Tasks (blocking Phase 1: Taxonomy)

1. ~~**BLOCKING**: Merge `claude/brave-lamport-d9zgjx`~~ — **done** (see SFH-2a). `bill_level_extractions` population confirmed separately via SFH-2b (169/169/169).
2. **DONE 2026-05-26** — Taxonomy doc drift reconciled. *(see completed_tasks.md)*
3. Verify claim that `subject_area` is "hardcoded to 'artificial_intelligence'" in Policy Navigator `fact_laws` (impacts Phase 1.A normalization table design).
4. **`alembic upgrade head`** — Apply migration `l8i4j0k2g713` (see Active Tasks).
5. **Sync local → Supabase** — Dashboard Step 5.
6. **Sync Regs Checker → Policy Navigator** — Dashboard Step 6.
7. **Run rollup matrix** — `python -m src.scripts.rollup_matrix`.

## Bugs / Issues

### BUG-1: Laws missing Orrick data → auto Tier D — ACCEPTED
Only 2 Orrick laws + 53 IAPP active bills lack Orrick data. The 53 IAPP bills are pending legislation — the Orrick gate legitimately flags them. Accept Tier D for these.

### BUG-2: Failed extraction retry — FIXED
### BUG-3: Supabase sync "not configured" — FIXED
### BUG-4: Unicode normalization in evidence spans — FIXED (Phase 1, 2026-04-05)

### BUG-7: NameError crash in `extract_single_record` — FIXED (2026-06-10, `claude/brave-lamport-d9zgjx`)
Three undefined-name regressions from the RR7g dedup refactor (`content_hash` renamed to `passage_text_hash`, stale `existing_hashes` block, `monitor` used before import). Would have crashed every passage on the next extraction run. Pending merge to main.

### BUG-8: LM Studio status endpoint crashes when LM Studio unreachable — FIXED (2026-06-10)
`get_models_status()` in `dashboard.py` referenced `settings` without importing it in the error branch. Fixed.

### BUG-5: Gemma 4 `<|channel>thought` HTTP 400 — KNOWN / WORKAROUND
LM Studio + Gemma 4 26B-A4B occasionally emits a structured thinking token that triggers a 400 error. Affects ~2 passages per run; they fall through as `uncertain` triage. Fix: update LM Studio when a Gemma-4-compatible release is available.

### BUG-6: Alembic migration `g3d9e5f7b208` DuplicateObject — FIXED (2026-05-09)
`triagedecision` / `triagemethod` enum types collided between manual `CREATE TYPE` and SQLAlchemy DDL. Fixed by removing manual blocks; SQLAlchemy owns enum creation via `sa.Enum(create_constraint=False)`.

---

## Engineering Session (2026-06-10) — COMPLETED ✓

Bug check (BUG-7/BUG-8 fixed), lint cleanup (RR2c partial), CI hard gate, repo
cleanup, Phase 4a confirmed, Phase 4b completed. All on `claude/brave-lamport-d9zgjx`
— **pending merge to main**. **Full breakdown: `completed_tasks.md` (2026-06-10 entry).**

## Engineering Session (2026-06-21) — Extraction validation pipeline improvements (Phases 1–4)

**Branch**: `claude/brave-lamport-d9zgjx`
**Scope**: Four phases from extraction validation report; all committed and pushed.

### Phase 1 — Artifact-aware span grounding ✅
- **`src/core/text_grounding.py`** (new): Standalone `verify_evidence_spans()` with 4-tier matching. Tier 4 strips PDF revisor artifacts (margin line-numbers `N.NN`, hyphenated line-breaks, `SECTIONA1` glyphs) before loose-match pass. Floor ≥ 25 chars.
- **`src/agents/base.py`**: `_verify_evidence_spans` delegates to `text_grounding.verify_evidence_spans`; private `_normalize_*` methods retained.
- **`src/scripts/reground_spans.py`** (new): Idempotent script — re-verifies stored spans against passage text, updates `evidence_spans` in DB (no LLM). Use `--dry-run` first.

### Phase 2 — Source quality gate ✅
- **`src/ingestion/local_ingest.py`**: Added `_STATUTORY_STRUCTURE_MARKERS` (AN ACT, SECTION, §, WHEREAS, Chapter…) and `_compute_fulltext_status()` returning `ok` / `too_short` / `capture_failed` / `no_statutory_structure`. `_check_source_quality()` now rejects files lacking statutory markers in first 4 KB. Status stamped into `IngestionJob.metadata_["fulltext_status"]`.

### Phase 3 — Duplicate canonical detection ✅
- **`src/core/citation_normalizer.py`**: Added `find_duplicate_canonicals()`, `_pick_preferred()`, `_preference_reason()`, `_normalize_bill_number()`. Groups canonical IDs by (jurisdiction, normalized bill_number) to surface pairs like `US-TX-HB149` + `TMP-TX-AITEXASRESPONS`.
- **`src/scripts/consolidate_duplicates.py`** (new): Reports duplicate pairs; with `--apply` re-points `document_versions.family_id` to the preferred family and removes the empty duplicate.

### Phase 4 — Grounding-based admission gate ✅
- **`src/core/admission.py`** (new): `compute_admission_status(evidence_spans, confidence_tier)` → `admitted` / `needs_review` / `excluded`. Admitted when ≥1 span `verified=True` OR tier A/B/C (tracker-confirmed). `needs_review` when zero verified + Tier D.
- **`src/scripts/compute_admissions.py`** (new): Stamps `metadata_["admission_status"]` on every extraction; idempotent. Run after `reground_spans.py`.
- **`src/api/routes/dashboard.py`**: Two new endpoints:
  - `GET /api/admitted/export.csv` — admitted extraction set as CSV
  - `GET /api/admitted/export.jsonl` — admitted extraction set as JSONL (one object/line with full context)

**Pending operator steps:**
1. `python -m src.scripts.reground_spans --dry-run` then `python -m src.scripts.reground_spans`
2. `python -m src.scripts.compute_admissions --dry-run` then apply
3. Check `/dashboard/api/admitted/export.jsonl` for accepted-set export
4. `python -m src.scripts.consolidate_duplicates` for duplicate canonical report

---

## Engineering Session (2026-06-15) — NVIDIA backend extraction hardening — COMPLETED ✓

First full-corpus run on the **NVIDIA cloud backend** (gpt-oss-120b for clause/bill
agents, llama-3.1-8b for triage/definition_actor/preemption) instead of local LM
Studio. Triage finished (805 relevant / 154 uncertain / 265 not-relevant of 1224).
Extraction shook out a series of provider-specific reliability bugs, all fixed on
`claude/brave-lamport-d9zgjx`:

- ✅ **NVIDIA 429 retry** — `NvidiaLLMProvider.call()` retries rate-limit responses
  with exponential backoff (1/2/4/8/16 s, 5 attempts). Triage was getting hammered
  with `nvidia_quota_exhausted` and silently passing affected passages through.
- ✅ **NVIDIA transport-error retry** — the dominant extraction failure ("Server
  disconnected without sending a response", ~50 passages over an 11 h run) was an
  `httpx.RemoteProtocolError` escaping the retry loop (only 429 was retried). Now
  catches `httpx.TransportError` (covers RemoteProtocolError / ReadTimeout /
  ConnectError) with the same backoff.
- ✅ **reasoning_effort coercion** — NVIDIA's gpt-oss-120b only accepts
  `low`/`medium`/`high` and 400s on `off` (which the local config uses). Provider now
  coerces `off`/`none`/`disabled`/`minimal` → `low`. Config set to `low` for
  rights_protection + the 3 bill-level agents (binary/structured tasks that don't
  need full reasoning); obligation/threshold_exception/compliance_mechanism keep
  default reasoning.
- ✅ **Bare-array extraction output** — when the 8B model returns a top-level JSON
  array instead of `{"extractions": [...]}`, `parsed.get()` threw `'list' object has
  no attribute 'get'`. Now normalized to the envelope shape after `json.loads`.
- ✅ **definition_actor budget** — NVIDIA max_tokens 2048 → 4096; definition-heavy
  passages (omnibus privacy bills) were hitting `finish_reason=length` and forcing a
  wasteful truncation-retry on every pass.
- ✅ **Punctuation-insensitive evidence spans** — added a third match tier in
  `_verify_evidence_spans` (lowercase-alphanumeric + collapsed whitespace, with an
  index map back to char offsets). Catches models that re-case/re-punctuate quotes
  (notably the 8B definition_actor on ALL-CAPS statutes) without verifying
  hallucinated text — words must still appear contiguously. Gated to spans ≥ 15 chars.
- ✅ **Re-triage Failed** — new button + `run_retry_failed_triage()`: deletes
  `method=passthrough` + `llm_error` triage rows and re-runs triage for just those
  passages (mirrors the extraction Retry-Failed flow; previously those rows were
  permanently stuck because run_triage skips already-triaged passages).
- ✅ **Archiver ConfidenceTier case bug** — `_export_low_confidence` used
  `ConfidenceTier.c/.d` (enum values are uppercase `C`/`D`), raising
  `AttributeError("c")` that silently dropped the low-confidence review CSV. Fixed.
- ✅ **Lint hard-gate green** — fixed F821 (`SectionTriageResult` used without import
  in the new failed-triage-count endpoint — was silently 500ing the badge), F841
  (unused `existing`), F401 (unused `Callable`). CI Ruff-lint job back to green;
  Unit-tests job already green (855 passed).

**Pending merge to main** along with the 2026-06-10 work.

## Upcoming: dashboard.py split (deferred until after extraction run validates)

`src/api/routes/dashboard.py` is 5,400+ lines. Natural split into 6 modules:
1. `pipeline_ops.py` — seed/fetch/triage/extract/sync (`/api/run/*`)
2. `monitoring.py` — stats, progress, monitor, events, latency, health
3. `documents.py` — list docs, edit metadata, upload, completeness
4. `exports.py` — all CSV/JSONL export endpoints
5. `concepts.py` — concepts page + concept API (already self-contained)
6. `models.py` — models page + model config API (already self-contained)
Shared helpers (HTML builders, `_run_in_background`) move to `dashboard_utils.py`.
