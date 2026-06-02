# Regs Checker — Tasks

## Active Tasks

- **Phase 6 — Full reset + re-seed + ingest + triage + extract + sync (IN PROGRESS)**
  - **Phase 6 Steps 1–2 complete**: Reset pipeline, re-seed from CSV, re-ingest, run triage
  - **Phase 6 Step 2 status**: Triage run started; ~19 passages failed due to Gemma token exhaustion (FIXED in Phase 8 below).
  - **Phase 7M complete**: Orrick enrichment, JSON repair, adaptive retry, Extract 5 fix, agent routing optimization all committed to `claude/onboard-government-project-3bq7i`
  - **Phase 8 complete** (2026-05-10): Export endpoints bug, Gemma token doubling, channel-thought recovery, tab-key JSON repair, low-confidence persistence all committed to `claude/onboard-government-project-PyyB9`
  - **Next: selective triage reset** on the ~19 failed passages (use "Reset Triage" on Triage page, then re-run triage).
  - **Next: run `alembic upgrade head`** to apply migration `l8i4j0k2g713` (adds `duration_ms`, `input_tokens`, `output_tokens` to `extractions` table).
  - After triage is clean: proceed to extraction (Step 3), then sync (Steps 5→6).
  - 16 laws with still-quarantined source text will be skipped on re-ingest (see `output/law_texts_quarantine/NEEDED_SOURCES.md`).
  - Step 3 uses **6 agents** (ambiguity retired) + **3 bill-level agents** (enforcement, applicability, compliance_timeline).
  - Model: `google/gemma-4-26b-a4b` — all agents configured in `config/agent_models.json` with pre-doubling token budgets.

- **Apply pending Alembic migration** — Run `alembic upgrade head` to add `duration_ms` / `input_tokens` / `output_tokens` columns to the `extractions` table (migration `l8i4j0k2g713`).

- **Selective triage reset** — Re-triage ~19 passages that failed with `finish_reason=length` (Gemma token exhaustion, now fixed). Use Triage page → Reset Failed → re-run triage.

- **Obtain correct source text for 16 quarantined laws** — See `output/law_texts_quarantine/NEEDED_SOURCES.md`. Place correct bill text in `output/law_texts/<canonical_law_id>.txt`.

- **TN quarantine files contain TX bill content** — TX SB 1188, SB 2373, SB 815, SB 20, SB 1621 may be legitimate TX AI laws. Decide whether to add as new TX entries in `fact_laws.csv`.

- **Merge feature branches to main** — Phase 7M work on `claude/onboard-government-project-3bq7i` and Phase 8 work on `claude/onboard-government-project-PyyB9`. Needs review and merge after Phase 6 validation.

---

### Phase 8 — Export Bugs + Gemma Model Fixes + Low-Confidence Persistence (COMPLETED ✓)

**Status**: All sub-fixes applied and tested. 16,322 rows synced successfully. README.md and architecture.md reconciled with current pipeline.

**Sub-fixes:**
- ✓ Fixed export endpoints: rewind buffer for CSV/JSON flagger downloader
- ✓ Implemented persistent low-confidence storage: `_active/<extraction_run>/low_confidence_extractions/` with RunArchiver
- ✓ Fixed Supabase sync failures:
  - Raw_artifacts 409: Added TABLE_CONFLICT_COLUMNS dict + ?on_conflict query param to PostgREST
  - Extractions 400: Added 3 missing columns (duration_ms, input_tokens, output_tokens)
  - Bill_level_extractions: Created table with (document_version_id, agent_name) unique constraint
  - Failed_extraction_attempts: Created table with retry tracking
- ✓ Updated README: Agent list (6 passage + 3 bill-level), Gemma 4-26b model, token doubling docs, bill-level table, LocalLLMProvider section, low_confidence_extractions files
- ✓ Reconciled architecture.md: Section 3 rewritten (signal-based routing, bill-level agents, ambiguity retirement, enforcement context injection)

**Technical Detail** (see completed_tasks.md for comprehensive breakdown):

#### Phase 8A: Export Endpoints Bug Fix — DONE
- **Root cause**: Dashboard low-confidence export endpoints (`/api/low-confidence/export.csv` and `/api/low-confidence/export.jsonl`) referenced non-existent `dv.document_family` relationship
- **Fix**: Changed to correct relationship name `dv.family`; added null guards before accessing `.source`, `.canonical_title`, `.metadata_`
- **Files modified**: `src/api/routes/dashboard.py` (lines 2222-2223, 2298-2312)

