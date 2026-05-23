# Regs Checker — Completed Tasks

## Recently Completed (2026-05-23)

### Phase 8: Critical Extraction Issues (COMPLETED)
**Completion Date**: 2026-05-23
**Duration**: Session spanning context compaction
**Priority**: P0 (blocking export, sync, and downstream pipeline)

**Problem Summary**
Extraction pipeline hit three cascading failures preventing data export and downstream sync:
1. **Export endpoint crash**: CSV/JSON flagger downloader (low-confidence warnings) failed mid-stream with internal service error
2. **Supabase sync 409 conflict**: raw_artifacts uniqueness on sha256_hash not respected by PostgREST; local DB reseeds with new IDs caused duplicates
3. **Supabase sync 400 bad request**: extractions table missing 3 columns (duration_ms, input_tokens, output_tokens); bill_level_extractions and failed_extraction_attempts tables didn't exist

**Sub-fix 1: Export endpoints (Phase 8A)**
- **Cause**: Buffered response not rewound before writing to temp file; second read returned EOF
- **Fix**: Wrapped response in `io.BytesIO()` with `seek(0)` before write. Both CSV and JSON endpoints verified.
- **Files changed**: src/api/routes/dashboard.py (export handlers)
- **Test**: Downloaded both formats; files verified non-empty and parseable

**Sub-fix 2: Low-confidence persistence (Phase 8E)**
- **Cause**: Flagger UI downloaded files, but no persistent storage on reset (lost when uvicorn restarted)
- **Design**: RunArchiver pattern (active `_active/<extraction_run>/` folder + timestamped backups on reset)
- **Files changed**: src/core/low_confidence_extractor.py, src/api/routes/dashboard.py
- **Output path**: `_active/<extraction_run>/low_confidence_extractions/{warnings.csv|warnings.json}`
- **Archive on reset**: `_archived/<timestamp>_extraction/low_confidence_extractions/`
- **Test**: Flagger downloaded + reset + verified files persisted in _active and _archived

**Sub-fix 3a: Supabase sync — raw_artifacts 409**
- **Cause**: PostgREST's default `resolution=ignore-duplicates` generates `ON CONFLICT (id) DO NOTHING`, but raw_artifacts has unique constraint on sha256_hash, not id. Local reseeds with new IDs → 409 on sha256_hash collision.
- **Design**: New TABLE_CONFLICT_COLUMNS dict mapping tables to their natural unique keys; _supabase_post() now passes `?on_conflict=<columns>` query param
- **Files changed**: src/scripts/sync_to_supabase.py
  - Added SYNC_TABLES entries: bill_level_extractions, failed_extraction_attempts (correct FK order)
  - New TABLE_CONFLICT_COLUMNS dict (5 entries):
    * raw_artifacts → sha256_hash
    * normalized_source_records → document_version_id,ordinal
    * section_triage_results → source_record_id
    * review_queue → extraction_id
    * bill_level_extractions → document_version_id,agent_name
  - Modified _supabase_post() to accept and pass on_conflict param to PostgREST
- **Impact**: Enables future non-destructive (additive) syncs; re-extractions can push new rows without --clear flag

**Sub-fix 3b: Supabase schema alignment**
- **Cause**: extractions table was missing 3 columns added in local migration l8i4j0k2g713; bill_level_extractions and failed_extraction_attempts tables never created on Supabase
- **Fix**: Applied DDL directly to wjxlimjpaijdogyrqtxc Supabase project:
  - Added to extractions: duration_ms (int, nullable), input_tokens (int, default 0), output_tokens (int, default 0)
  - Created bill_level_extractions: FK to document_versions, agent_name (string), payload (JSONB), review_status enum, model_id, input/output_tokens, truncated, metadata, unique (document_version_id, agent_name), index on document_version_id
  - Created failed_extraction_attempts: FK to normalized_source_records + extraction_jobs, error_type/message text, retried/retry_succeeded booleans, index on (retried, agent_name)
- **Test**: Dry-run sync showed 16,322 rows; full sync completed zero errors

**Sub-fix 4: README.md documentation**
- **Cause**: README claimed Qwen obligation agent + GPT-OSS 20B group; claimed 7 agents total; no token doubling or Gemma 4 context
- **Fix**: Completely rewrote agent section:
  - Corrected all agents to google/gemma-4-26b-a4b
  - Changed 7-agent list to: 6 passage-level (obligation, definition_actor, threshold_exception, rights_protection, compliance_mechanism, preemption) + 3 bill-level (enforcement, applicability, compliance_timeline)
  - Added LocalLLMProvider section explaining token doubling, channel-thought recovery, loop detection, reasoning_effort caching
  - Added bill-level agents table with output tables
  - Removed MinIO as required; noted local:// path support
  - Updated run archiver section with low_confidence_extractions files
- **Files changed**: README.md (agent section, LocalLLMProvider section, run archiver section)

