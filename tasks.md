# Regs Checker — Tasks

## Active Tasks

- **Phase 6 — Full reset + re-seed + ingest + triage + extract + sync (READY TO EXECUTE)**
  - Pre-flight done: smart routing, title disambiguation, regulatory_category, 4 URL swaps, MN omnibus trim, ambiguity agent retired, Unicode fix, keyword expansion, confidence improvements, **model config UI**.
  - Model assignments configurable at runtime via `/dashboard/models` (no code changes needed to swap models).
  - User runs: `python scripts/reset_pipeline.py`, then dashboard Steps 1→2→3→4.5→5 (`--clear`).
  - 16 laws with still-quarantined source text will be skipped on re-ingest (see `output/law_texts_quarantine/NEEDED_SOURCES.md`).
  - Step 3 uses **6 agents** (ambiguity retired — findings embedded as `interpretation_risks` on obligation/rights payloads)

- **Obtain correct source text for 16 quarantined laws** — See `output/law_texts_quarantine/NEEDED_SOURCES.md`. Place correct bill text in `output/law_texts/<canonical_law_id>.txt`.

- **TN quarantine files contain TX bill content** — TX SB 1188, SB 2373, SB 815, SB 20, SB 1621 may be legitimate TX AI laws. Decide whether to add as new TX entries in `fact_laws.csv`.

- **Merge feature branch to main** — All work on `claude/onboard-government-project-3bq7i`. Needs review and merge after Phase 6 validation.

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

#### Phase 7A — Enforcement Context Injection (Quick Win, ~2h)
Stopgap before bill-level agents land. Inject bill enforcement/penalty sections into the existing
obligation agent's context block, alongside `bill_definitions` and `bill_scope`.
- Add `bill_enforcement` collector to `src/core/bill_context.py` — keyword match on "penalty",
  "fine", "civil action", "enforcement", "violation", "liable for"
- Extend `_append_bill_context` in `src/agents/base.py` to inject the new block
- Re-run obligation agent on existing passages; measure non-null rate of `enforcement.max_civil_penalty_usd`
- **Decision gate**: if non-null rate jumps meaningfully, deprioritize Phase 7C; if not, proceed

#### Phase 7B — Bill-Level Agent Infrastructure (~1-2d)
Foundation for Phase 7C–E. No user-visible output yet.
- New orchestration pattern: post-passage pass that runs bill-level agents per `document_version`
- Add `BillLevelAgent` base class (parallel to `BaseExtractionAgent`) — input is full law text +
  per-passage extraction results, output is one `BillLevelExtraction` row
- New `bill_level_extractions` table or extend `extractions` with `scope='bill'`
- Add new `ExtractionType` enum values: `enforcement_summary`, `applicability_summary`, `compliance_timeline`
- Monitor + dashboard support for bill-level agent runs

#### Phase 7C — Enforcement Agent (~1-2d)
Bill-level agent producing one structured enforcement record per law.
- Output schema: `max_civil_penalty_usd`, `penalty_per` (violation/day/occurrence),
  `cure_period_days`, `enforcing_body`, `private_right_of_action` (bool), `criminal_penalties` (bool),
  `enforcement_text` (evidence)
- Maps directly to `law_enforcement_details`
- Backfill all ~190 laws (≈3-6h at Gemma speeds)

#### Phase 7D — Applicability Agent (~1-2d)
Bill-level agent producing one structured applicability record per law.
- Output schema: `covered_entity_types`, `covered_sectors`, `ai_system_types_in_scope`,
  `size_thresholds` (revenue/employees/data volume), `geographic_scope`, `key_exemptions`,
  `government_only` (bool — resolves the currently-manual field)
- Maps to `law_triggering_thresholds`, feeds `anonymous_audit_profiles` matching engine
- Backfill all laws

#### Phase 7E — Compliance Timeline Agent (~1d)
Bill-level agent for deadlines and frequencies.
- Output schema: `law_effective_date`, `enforcement_start_date`, `key_deadlines[]` (action,
  deadline_type, relative_days, frequency_months), `cure_period_days`, `sunset_date`
- Maps to `law_obligation_flags.impact_assessment_frequency_months`,
  `law_enforcement_details.cure_period_days`, LawCard deadline view
- Backfill all laws

#### Phase 7F — Threshold Agent Restructure (~2-3d, lower priority)
Split flat `threshold_type` output into three sub-types with distinct schemas:
- **Scope thresholds** → `law_triggering_thresholds` (revenue, employees, processing volume, FLOPS)
- **Temporal thresholds** → Phase 7E timeline (deadlines, frequencies, response windows)
- **Exemptions** → `law_triggering_thresholds.exemptions` (carve-outs, safe harbors, excluded entities)
- Migration path required for existing 28 rows in `law_triggering_thresholds`

#### Phase 7G — Safe Harbor + Missing Data Types (~3-5d, lowest priority)
Layer on after Phase 7C–E prove out.
- **Safe Harbor**: boolean flag + framework reference (NIST AI RMF, etc.) + conditions; either as
  obligation field or compliance_mechanism sub-type. Feeds `resp_*` framework tables.
- **Protected subjects** on rights_protection: consumers, employees, candidates, students, patients, minors
- **Notification/consent**: `consent_type` (opt-in/opt-out/notice), `timing`, `method`
- **Data retention**: `retention_period_months`, `retention_subject`
- **Incident reporting**: `ag_incident_reporting_hours` (already exists, currently empty)
- **Cross-law references**: structured links instead of ambiguity findings; feeds `law_scope_exclusions`
  and `jurisdictional_conflicts`

#### Sequencing & Decision Gates
- 7A is independent, ship first.
- 7B is a prerequisite for 7C, 7D, 7E (do it once, three agents reuse it).
- 7C/7D/7E are independent of each other after 7B — can parallelize if desired.
- 7F and 7G are layered enhancements; defer until bill-level pattern is validated.
- After each new agent ships, measure product-table population rate before proceeding to the next.

---

## Blocked Tasks
- **Cross-validation scoring** — Needs extraction to complete.
- **Phase 4** — Requires eval set (ANALYSIS-1).

## Questions / Clarifications Needed
- Sync to Policy Navigator: all types or approved-only?
- Is MinIO/S3 actually needed? Pipeline works without it.
- Who performs lawyer review for eval set (ANALYSIS-1)?

## Next Tasks (after extraction completes)

- **Sync local → Supabase** — Dashboard Step 5.
- **Sync Regs Checker → Policy Navigator** — Dashboard Step 6.
- **Run rollup matrix** — `python -m src.scripts.rollup_matrix`
- **Review test coverage** — 450 pass, 7 fail (pre-existing). 4 stale import files.

## Bugs / Issues

### BUG-1: Laws missing Orrick data → auto Tier D — ACCEPTED
Only 2 Orrick laws + 53 IAPP active bills lack Orrick data. The 53 IAPP bills are pending legislation — the Orrick gate legitimately flags them. Accept Tier D for these.

### BUG-2: Failed extraction retry — FIXED
### BUG-3: Supabase sync "not configured" — FIXED
### BUG-4: Unicode normalization in evidence spans — FIXED (Phase 1, 2026-04-05)