#### Phase 8B: Gemma Token Doubling + reasoning_effort Caching — DONE
- **Root cause**: `config/agent_models.json` had `reasoning_effort: "off"` for all agents. Gemma rejects this parameter (HTTP 400); on retry the token doubling logic had already decided NOT to double. Result: Gemma ran full thinking mode with no JSON budget (~50% empty response errors).
- **Fix**: Removed `reasoning_effort: "off"` from configs; restored pre-doubling values. Added `_reasoning_effort_unsupported: set[str]` cache to LocalLLMProvider.
- **Files modified**: `config/agent_models.json`, `src/core/llm_provider.py` (lines 239, 256-265, 359)

#### Phase 8C: Channel-Thought Recovery — DONE
- **Root cause**: Gemma 4 emits `<|channel>thought` tokens that LM Studio can't tokenize (HTTP 400); actual JSON appears in error body
- **Fix**: Added recovery logic in LocalLLMProvider.call() (lines 273-304) to extract JSON from error body, validate, and return
- **Files modified**: `src/core/llm_provider.py` (lines 273-304)

#### Phase 8D: JSON Key Whitespace Stripping — DONE
- **Root cause**: Some models emit tab-prefixed JSON keys like `"\tterm"` instead of `"term"`
- **Fix**: Added recursive `_strip_keys()` helper in `_repair_json()` (lines 699-710) that strips leading/trailing whitespace from all dict keys
- **Files modified**: `src/agents/base.py` (lines 699-710)

#### Phase 8E: Low-Confidence Persistence to Disk — DONE
- **Root cause**: Export CSV/JSONL disappeared after extraction reset (app reset)
- **Solution**: Added `_export_low_confidence()` method to RunArchiver that writes persistent files at end of every run:
  - `output/extraction_runs/active/low_confidence_extractions.csv` (12 columns, Tier C/D only, ordered by confidence_score ascending)
  - `output/extraction_runs/active/low_confidence_extractions.jsonl` (one JSON object per line with full payload)
- **Persistence**: Files survive resets (archived to timestamped folder with active folder preserved)
- **Called from**: `finalize()` method after `_export_extractions()` and before `_export_agent_stats()`
- **Files modified**: `src/core/run_archiver.py` (added ~127 lines)

**Documentation Updates** (2026-05-23):

#### README.md Reconciliation
- Completely rewrote agent section: corrected all agents to google/gemma-4-26b-a4b (was claiming Qwen/GPT-OSS 20B)
- Changed 7-agent list to 6 passage-level + 3 bill-level agents
- Added LocalLLMProvider section explaining token doubling, channel-thought recovery, loop detection, reasoning_effort caching
- Added bill-level agents table with output tables
- Removed MinIO as required; noted local:// path support
- Updated run archiver section with low_confidence_extractions files

#### architecture.md Reconciliation (Section 3)
- Completely rewrote Extraction section: 7 agents → 6 passage-level + 3 bill-level agents
- Documented signal-based routing with fallback behavior
- Documented ambiguity agent retirement (Phase 1B) with archive path
- Explained interpretation_risks embedding on obligation/rights payloads
- Added bill enforcement context injection from src/core/bill_context.py
- Added bill-level agents table with output tables and frequency
- Documented per-extraction processing (Unicode normalization, Orrick similarity, confidence scoring, adaptive retry, failed_extraction_attempts)
- Updated Key Dependencies (Gemma 4-26b, removed MinIO requirement)
- Updated Test Infrastructure gap list (6 + 3 agent pipeline)

#### Supabase Sync Script Fix (src/scripts/sync_to_supabase.py)
- Added SYNC_TABLES entries: bill_level_extractions, failed_extraction_attempts (correct FK order)
- New TABLE_CONFLICT_COLUMNS dict (5 entries): raw_artifacts, normalized_source_records, section_triage_results, review_queue, bill_level_extractions
- Modified _supabase_post() to pass ?on_conflict query param when table in TABLE_CONFLICT_COLUMNS
- Clarified clear_supabase_tables() docstring (id=gte.0 filter works on all current serial integer PK tables)