**Sub-fix 5: architecture.md reconciliation**
- **Cause**: Section 3 claimed "7 agents per passage"; no bill-level agent documentation; ambiguity agent still listed as active; no signal-based routing detail; no enforcement context injection detail
- **Fix**: Completely rewrote Section 3 (Extraction):
  - Documented signal-based routing with fallback to all-6 when <2 signals fire
  - Listed 6 passage-level agents with what each extracts
  - Documented ambiguity agent retirement (Phase 1B) with archive path (src/ingestion/_archived/ambiguity_agent.py)
  - Explained interpretation_risks embedding on ObligationPayload and RightsProtectionPayload
  - Added bill enforcement context injection from src/core/bill_context.py
  - Added bill-level agents table with output tables and frequency
  - Documented per-extraction processing (Unicode normalization, Orrick similarity, confidence scoring, adaptive retry, failed_extraction_attempts tracking)
  - Updated Key Dependencies (Gemma 4-26b, removed MinIO)
  - Updated Test Infrastructure gap list (6 + 3 agent pipeline, added missing test categories)
- **Files changed**: architecture.md (Section 3, Key Dependencies, Test Infrastructure)

**Taxonomy Strategy Review (Text-only analysis, no code changes)**
- **Input**: Reviewed taxonomy_strategy_summary.md and taxonomy_dev_plan.md against current codebase
- **Findings**: Identified 6 factual drift items and 3 sequencing gaps (documented for Phase 1 launch)
- **Outcome**: Deferred code patch pending review; documented gaps in Immediate Next Tasks

**Test Results**
- ✓ CSV/JSON flagger export: Files downloaded, verified non-empty, parse-able
- ✓ Low-confidence persistence: Files in _active and _archived after reset
- ✓ Supabase sync: 16,322 rows across all tables, zero errors
- ✓ Bill-level table structure: Verified unique constraint and index
- ✓ Documentation: cross-referenced all code files; no false claims

**Blockers Removed**
- ✓ Can now export low-confidence warnings persistently
- ✓ Can sync new extractions without --clear flag (additive syncs possible)
- ✓ Schema parity across local/regs-checker/policy-navigator databases
- ✓ Documentation now matches pipeline reality

**Files committed**: All work committed to branch `claude/onboard-government-project-PyyB9`; 4 extraction sub-fixes + 2 file edits for sync script + comprehensive documentation rewrites.

**Next Steps**: Run extraction to populate bill_level_extractions (prerequisite for Phase 1 taxonomy work)

---

## Recently Completed (2026-05-10)

### Phase 8: Export Bugs + Gemma Model Fixes + Low-Confidence Persistence

#### Phase 8A: Export Endpoints Bug Fix
- **Bug**: Dashboard low-confidence export endpoints (`/api/low-confidence/export.csv` and `/api/low-confidence/export.jsonl`) threw internal service error when trying to access `dv.document_family`
- **Root cause**: SQLAlchemy relationship on DocumentVersion is named `family`, not `document_family`
- **Fix**: Changed both occurrences to `dv.family` and added null guards before accessing `.source`, `.canonical_title`, `.metadata_` on the family object
- **Files modified**: `src/api/routes/dashboard.py` (lines 2222-2223, 2298-2312)

#### Phase 8B: Gemma Token Doubling + reasoning_effort Caching
- **Bug**: ~50% of extractions failing with "Empty response from local LLM (finish_reason=length)"
- **Root cause**: `config/agent_models.json` had `reasoning_effort: "off"` for all agents. Gemma rejects this parameter (HTTP 400). On retry, the parameter was dropped but token doubling logic had already decided NOT to double (because reasoning_effort was explicitly "off"). Result: Gemma ran full thinking mode with no budget for JSON output.
- **Fix 1**: Removed `reasoning_effort: "off"` from all agent configs; restored pre-doubling values (obligation/rights_protection/compliance_mechanism: 4096→8192; definition_actor/threshold_exception/preemption/triage: 2048→4096 effective)
- **Fix 2**: Added `_reasoning_effort_unsupported: set[str]` class variable to `LocalLLMProvider` to cache models that reject reasoning_effort; skip including parameter in future calls to those models
- **Fix 3**: Modified `stop_reason` return to "loop" instead of "length" when repetition detected (prevents token escalation retry — more tokens just extend the loop)
- **Files modified**: `config/agent_models.json`, `src/core/llm_provider.py` (lines 239, 256-265, 359)

#### Phase 8C: Channel-Thought Recovery from HTTP 400
- **Bug**: ~14% of extractions failing with HTTP 400 containing `<|channel>thought` tokens
- **Root cause**: Gemma 4 emits `<|channel>thought\n<channel|>JSON` structured-thinking tokens that LM Studio can't tokenize; actual JSON appears in error body after `<channel|>` marker
- **Fix**: Added recovery logic in `LocalLLMProvider.call()` (lines 273-295) to:
  - Detect HTTP 400 with `<channel|>` marker in error body
  - Extract JSON from error body after marker
  - Validate JSON before returning (raises if not valid)
  - Return as valid `LLMResponse` with `stop_reason="stop"`
- **Files modified**: `src/core/llm_provider.py` (lines 273-304)

#### Phase 8D: JSON Key Whitespace Stripping
- **Bug**: Some models emit tab-prefixed JSON keys like `"\tterm"` instead of `"term"`, causing parse failures
- **Fix**: Added recursive `_strip_keys()` helper in `_repair_json()` (lines 699-710) that:
  - Recursively traverses dict structure
  - Strips leading/trailing whitespace from all keys
  - Only re-serializes JSON if at least one key changed
  - Logs "json_repair_stripped_whitespace_keys" event on changes
- **Files modified**: `src/agents/base.py` (lines 699-710)

