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
> **⚠️ Open contradiction:** v3 says applicability "not run"; my C-1 analysis saw 472
> bill-level rows. Reconciles if 472 = enforcement+timeline only. **Settle with
> `GROUP BY agent_name` before Phase 1a.** Plan §3.

### Phase 1 — Foundation: trustworthy, measurable, non-destructive runs (now)
- ✅ Model pin — NVIDIA primary: `openai/gpt-oss-120b` (heavy agents) + `meta/llama-3.1-8b-instruct` (triage/definition_actor/preemption); local Gemma fallback retained in `config/agent_models.json`. 6+3 agents.
- ⏳ **1a** — confirm `applicability_agent` row count (`GROUP BY agent_name`); if 0, run applicability across all 232. C-1 export fix is the prerequisite. *(NLP, DevOps)* **Operator query: `SELECT agent_name, COUNT(*) FROM bill_level_extractions GROUP BY agent_name;`**
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
1. **Merge `claude/brave-lamport-d9zgjx` → main** — contains 3 NameError fixes that crash extraction. Must merge before next run.
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

- ⏳ **P3-1** — `sync_extractions.py`: drop `review_status = 'approved'` from all three
  queries in `sync_extractions()` (pending count, dry-run bridged count, main fetch).
  Keep the existing `confidence_tier::text = ANY(:tiers)` filter — `_eligible_tiers()`
  already excludes D by default (`confidence_publish_min_tier = "C"` in
  `src/core/config.py`). Update the module docstring (currently documents the P2-1
  approved-only gate) and inline comments.
- ⏳ **P3-2** — Policy Navigator live migration: `CREATE OR REPLACE VIEW
  rollup_eligible_extractions` to drop its `review_status IN ('approved','verified')`
  condition (added in P2-3, migration `p2_3_rollup_eligible_extractions_view`) so it
  becomes a pass-through of `synced_extractions` (or is retired in favor of querying
  `synced_extractions` directly — decide during implementation). Tier filtering
  continues to live in Python in `rollup_matrix.py`, unchanged. Verify against a
  scratch Postgres schema before applying live, per the P2 pattern.
- ⏳ **P3-3** — `sync_updates()` in `sync_extractions.py`: change `is_eligible` from
  `review_status == 'approved' and tier in eligible_tiers` to tier-only. Update the
  function's docstring, which currently documents the "RC leads, PN backs up" review-
  gated design from P2-6.
- ⏳ **P3-4** — Dashboard: new panel/route for **Tier-D extractions** (permanently
  ineligible under the tier-only gate) so analysts have a queue of what still needs
  re-extraction or prompt/model tuning to reach C+. Mirror the existing
  `/api/low-confidence/export.csv` pattern in `src/api/routes/dashboard.py`.
- ⏳ **P3-5** — Dashboard: new **audit panel** listing `synced_extractions` rows in
  Policy Navigator whose `review_status` is not `approved`/`verified` — i.e., rows now
  live in the product without RC human sign-off. This is the visibility backstop for
  removing the P2 review gate; without it there's no way to see what shipped
  unreviewed.
- ⏳ **P3-6** — Tests: prove pending/flagged/rejected-status extractions at tier A/B/C
  now sync (regression against the old P2-1 behavior), and tier-D never syncs
  regardless of review_status. Cover both `sync_extractions()` and `sync_updates()`.
- ⏳ **P3-7** — `docs/phase3_completion_log.md` (new) + a forward-pointing addendum on
  `docs/remediation_plan.md`'s Phase 2 section noting the gate was relaxed in Phase 3.
  Apply the live PN migration via `apply_migration`, re-run the Supabase advisor scan.

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
- ⏳ **EA1-1** **[Critical]** Gold set expansion: 33 fixtures / ~3 statutes (one
  vetoed) → stratified set of **12–15 laws**: ≥2 OCR-quality PDFs, ≥1
  amendment-markup (engrossed) bill, ≥1 deepfake/likeness law, ≥1 tracker-silent
  law, per-agent expected extractions for **all 6 clause agents**. Annotation by
  RPR with double-annotation on 20% for agreement measurement. *(RPR, NLP)*
- ⏳ **EA1-2** **[Critical]** Harness covers all 9 agents: `harness.py` imports only
  obligation/definition_actor/threshold_exception — rights_protection,
  compliance_mechanism, preemption + all 3 bill-level agents have **zero**
  ground-truth eval. Add bill-level eval mode (whole-bill fixture → expected
  `law_enforcement_details`/thresholds/timeline fields). *(NLP, BE)*