**Files committed**: All work committed to branch `claude/onboard-government-project-PyyB9`; 4 extraction sub-fixes + 2 file edits for sync script + comprehensive documentation rewrites.

**Impact**: 
- Next extraction run should see empty response errors drop significantly
- HTTP 400 channel-thought errors successfully recovered
- Tab-key JSON errors fixed
- Low-confidence extractions persisted to disk in `output/extraction_runs/active/`, surviving resets
- Documentation now matches actual 6 + 3 agent pipeline

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

1. **BLOCKING**: Run extraction to populate bill_level_extractions (prerequisite for Phases 1.H, 2.A, 2.C, 2.D)
   - Current: table structure exists but empty
   - Unblocks: Phase 1.H (preemption_status), Phase 2.A (covered_sectors), Phase 2.C (harm_categories), Phase 2.D (obligation Level-2)
   - Dashboard Step 3 ("Extract All"); monitor Live Extraction Monitor widget
   - Expected improvements from Phase 8 fixes:
     - Empty response errors drop significantly with token doubling restored
     - Channel-thought HTTP 400 errors successfully recovered
     - Token scaling and agent routing optimization improve pipeline speed
   
2. **DONE 2026-05-26 — Taxonomy doc drift reconciled.** Planning docs now in `docs/taxonomy_strategy_summary.md` and `docs/taxonomy_dev_plan.md`. Verified-real drift fixed:
   - Law count: authoritative count is **232** (matches `data/fact_laws.csv` data rows). Strategy + dev plan updated.
   - Anthropic SDK refs (dev plan §5.3 Track 3.F): replaced with local Gemma / LM Studio reality.
   - Agent version tracking: `bill_level_extractions` has no `agent_version` column; agreed to use `agent_name` suffix convention (e.g. `applicability_agent_v2`). Dev plan §5.3 + §5.7 + §8 cross-phase risks updated.
   - `data/lookups/` directory: confirmed missing; Phase 1 Track 1.C now creates it as the first action.
   - `--mode recover`: **drift claim was wrong** — flag exists at `src/scripts/seed_pipeline.py:521,665`. Dev plan reference is correct.
   - "7 agents" references: **not present** in these two planning docs. Drift, if any, lives in older handoffs / older README sections; flag for a separate pass if needed.
   - Phase 1 success gates (strategy §6) had cross-phase contamination (`dim_actor_types` join + matching-engine deltas were Phase 2 work); rewritten to be Phase-1-internal, with a new "Phase 2 success gates" section added.
   - Phase 1 Track 1.H given an explicit "blocked until extraction populates `bill_level_extractions`" note.
   
3. Verify claim that subject_area is "hardcoded to 'artificial_intelligence'" in Policy Navigator fact_laws (impacts Phase 1.A normalization table design)
4. **`alembic upgrade head`** — Apply migration `l8i4j0k2g713` to add `duration_ms`, `input_tokens`, `output_tokens` columns to `extractions` table (if not already applied).
5. **Sync local → Supabase** — Dashboard Step 5
6. **Sync Regs Checker → Policy Navigator** — Dashboard Step 6
7. **Run rollup matrix** — `python -m src.scripts.rollup_matrix`

## Bugs / Issues

### BUG-1: Laws missing Orrick data → auto Tier D — ACCEPTED
Only 2 Orrick laws + 53 IAPP active bills lack Orrick data. The 53 IAPP bills are pending legislation — the Orrick gate legitimately flags them. Accept Tier D for these.

### BUG-2: Failed extraction retry — FIXED
### BUG-3: Supabase sync "not configured" — FIXED
### BUG-4: Unicode normalization in evidence spans — FIXED (Phase 1, 2026-04-05)

### BUG-5: Gemma 4 `<|channel>thought` HTTP 400 — KNOWN / WORKAROUND
LM Studio + Gemma 4 26B-A4B occasionally emits a structured thinking token that triggers a 400 error. Affects ~2 passages per run; they fall through as `uncertain` triage. Fix: update LM Studio when a Gemma-4-compatible release is available.

### BUG-6: Alembic migration `g3d9e5f7b208` DuplicateObject — FIXED (2026-05-09)
`triagedecision` / `triagemethod` enum types collided between manual `CREATE TYPE` and SQLAlchemy DDL. Fixed by removing manual blocks; SQLAlchemy owns enum creation via `sa.Enum(create_constraint=False)`.