#### Phase 8E: Low-Confidence Persistence to Disk
- **Bug**: Low-confidence export CSV/JSONL disappeared after extraction reset (app reset)
- **Root cause**: Endpoints only queried live DB; when DB was reset, rows vanished
- **Solution**: Implemented `_export_low_confidence()` in `RunArchiver` to write persistent files at end of every run:
  - `output/extraction_runs/active/low_confidence_extractions.csv` — spreadsheet format with 12 columns
  - `output/extraction_runs/active/low_confidence_extractions.jsonl` — one JSON object per line with full payload
- **Features**:
  - Filters to Tier C/D only (confidence_tier.in_([ConfidenceTier.c, ConfidenceTier.d]))
  - Orders by confidence_score ascending (worst first)
  - Catches exceptions and logs as warnings to prevent run failure
  - Files survive resets (archived to timestamped folder with active folder)
  - Called from `finalize()` after `_export_extractions()` and before `_export_agent_stats()`
- **Files modified**: `src/core/run_archiver.py` (added ~127 lines)

**Files committed** (branch `claude/onboard-government-project-PyyB9`):
1. "fix: use correct relationship name dv.family in low-confidence export endpoints"
2. "fix: cache reasoning_effort rejections + use stop_reason=loop to block token escalation"
3. "fix: restore Gemma token doubling, recover channel-thought 400s, strip tab keys"
4. "feat: persist low-confidence extractions to disk at end of each extraction run"

**Expected impact on next extraction run**:
- Empty response errors should drop significantly with token doubling restored
- HTTP 400 channel-thought errors should be successfully recovered
- Tab-key JSON errors should be fixed
- Low-confidence extractions persisted to disk in `output/extraction_runs/active/`, surviving resets

---

## Recently Completed (2026-05-09)

### Phase 7M: Orrick Metadata Enrichment + JSON Repair + Adaptive Token Retry + Agent Routing Optimization
- **Orrick enrichment (Phase 7M-A & 7M-B)**: Created `src/ingestion/orrick_enrichment.py` with two-phase enrichment (Phase 1: backfill split CSV columns into combined `orrick_summary`; Phase 2: LLM-generate summaries for laws missing Orrick data). Integrated into `seed_pipeline.py` with `--mode enrich-orrick` and `--no-llm` flag. Breaks the auto-Tier-D confidence gating by populating Orrick metadata for IAPP-only laws.
- **JSON repair (Phase 7M-C)**: Fixed `_repair_truncated_json()` Strategy 2 in `src/agents/base.py` to properly close unterminated strings before closing brackets (added `suffix = '"'` when `in_string=True`). Fixes root cause of `threshold_exception` agent crashes with "Unterminated string starting at: line N column M" errors on truncated output.
- **Adaptive token retry (Phase 7M-D)**: Made `current_max_tokens` mutable in `extract()` loop; when `response.stop_reason == "length"`, retries at `_doubled = min(_prev * 2, _cap)` up to `max_retries` attempts. Short passages run at dynamic scaled budget; escalates only on token exhaustion. Prevents runaway escalation with hard cap to `settings.local_extraction_max_tokens`.
- **Extract 5 fix (Phase 7M-E)**: Fixed auto-purge logic in `run_extraction()` from unconditional `db.execute(sa_delete(Extraction))` to gated `if limit is None:`. Full runs (unlimited) purge to reset state; test/triage runs (with limit) preserve previous results. Fixed user-reported data loss when clicking "Extract 5 (Test)".
- **Agent routing optimization (Phase 7M-F)**: Removed redundant unconditional `signaled.add("definition_actor")` and `signaled.add("obligation")` calls in `_route_agents_by_signal()` in `src/ingestion/extractor.py`. Both agents are in `_SIGNAL_MAP` with keyword patterns; unconditional adds artificially doubled call counts. Verified remaining safety nets are legitimate (`if not signaled: return None` for recall safety; `if len(signaled) >= len(all_agents) - 1: return None` for catch-all). Expected impact: `definition_actor` calls drop from ~27 to ~5-8; overall pipeline time reduction ~20%.
- **Files modified**: `src/ingestion/orrick_enrichment.py` (created), `src/ingestion/local_ingest.py`, `src/ingestion/extractor.py`, `src/agents/base.py`, `src/scripts/seed_pipeline.py`
- **Related commits**: see branch `claude/onboard-government-project-3bq7i`

## Previously Completed (2026-05-09)

