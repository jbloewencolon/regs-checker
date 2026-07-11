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

### Phase SFH-1 — Make failure visible (deterministic, unit-testable; ⏳ awaiting operator go)
- ⏳ **SFH-1a** **[High]** SF-04 loop-truncation bypass: treat `stop_reason in
  ('length','loop')` as truncated at both consult sites (`base.py:358` truncation
  flag; `base.py:~481` retry-with-doubled-budget condition); record `stop_reason`
  in extraction metadata; count loops per agent in `agent_stats.json`. Closes the
  one truncation path that today sails through with full confidence eligibility. *(BE)*
- ⏳ **SFH-1b** **[High]** SF-06 passage-conservation check: run-end invariant
  `selected == extracted + abstained + failed + skipped_boilerplate + skipped_dedup`,
  each term emitted in `run_summary.json`, hard alert with residual ids (set
  difference) on any mismatch. Kills the 660-vs-647 class of silent loss. *(BE)*
- ⏳ **SFH-1c** **[High]** SF-03 sync-skip persistence: `sync_skips` table
  (extraction_id, doc_family_id, reason, run_ts; Alembic migration) + persist on
  every bridge-miss in both legs + `--resync-skips` replay mode + alert naming the
  unmapped families. Today the id cursor advances past unmapped rows forever. *(BE)*
- ⏳ **SFH-1d** **[Medium]** SF-02 routing recall delta: tag sampled passages
  (`routing_bypassed=true` in metadata), compute at run end which extractions came
  from agents routing would have skipped, emit delta + false-narrowing rate in
  `run_summary.json`, alert over threshold. The 5% sampling cost currently buys
  zero monitoring value. *(BE)*
- ⏳ **SFH-1e** **[Medium]** SF-05 salvage accounting: count array elements
  pre/post `_repair_truncated_json`, store `items_dropped_by_repair` in extraction
  metadata, aggregate per-strategy repair hits into `run_summary.json`, alert when
  run repair rate exceeds ~3%. *(BE)*
- ⏳ **SFH-1f** **[High]** SF-08 remainder (quarantine approved): Pydantic-validate
  tracker metadata keys at read time (fail loud — kills the
  'enforcement'-vs-'enforcement_penalties' drift class); make the scoring path
  honor the existing `orrick_source='llm_generated'` stamp — generated summaries
  score as tracker-absent (triage keyword seeding only); per-run counts of laws
  scored against generated vs. real tracker data. **Known consequence, accepted:**
  enrich-orrick-only laws drop to the gated/capped path until SFH-3. *(BE, NLP)*
- ⏳ **SFH-1g** **[Medium]** SF-09 sync observability: `sync_runs` row per
  invocation (leg, started, finished, synced, skipped, updated, error) + freshness
  check in `sync_monitor.py` (alert when newest `synced_at` exceeds cadence, or a
  run syncs 0 with pending cursor rows). *(BE)*
- ⏳ **SFH-1h** **[Medium]** SF-10 reparse lineage guard: within-version re-parse
  requires explicit `--force-reparse` (logs count of extraction rows orphaned);
  never delete across versions — text change ⇒ new `DocumentVersion` with
  `predecessor_id`. **Prerequisite for the entire SFH-4 live-data phase.** *(BE)*
- ⏳ **SFH-1i** **[Low]** SF-11 + B8 meta-monitoring: count triage-warning write
  failures (the `except Exception: pass` at `section_triage.py:67`) and
  summary-generation failures in `run_summary` — never raise, never invisible. *(BE)*
- ⏳ **SFH-1j** **[Medium]** B5 remainder: extend the EA5-3 input-targeting pattern
  (pattern-located sections + bounded tail, no raw head-truncation bias) from
  enforcement_agent to **applicability_agent + compliance_timeline_agent** —
  deadlines and applicability clauses also live in bill tails. Same
  strictly-better-input class EA5-3 landed under. *(NLP)*
- ⏳ **SFH-1k** **[Low]** B9 schema-drift guard: startup assertion that the sync
  INSERT column list matches `synced_extractions` information_schema (five lines
  that would have caught the months-long "INSERT never succeeded" episode). *(BE)*
- ⏳ **SFH-1l** **[Medium]** B10 process: one-passage end-to-end CI smoke test
  (triage → routing → one agent with stubbed provider → persistence) so wiring
  errors fail CI; fix README vs `architecture.md` provider drift
  (`config/agent_models.json` is authoritative). *(BE, DevOps)*
- ⏳ **SFH-1m** **[Medium]** EA4-1 config flip (audit B7 concurs): move
  `cross_validation`/`gap_detection` from `openai/gpt-oss-20b` to a
  different-lineage model ≥ extractor capability (e.g. `meta/llama-3.1-70b-instruct`)
  in `config/agent_models.json`; catch-rate measurement on seeded-error fixtures
  stays with the operator (needs live LLM). *(NLP)*
- ⏳ **SFH-1n** **[Low]** Triage determinism (approved): nvidia triage
  `temperature 0.2 → 0`, `top_p → null` in `config/agent_models.json` — variance
  reduction on a binary gate. (Routing threshold explicitly NOT changed — see
  operator decision 3.) *(NLP)*

### Phase SFH-2 — Operator actions (not buildable in this sandbox)
- ⏳ **SFH-2a** — merge `claude/brave-lamport-d9zgjx` → main (tasks.md top item;
  audit P0 concurs: three NameError crash fixes sit unmerged while CI is green).
- ✅ **SFH-2b** — applicability confirm query: **done 2026-07-06 via Supabase MCP**
  (169/169/169 — no backfill; see Phase 1a).
- ⏳ **SFH-2c** — rule on eval-set size: audit says 20–30 laws; EA amendment #4
  deliberately right-sized to 8–10 for solo annotation capacity. Decide before
  EA1-1 annotation starts (the audit's number is not silently adopted).

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
(1a/1b/1c/1f first) once the operator gives the go. SFH-2a/2c are operator
week-one actions. SFH-3 stays behind EA1 — which both the audit ("the single
highest-leverage investment") and the EA plan agree is the true long pole.
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