- ⏳ **EA1-3** **[High]** Baseline + regression gate: run harness on current prompts/
  models, commit per-agent per-field P/R/F1 baseline artifact; every prompt/model/
  weight PR reruns and diffs against baseline. Numerics scored exact-match;
  text fields scored by span overlap. *(NLP, DevOps)*
- ⏳ **EA1-4** **[High]** Amendment-markup corpus audit: parser has **no**
  strikethrough/insertion handling — extracting obligations from stricken text is
  a live severe-failure risk. Audit `output/law_sources/` for engrossed-style
  bills; report count + examples. (Fix gated as EA2-4.) *(NLP)*

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
- 🔒 **EA2-4** **[High]** Parser strikethrough handling (gated on EA1-4 audit):
  strip stricken text / retain inserted text for engrossed bills before
  segmentation; add parse-quality flag `amendment_markup_detected`. *(NLP, BE)*

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
- ⏳ **EA5-2** **[High]** Reconciliation as verification: `enforcement_normalizer`
  merges clause-level + bill-level + trackers by precedence but never **flags
  disagreement**. Emit `enforcement_conflict` review items when sources disagree
  on penalty/PRoA/cure-period (the redundancy already exists; exploit it).
  *(NLP, BE)*
- ⏳ **EA5-3** **[Medium]** Enforcement-agent input targeting: feed
  enforcement-pattern sections from `bill_context.py` (+ bill tail) instead of the
  raw 128k-char prefix, closing the end-of-bill truncation bias (EA0-4 flags it;
  this fixes it). *(NLP)*
- ⏳ **EA5-4** **[Medium]** Penalty-range structure: "if a range is given, use the
  maximum" collapses legally distinct tiers (negligent vs willful). Add optional
  `penalty_tiers` array {condition, amount_usd}; keep max for the matrix column.
  *(NLP, RPR)*

### Phase EA6 — Prompt & schema legal-nuance fixes (gated on EA1 regression gate)
- 🔒 **EA6-1** **[High]** Implied rights defensibility: `rights_protection.yml:77`
  manufactures rights from obligations ("notice obligation implies notice right") —
  a contested legal inference stored at equal status with textual rights. Add
  `derivation: textual | implied_from_obligation` to `RightsProtectionPayload`;
  implied rows visibly badged in review + product. *(RPR ruling, NLP)*
- 🔒 **EA6-2** **[Medium]** Constrained decoding: NVIDIA NIM structured outputs
  (JSON schema) for clause agents; shrinks the 5-strategy `_repair_json` surface
  (repair chain retained as fallback for local provider). *(NLP, BE)*
- 🔒 **EA6-3** **[Low]** Dedupe `interpretation_risks` across obligation/rights
  agents on the same passage (same term+risk_type). *(BE)*
- 🔒 **EA6-4** **[Low]** CV prompt trim: stop re-serializing evidence_spans +
  metadata into the CV payload dump (CV already has the passage). Token savings
  with zero signal loss. *(NLP)*
- 🔒 **EA6-5** **[Medium]** Date parse status: `TimelineInfo` validator silently
  passes unparseable dates through (`normalize_date(v) or v`) → ISO and free text
  mixed in one column. Store raw + normalized + `date_parse_status`; unparsed
  dates excluded from deadline computations. *(BE)*

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
6. **Session note (2026-07-03):** all five EA0 items landed this session —
   code fixes + 36 new regression tests (907/907 unit tests passing), no
   live LLM required since all five are pure-logic/config defects. **EA1
   (gold-set baseline capture) could NOT be run this session** — this
   execution environment has neither `NVIDIA_API_KEY` nor a reachable local
   LM Studio instance, and the harness (`src/evaluation/harness.py`) calls
   real model providers. EA1-3-lite (run the existing 33 fixtures, commit
   baseline scores) is the next unblocked step and must run on the
   operator's machine via `python start.py` per CLAUDE.md. EA2 (deterministic
   numeric/span grounding) is pure code + unit tests and can proceed in this
   kind of environment without waiting on that baseline.

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
- ⏳ **RR3d** **[Medium]** Review `/dashboard` + `/internal` auth posture; document
  the intended deployment trust boundary (currently a localhost analyst tool). *(BE)*

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

1. **BLOCKING**: Merge `claude/brave-lamport-d9zgjx` + run extraction to populate `bill_level_extractions`. See Active Tasks above.
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