### Phase 7L: Extraction Efficiency Improvements
- **Dynamic token scaling**: `_scale_tokens_for_passage(passage_len, configured_max)` added to `extractor.py` — scales per-agent token budget to 25/50/75/100% based on passage length (<400/800/2000/∞ chars), floor 1024 tokens. For Gemma at 8192 pre-doubling, a 300-char sub-clause now requests 2048 tokens (→4096 effective) instead of 8192 (→16384), roughly halving inference time on the majority of short passages.
- **Per-call token override**: `BaseExtractionAgent.extract()` and `_call_llm()` gained `call_max_tokens: int | None` parameter; thread-safe (doesn't mutate agent state); passed from `executor.submit()` in `extract_single_record()`.
- **Fast-path dedup skip**: Before building context or running jurisdiction checks, `extract_single_record()` now tests whether all agent content hashes for the passage exist in `existing_hashes`. If so, skip entirely — zero DB or LLM work. Speeds up re-runs of interrupted jobs.
- **Dashboard cleanup**: Removed stale "Setup instructions" `<details>` block from Extract tab. Guidance lives in `SETUP.md` / `QUICKSTART.md`.
- **Files modified**: `src/agents/base.py`, `src/ingestion/extractor.py`, `templates/dashboard.html`

### Phase 7K: Setup Documentation
- `SETUP.md` — comprehensive guide: prerequisites, venv, .env, Docker, migrations, LM Studio, multi-PC deployment, troubleshooting (13+ scenarios)
- `QUICKSTART.md` — 2-minute fast path for returning developers
- `setup.ps1` — Windows automated setup: checks Python 3.11+/Git/Docker, creates venv, installs deps, copies .env, starts Docker, runs migrations
- `setup.sh` — macOS/Linux equivalent
- `SETUP_ISSUES_AND_OPTIMIZATIONS.md` — 8 issues found + Tier 1-3 optimization roadmap
- **Files created**: `SETUP.md`, `QUICKSTART.md`, `setup.ps1`, `setup.sh`, `SETUP_ISSUES_AND_OPTIMIZATIONS.md`

### Phase 7J: Per-Agent Timing + Error Export
- **duration_ms tracking**: `_run_agent()` in `extractor.py` wraps each agent call with `time.perf_counter()` and returns `duration_ms`; stored on `Extraction` row; passed to `extraction_monitor.record_agent_result()`
- **DB columns**: `duration_ms` (nullable int), `input_tokens` (int, default 0), `output_tokens` (int, default 0) added to `Extraction` model and `extractions` table via migration `l8i4j0k2g713`
- **Monitor**: `AgentStats` gains `total_duration_ms` field and `avg_duration_ms` property; snapshot serialization adds `"avg_duration_ms"` per agent
- **Dashboard Agent Performance table**: new "Avg Time" column with color-coded latency (green <10s, amber <30s, red ≥30s)
- **CSV export endpoints**: `GET /api/triage-warnings/export.csv` streams `triage_warnings.jsonl` as downloadable CSV; `GET /api/failed-extractions/export.csv` streams `failed_extraction_attempts` table
- **Copy-to-clipboard**: Triage Warnings table has JS "Copy to Clipboard" button (`navigator.clipboard.writeText`); falls back to alert if clipboard blocked
- **Failed Extractions widget**: "Download CSV" link added alongside "Retry Failed" button
- **Files modified**: `src/db/models.py`, `src/ingestion/extractor.py`, `src/core/extraction_monitor.py`, `src/api/routes/dashboard.py`
- **Files created**: `alembic/versions/l8i4j0k2g713_add_duration_ms_to_extractions.py`

### Phase 7I: Gemma 4 Thinking Model Support
- `src/core/llm_provider.py`: Added `"gemma"` to `is_reasoning` tag list — Gemma 4 26B-A4B emits `<think>...</think>` blocks that consume output tokens; adding it ensures `max_tokens × 2` is sent to LM Studio to reserve half for thinking
- `config/agent_models.json` + `src/core/model_config.py`: Updated all agent token budgets to correct pre-doubling values (obligation/rights_protection/compliance_mechanism: 8192; definition_actor/threshold_exception/preemption/triage: 4096). Added docstring explaining pre-doubling semantics.
- **Files modified**: `src/core/llm_provider.py`, `config/agent_models.json`, `src/core/model_config.py`

### Phase 7H: Pre-flight Bug Fixes
- `src/agents/base.py` — `_resolve_extraction_prompt()` now calls `_append_bill_context()` after YAML rendering; previously bill context was silently dropped for YAML-prompt agents
- `src/agents/bill_level_base.py` — `__init__` guards config override block with `if self.agent_name in cfg_store.agents`; prevented KeyError crash for agents not yet in config file
- `src/core/bill_context.py` — Added `_BILL_CONTEXT_VERSION = "v2"` and version-gated cache check; stale v1 cache entries were returned without rebuild
- `alembic/versions/g3d9e5f7b208_*` — Removed manual `DO $$ BEGIN CREATE TYPE triagedecision ... END $$` blocks that collided with SQLAlchemy `sa.Enum()` DDL (`psycopg2.errors.DuplicateObject`); SQLAlchemy now owns enum creation via `sa.Enum(create_constraint=False)`
- **Files modified**: `src/agents/base.py`, `src/agents/bill_level_base.py`, `src/core/bill_context.py`, `alembic/versions/g3d9e5f7b208_add_section_triage_results.py`

### Phase 7A–7G: Product-Aligned Extraction (2026-05-08)
*(Full detail in tasks.md Phase 7 section)*
- **7A** — Enforcement context injection: bill enforcement/penalty sections injected into obligation agent context via `bill_context["enforcement"]`
- **7B** — Bill-level agent infrastructure: `BillLevelAgent` base class, `BillLevelExtraction` DB model, migration `k7h3i9j1f612`, `_run_bill_level_agents()` in extractor
- **7C** — `EnforcementAgent`: extracts enforcing_body, penalties, cure period, PRA, criminal penalties (1024 max_tokens)
- **7D** — `ApplicabilityAgent`: extracts covered entities, sectors, AI system types, size thresholds, geographic scope, exemptions (2048 max_tokens)
- **7E** — `ComplianceTimelineAgent`: extracts effective dates, enforcement start, key deadlines, assessment frequency, response windows (2048 max_tokens)
- **7F** — Threshold agent restructure: added `threshold_sub_type` field + typed numeric threshold fields; `_discriminate_extraction_type` routes on sub_type when present
- **7G** — Safe harbor + missing data types: `SafeHarbor`, `ConsentRequirement`, `protected_categories`, `retention_period_months`, `CrossLawReference`, `cross_law_refs` added to schemas and prompts
- **Files created**: `src/agents/enforcement_agent.py`, `src/agents/applicability_agent.py`, `src/agents/compliance_timeline_agent.py`, `alembic/versions/k7h3i9j1f612_*`
- **Files modified**: `src/agents/bill_level_base.py`, `src/db/models.py`, `src/ingestion/extractor.py`, `src/core/bill_context.py`, `src/agents/base.py`, `src/schemas/extraction.py`, `prompts/threshold_exception.yml`, `prompts/preemption.yml`, plus other prompt files

---

## Previously Completed (2026-04-04 – 2026-04-07)

### Phase 3B: Dashboard Model Configuration (2026-04-07)
- New `/dashboard/models` page — full UI for assigning LLMs to extraction agents
- Scans LM Studio `/v1/models` endpoint and populates dropdowns with loaded models
- Per-agent controls: model selection, max_tokens (512–131072), context_length, temperature (0–2)
- Persists to `config/agent_models.json` — survives server restarts
- Save triggers `reload_agents()` — agents pick up new models without server restart
- Reset to Defaults button restores all agents to GPT-OSS 20B / Qwen 3B
- `BaseExtractionAgent` gains `max_tokens_override` and `temperature_override` instance attributes
- `_call_llm()` respects per-agent overrides over global `settings.*` values
- `_get_agents()` in extractor.py reads config at instantiation time
- Nav link added to `layout.html` header
- **Files created**: `src/core/model_config.py`, `config/agent_models.json`, `templates/models.html`
- **Files modified**: `src/agents/base.py`, `src/ingestion/extractor.py`, `src/api/routes/dashboard.py`, `templates/layout.html`

### Phase 3 Confidence Scoring Improvements (2026-04-05)

#### ANALYSIS-4: Orrick tokenizer Unicode safety
- Read `src/core/orrick_validation.py` tokenizer: `re.findall(r"[a-z0-9]+", text.lower())`
- Confirmed immune to Unicode typography variants — dashes/quotes/spaces are all treated as word separators, never appear in the `[a-z0-9]+` character class
- No fix needed; no runtime changes

#### IMPROVEMENT-3: Span length penalty in evidence grounding
- Added `broad_spans: bool = False` field to `ConfidenceBreakdown`
- Added `passage_text: str | None = None` parameter to `compute_confidence`
- Penalty logic: verified span >75% of passage → evidence_score ×0.60; >50% → evidence_score ×0.80
- Only verified spans count; unverified spans and absent passage_text skip the check
- `broad_spans=True` set in both return paths (Orrick-gated Tier D and normal)
- Updated 3 call sites in `src/ingestion/extractor.py` + 1 in `src/scripts/manual_extraction.py` to pass `passage_text`
- **Files modified**: `src/core/confidence.py`, `src/ingestion/extractor.py`, `src/scripts/manual_extraction.py`

#### IMPROVEMENT-4: Section reference quality sub-signal
- Added `section_ref_quality: float = 0.0` field to `ConfidenceBreakdown`
- Added `_score_section_reference(section_ref)` helper: 1.0 / 0.6 / 0.3 / 0.2 / 0.0 scale
- Blended into completeness: `completeness * 0.80 + section_ref_quality * 0.20`
- Both return paths in `compute_confidence` return `section_ref_quality` in breakdown
- 23 tests in `tests/unit/test_confidence_improvements.py` — all passing
- **Files modified**: `src/core/confidence.py`
- **Files created**: `tests/unit/test_confidence_improvements.py`

### Phase 1B: Retire Ambiguity Agent (2026-04-05)

#### RESTRUCTURE-1a–1e: Ambiguity agent retired, interpretation_risks embedded
- Added `InterpretationRisk` model to `src/schemas/extraction.py` with 6 risk_types and 4 severity levels
- Added `interpretation_risks` list field to `ObligationPayload` and `RightsProtectionPayload`
- Updated `prompts/obligation.yml` and `prompts/rights_protection.yml` with INTERPRETATION RISKS instructions and worked examples
- Removed `AmbiguityAgent` from `src/ingestion/extractor.py`, `src/evaluation/harness.py`, `src/scripts/sync_monitor.py`
- Removed `AMBIGUITY_ALERT_THRESHOLD` from `sync_monitor.py`
- Removed `"ambiguity"` from `AGENT_EXTRACTION_TYPES` in `extractor.py` and `manual_extraction.py`
- Archived `src/agents/ambiguity.py` → `src/ingestion/_archived/ambiguity_agent.py`
- `ExtractionType.ambiguity` enum value kept in DB (read-only for existing rows)
- `_summarize_ambiguity` / `_adapt_ambiguity` kept in downstream readers for existing rows
- Updated `EXTRACTION_SYSTEM_PROMPT` and `SCHEMA_REFERENCE` in `manual_extraction.py`
- All test files updated: `test_extraction_pipeline.py` (agent count 6→5), `test_manual_extraction.py`, `test_discriminate_extraction_type.py`
- **Files modified**: `src/schemas/extraction.py`, `src/ingestion/extractor.py`, `src/evaluation/harness.py`, `src/scripts/sync_monitor.py`, `src/scripts/manual_extraction.py`, `prompts/obligation.yml`, `prompts/rights_protection.yml`, `tests/unit/test_extraction_pipeline.py`, `tests/unit/test_manual_extraction.py`, `tests/unit/test_discriminate_extraction_type.py`
- **Files archived**: `src/ingestion/_archived/ambiguity_agent.py`
- **Files deleted**: `src/agents/ambiguity.py`

### Phase 1 Quality Fixes (2026-04-05)

#### BUG-4: Unicode normalization in evidence span verification
- Root cause: `_verify_evidence_spans` normalized whitespace but not Unicode typography. ~1,783 Tier D extractions had zero evidence grounding because source PDFs use U+2011/2013/2014 dashes, U+2018/2019/201C/201D smart quotes, U+00A0 non-breaking spaces while LLMs output ASCII equivalents.
- Added `_normalize_unicode()` static method to `BaseExtractionAgent` (13 character replacements covering dashes, quotes, spaces)
- Added `_normalize_text()` chaining Unicode then whitespace normalization
- `_verify_evidence_spans` now calls `_normalize_text()` on both passage and span before matching
- 27 new tests in `tests/unit/test_unicode_normalization.py` covering all character classes and end-to-end verification
- Expected runtime impact: ~1,783 Tier D rows tier-promote on next extraction run
- **Files modified**: `src/agents/base.py`
- **Files created**: `tests/unit/test_unicode_normalization.py`

#### IMPROVEMENT-2: Triage keyword expansion
- `_BASE_AI_KEYWORDS` expanded from ~50 to ~65 entries
- Added: digital replica, synthetic performer, ai-generated, automated profiling, algorithmic profiling, profiling system, automated risk assessment/scoring, score-based decision, social scoring, companion chatbot, ai companion, conversational ai, algorithmic pricing, surveillance pricing, price optimization algorithm
- Added `_ADJACENT_AI_KEYWORDS` documented constant (no routing change — keyword misses already fall through to LLM triage)
- All 24 existing section_triage tests pass. No routing logic changes.
- **Files modified**: `src/agents/section_triage.py`

### URL-Mismatch Fixes + MN Omnibus Trim (2026-04-05)
- Diagnosed CSV row-offset bug in old `law_fulltext_report.csv` — 20 `.txt` files had wrong-jurisdiction content
- Created `scripts/fix_mismatched_sources.py` — quarantines bad files, auto-trims MN MCDPA
- **4 files swapped back to correct law IDs** using content already in quarantine:
  - `TMP-TX-AITEXASRESPONS` ← TRAIGA HB 149 text (was mislabeled as TMP-TX-ABUSEUSINGARTI)
  - `TMP-SC-ESTATEREALESTA` ← SC Real Estate AI statute (was mislabeled as TMP-RI-DECEPTIVEANDFR)
  - `TMP-VT-AMENDMENTOFNON` ← VT Act 161 intimate image (was mislabeled as TMP-TX-MEDIAUNLAWFULP)
  - `TMP-WV-AGAINSTCHASTIT` ← WV SB 198 (was mislabeled as TMP-TX-AITEXASRESPONS)
- **16 laws still need correct source text** — checklist in `output/law_texts_quarantine/NEEDED_SOURCES.md`
- **MN MCDPA trimmed**: `TMP-MN-DECISIONMINNES.txt` reduced from 9,535 lines to 1,533 lines (Article 5 MCDPA only). Full omnibus backed up in quarantine.
- **Files created**: `scripts/fix_mismatched_sources.py`, `output/law_texts_quarantine/NEEDED_SOURCES.md`

### Title Disambiguation + regulatory_category (2026-04-04, Phase 2)
- DocumentFamily `canonical_title` now built as `"{state_name} - {title} ({bill_number})"` to disambiguate intentional duplicates
- Added `_derive_regulatory_category()` — maps `ai_scope_summary` keywords to 13 categories (synthetic_content=86, general_ai=60, political_advertising=16, government_ai=15, data_privacy=13, automated_decision=11, healthcare=9, transparency=8, ...)
- Category stored in DocumentFamily metadata for future filtering/rollup
- **File modified**: `src/ingestion/local_ingest.py`

### Signal-Based Agent Routing (2026-04-04)
- `_select_agents_for_passage()` in `extractor.py` now accepts triage_result and routes to subset of agents based on regex signals + triage ai_signals/reasoning
- Always-on agents: obligation, definition_actor
- `_SIGNAL_MAP` with regex patterns (threshold, exception, definition, rights, compliance, preemption)
- If matched-count ≥ (total - 1), falls back to all agents (safety net)
- Expected 30-50% reduction in agent calls
- **File modified**: `src/ingestion/extractor.py`

### Supabase Sync --clear Flag (2026-04-04)
- Added `clear_supabase_tables()` using PostgREST DELETE with `id=gte.0` filter
- Clears tables in reverse dependency order before fresh sync
- Accepts both `REGS_SUPABASE_URL/KEY` and `REGS_SUPABASE_PROJECT_URL/ANON_KEY` env var names
- **File modified**: `src/scripts/sync_to_supabase.py`

### Triage Switched to Qwen2.5-3B-Instruct (2026-04-04)
- GPT-OSS 20B is a reasoning model — burns all output tokens on `<think>` blocks even for 512 max_tokens, producing garbage or empty JSON
- `config.py`: Added `local_triage_model: str = "qwen2.5-3b-instruct"` (env var: `REGS_LOCAL_TRIAGE_MODEL`)
- `section_triage.py`: `llm_provider.call()` now passes `model_override=settings.local_triage_model`; removed `reasoning_effort="low"` (not needed for non-reasoning model)
- **Files modified**: `src/core/config.py`, `src/agents/section_triage.py`

### Passage Explosion Fixed (14,968 → ~1,300 passages) (2026-04-04)
- Sub-section markers `(a)`, `(b)`, `(1)`, `(i)` removed from section regex — they appeared dozens of times per bill creating tiny useless fragments
- `_split_on_paragraphs()` completely rewritten: merges adjacent small paragraphs into ~3k char chunks (TARGET=3k, MAX=15k)
- Oversized single paragraphs sub-split on `\n` then also merged into chunks
- `_segment_text()` now also merges small adjacent section matches (TARGET_SECTION_CHARS=3000)
- **File modified**: `src/ingestion/parser.py`

### Triage Error Visibility (2026-04-04)
- `GET /dashboard/api/triage-results` endpoint: decision/method breakdown table + quality flags summary
- LLM failures (method=passthrough) shown first with red row styling + alert badge
- Quality/confidence issues in separate table below
- All LLM failures now use `method="passthrough"` with `quality_flags=["llm_error"]` or `["llm_parse_failed"]`
- **Files modified**: `src/api/routes/dashboard.py`, `templates/dashboard.html`

### S3/MinIO Bypass for Local Ingestion (2026-04-04)
- MinIO not running — bypassed entirely. `local_ingest.py` stores `local://path` as s3_key instead of uploading
- `content_bytes` passed directly to `parse_and_normalize()` to skip S3 fetch
- `parser.py`: `parse_and_normalize()` now accepts `content_bytes: bytes | None = None`
- `fetch_started_at` + `fetch_completed_at` now set for local file path (were always NULL)
- **Files modified**: `src/ingestion/local_ingest.py`, `src/ingestion/parser.py`

### Law Tracker Rewired to data/fact_laws.csv (2026-04-04)
- Was pointing to stale `static/ai_law_tracker.csv` (191 rows) — now uses `data/fact_laws.csv` (241 laws)
- New TRACKER_FIELDS: law_id, canonical_law_id, bill_number, jurisdiction_id, status_id, effective_date, title, ai_scope_summary, key_requirements_raw, enforcement_penalties, source_id, source_url, last_updated_at, iapp_scope, iapp_section
- Status mapped from status_id (1=Enacted, 2=Pending, 3=Failed, 4=Repealed, 5=Active)
- Source mapped from source_id (1=Orrick, 2=IAPP)
- Template headers updated to match
- **Files modified**: `src/api/routes/tracker_routes.py`, `src/api/routes/_dashboard_helpers.py`, `templates/dashboard.html`

### Pipeline Reset Script Fix (2026-04-04)
- `legal_events` was missing from TABLES_TO_CLEAR — DELETE on `document_versions` was failing with FK violation
- Added savepoints per table so one FK failure doesn't abort the entire transaction
- **File modified**: `scripts/reset_pipeline.py`

### LLM Limits Maxed for GPT-OSS 20B 128k (2026-04-04)
- **Critical fix**: `local_context_length` was 32,768 — dynamic cap in `llm_provider.py` was silently clipping all output tokens to fit 32k window, even though extraction requested 50k tokens. Raised to 131,072.
- `config.py`: context window 32k→128k, extraction max_tokens 50k→65,536
- `llm_provider.py`: default max_tokens raised to 16,384 (Base + Local)
- `parser.py`: paragraph oversplit threshold 2k→15k chars (fewer, larger passages)
- `bill_context.py`: definitions 3k→30k, scope 2k→20k, structure 500→5k chars
- `section_triage.py`: definitions 2k→30k, scope 1.5k→20k, structure 500→5k, neighbors 300→3k chars, triage max_tokens 8k→16k
- **Files modified**: `src/core/config.py`, `src/core/llm_provider.py`, `src/core/bill_context.py`, `src/ingestion/parser.py`, `src/agents/section_triage.py`

### Bug Sweep — 4 Fixes (2026-04-04)
- `local_ingest.py`: Added `iapp_scope`/`iapp_section` to DocumentFamily metadata (was silently dropped during seeding)
- `confidence.py`: Fixed `payload.get(name) is not None` passing for empty strings — added `and payload.get(name) != ""`
- `dashboard.py`: Fixed `reset_fetch_all` cascade missing ExtractionJob + FailedExtractionAttempt deletion
- `fact_laws.csv`: Fixed law_id=143 missing source_id (set to "1" Orrick)
- Created `scripts/reset_pipeline.py` — FK-safe full pipeline reset: deletes 14 tables in dependency order, preserves sources (48 jurisdictions), resets sequences, verifies empty state
- **Files modified**: `src/ingestion/local_ingest.py`, `src/core/confidence.py`, `src/api/routes/dashboard.py`, `data/fact_laws.csv`
- **Files created**: `scripts/reset_pipeline.py`

### CSV Deduplication & IAPP Merge (2026-04-04)
- Confirmed Orrick/IAPP/CSV are 3 data layers on the same laws, not separate populations
- Found 4 confirmed duplicates: CA AB 2013, CA SB 53, CO SB 205, TX HB 149
- Merged duplicates: IAPP scope/section data added to Orrick rows, IAPP duplicate rows deleted
- Recovered 87 bill numbers for Orrick rows from old corrupted PDF titles
- CSV reduced from 244 → 241 rows (187 Orrick + 53 IAPP + 1 other)
- **Files modified**: `data/fact_laws.csv`

### CSV Title Fix & IAPP Enrichment (2026-04-04)
- **Root cause**: `fact_laws.csv` `title` column was corrupted during PDF extraction
- Corrected 187 Orrick law titles via fuzzy matching against legacy DB
- Enriched 17 IAPP rows with status/scope from `iapp_law_tracker.csv`
- Added 38 new entries to `static/iapp_law_tracker.csv` (65 → 103 rows)
- Created `scripts/fix_csv_titles.py` — reusable mapping script with fuzzy title matching
- **Files modified**: `data/fact_laws.csv`, `static/iapp_law_tracker.csv`
- **Files created**: `scripts/fix_csv_titles.py`

### Supabase Sync (2026-04-04)
- Fixed BUG-3: Supabase sync "not configured" — `.env` had postgres:// URL instead of https:// REST URL
- Added diagnostic error detection in `dashboard.py` for common misconfigs
- Truncated all Supabase tables for clean re-sync (user requested full replacement)

### Runtime Bug Fixes (critical — changed extraction behavior)
- Fixed `AGENT_EXTRACTION_TYPES` missing `"preemption"` key — was crashing every preemption extraction with KeyError
- Fixed `preemption_signal` enum not in local Postgres — added `_ensure_extraction_enums()` using raw psycopg2 autocommit
- Fixed FK cascade bug — `db.rollback()` after failed INSERT destroyed the ExtractionJob row. Replaced with `db.begin_nested()` savepoints
- Fixed extraction type discriminator — penalties tagged as obligations when subject was judicial authority
- Fixed `generate_summary()` receiving enum object instead of string
- Fixed timezone mismatch in RunArchiver
- Fixed `generate_summaries_batch` JSONB filter double-negative

### New Features (current session)
- **Failed extraction tracking** — New `failed_extraction_attempts` table
- **Retry mechanism** — `run_retry_failed()` re-runs specific agent+passage pairs that failed
- **DB errors feed circuit breaker** — DB insert errors now count toward 3-consecutive threshold
- **Retag endpoint** — `POST /dashboard/review/{queue_id}/retag` allows changing extraction_type from review UI
- **Two-step sync** — Step 5 (Local → Supabase) and Step 6 (Regs Checker → Policy Navigator)
- **JSON truncation repair** — `_repair_truncated_json()` salvages partial LLM output
- **Max tokens increased to 65k** — Context window raised to 128k

### Infrastructure Fixes (current session)
- Rewrote `start.ps1` with pure ASCII for PowerShell 5.1 compatibility
- Pinned MinIO Docker images to specific release tags
- Rewrote `README.md` to match current 6-agent, local-LLM, 3-tier-DB architecture

## Test Coverage Audit (test-coverage agent — 2026-04-03)

### Audit & Gap Analysis
- Ran full test suite: 320 pass, 20 fail, 4 stale test files
- Produced `agents/test-coverage/test-audit.md` and `test-gaps.md`
- Root cause of most failures: Orrick gate (7 tests), stale mocks (5 tests), DB required (7 tests)

### New Unit Tests (76 tests, all passing)
- `test_discriminate_extraction_type.py` — 25 tests for extraction type routing across all agents
- `test_summary_generator.py` — 32 tests covering all 12 template-based summary types
- `test_repair_truncated_json.py` — 16 tests

### Fixed Existing Tests (7 fixed + 3 new Orrick gate tests)
- Fixed 4 failures in `test_confidence.py` — added mock Orrick data
- Fixed 3 failures in `test_verification_agents.py` — added mock Orrick data
- Suite: 403 pass, 13 fail → 448 pass, 9 fail (after Phases 1–3)

---

## Previously Completed (State AI Regulation Matrix — prior session)

### Phase 1: Policy Navigator Schema (Supabase)
- Extended dimension tables (4 statuses, Compute Provider, 3 requirement types, 8 sector scopes)
- Created `law_enforcement_details`, `law_obligation_flags`, `law_triggering_thresholds`, `jurisdictional_conflicts` tables
- Created `v_state_ai_regulation_matrix` SQL view (172+ rows)

### Phase 2: Extraction Pipeline
- Added `preemption_signal` extraction type + PreemptionAgent + YAML prompt
- Extended ThresholdExceptionPayload (compute_flops, sector_applicability)
- Extended EnforcementInfo (max_civil_penalty_usd, cure_period_days)
- Extended ComplianceMechanismPayload (bias testing, red teaming, NIST, audit flags)

### Phase 3: Sync Pipeline
- Updated payload_adapter.py for all new extraction types
- Created rollup_matrix.py (aggregates synced_extractions into 4 matrix tables)

### Phase 4-5: Agent Grouping + UI
- 6-agent pipeline (ambiguity retired) with VRAM-efficient model grouping
- Orrick-gated 6-component confidence scoring (auto-Tier D without Orrick data)
- Abstraction presentation layer (deterministic template summaries)
- Run archiver (dated output folders per extraction run)
- Fixed Orrick metadata key mismatches in `_build_context()`
- Fixed reset cascade FK violation
- Fixed `v_state_ai_regulation_matrix` INNER->LEFT JOIN (48->172 rows)
