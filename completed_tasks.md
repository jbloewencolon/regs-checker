# Regs Checker — Completed Tasks

## Recently Completed (current session — still matters for reasoning)

### Phase 3 Confidence Scoring Improvements (2026-04-05)

#### ANALYSIS-4: Orrick tokenizer Unicode safety
- Read `src/core/orrick_validation.py` tokenizer: `re.findall(r"[a-z0-9]+", text.lower())`
- Confirmed immune to Unicode typography variants — dashes/quotes/spaces are all treated as word separators
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
