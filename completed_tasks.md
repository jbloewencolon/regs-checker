# Regs Checker — Completed Tasks

## Recently Completed (current session — still matters for reasoning)

### CSV Deduplication & IAPP Merge (2026-04-04)
- Confirmed Orrick/IAPP/CSV are 3 data layers on the same laws, not separate populations
- Mapped IAPP bill numbers to Orrick law names via old corrupted titles + IAPP tracker cross-reference
- Found 4 confirmed duplicates: CA AB 2013, CA SB 53, CO SB 205, TX HB 149
- Merged duplicates: IAPP scope/section data added to Orrick rows, IAPP duplicate rows deleted
- Remaining 52 IAPP rows are genuinely new (mostly ACTIVE BILLS not tracked by Orrick)
- Fixed wrong HB 149 merge (was matched to NY law instead of TX)
- Added `iapp_scope` and `iapp_section` columns to fact_laws.csv
- Recovered 87 bill numbers for Orrick rows from old corrupted PDF titles
- CSV reduced from 244 → 241 rows (187 Orrick + 53 IAPP + 1 other)
- **Files modified**: `data/fact_laws.csv`

### CSV Title Fix & IAPP Enrichment (2026-04-04)
- **Root cause**: `fact_laws.csv` `title` column was corrupted during PDF extraction — truncated names, statute references concatenated, words missing
- Mapped all 187 Orrick CSV rows to 186 legacy document_families (correct titles) via fuzzy matching by jurisdiction
- Corrected 187 Orrick law titles (e.g., "of Arkansas CSAM HB1877" → "Amendment of Arkansas CSAM Laws")
- Fixed 2 severely corrupted rows manually (law_id=25 California Employment Regs, law_id=218 Utah AI Impersonation)
- Enriched 17 IAPP rows with status/scope from `iapp_law_tracker.csv` (e.g., "SB 205" → "SB 205 (Enacted - Automated Decision Systems)")
- Added 38 new entries to `static/iapp_law_tracker.csv` (65 → 103 rows) from fact_laws IAPP data
- Identified 15 jurisdiction mismatches: same bill numbers in different states (e.g., AB 1018 is California in tracker, Arkansas in CSV)
- Created `scripts/fix_csv_titles.py` — reusable mapping script with fuzzy title matching
- **Files modified**: `data/fact_laws.csv`, `static/iapp_law_tracker.csv`
- **Files created**: `scripts/fix_csv_titles.py`

### Supabase Sync (2026-04-04)
- Fixed BUG-3: Supabase sync "not configured" — `.env` had postgres:// URL instead of https:// REST URL, and no JWT key
- Added diagnostic error detection in `dashboard.py` for common misconfigs (postgres:// URLs, non-JWT keys, missing env vars)
- Added `REGS_SUPABASE_PROJECT_URL` fallback to all 4 Supabase dashboard endpoints
- First sync pushed 47,485/64,995 rows (partial failures from FK cascades on batched inserts)
- Verified **zero duplicates** via Supabase SQL queries on composite keys and primary keys
- Truncated all Supabase tables for clean re-sync (user requested full replacement)
- Next step: run `python -m src.scripts.sync_to_supabase` for clean full push

### Runtime Bug Fixes (critical — changed extraction behavior)
- Fixed `AGENT_EXTRACTION_TYPES` missing `"preemption"` key — was crashing every preemption extraction with KeyError
- Fixed `preemption_signal` enum not in local Postgres — added `_ensure_extraction_enums()` that auto-adds missing enum values using raw psycopg2 autocommit (ALTER TYPE cannot run in transactions)
- Fixed FK cascade bug — `db.rollback()` after failed INSERT destroyed the ExtractionJob row, breaking all subsequent extractions for that document. Replaced with `db.begin_nested()` savepoints
- Fixed extraction type discriminator — penalties tagged as obligations when subject was judicial authority (court, AG). Added enforcement-subject + penalty-verb detection
- Fixed `generate_summary()` receiving enum object instead of string — summaries were all falling back to generic text
- Fixed timezone mismatch in RunArchiver — `datetime.now(timezone.utc)` (aware) vs Postgres `func.now()` (naive) caused CSV exports to show 0 rows
- Fixed `generate_summaries_batch` JSONB filter — double-negative `not_(metadata_["plain_summary"].isnot(None))` was skipping extractions that needed summaries
- Fixed Alembic migration `create_type=False` syntax error for newer SQLAlchemy/Python versions

### New Features (current session)
- **Failed extraction tracking** — New `failed_extraction_attempts` table records every LLM and DB failure with agent name, error type, and retry state
- **Retry mechanism** — `run_retry_failed()` re-runs only the specific agent+passage pairs that failed. Dashboard endpoint + button.
- **DB errors feed circuit breaker** — Previously only LLM failures triggered it. Now DB insert errors (enum, FK) also count toward the 3-consecutive threshold.
- **Retag endpoint** — `POST /dashboard/review/{queue_id}/retag` allows changing extraction_type from the review UI. Dropdown added to every review row.
- **Two-step sync** — Dashboard now has Step 5 (Local -> Regs Checker Supabase) and Step 6 (Regs Checker -> Policy Navigator) as separate steps with separate endpoints.
- **JSON truncation repair** — `_repair_truncated_json()` salvages partial LLM output by finding the last complete array element and closing brackets.
- **Max tokens increased to 50k** — Both `extraction_max_tokens` and `local_extraction_max_tokens`.
- **Verification pass filtered to triaged passages only** — Cross-validation and gap detection no longer waste tokens on `not_relevant` passages.

### Infrastructure Fixes (current session)
- Rewrote `start.ps1` with pure ASCII for PowerShell 5.1 compatibility
- Pinned MinIO Docker images to specific release tags (`:latest` was returning 500 errors)
- Postgres starts independently from MinIO in startup script
- Rewrote `README.md` to match current 7-agent, local-LLM, 3-tier-DB architecture

## Test Coverage Audit (test-coverage agent — 2026-04-03)

### Audit & Gap Analysis
- Ran full test suite: 320 pass, 20 fail, 4 stale test files
- Produced `agents/test-coverage/test-audit.md` (full classification of every test)
- Produced `agents/test-coverage/test-gaps.md` (untested features prioritized by risk)
- Root cause of most failures: Orrick gate (7 tests), stale mocks (5 tests), DB required (7 tests)

### New Unit Tests (76 tests, all passing)
- `test_discriminate_extraction_type.py` — 25 tests for extraction type routing across all 7 agents
- `test_summary_generator.py` — 32 tests covering all 12 template-based summary types
- `test_repair_truncated_json.py` — 16 tests documenting both repair strategies and known limitations

### Fixed Existing Tests (7 fixed + 3 new Orrick gate tests)
- Fixed 4 failures in `test_confidence.py` — added mock Orrick data; replaced stale `test_weight_redistribution` with explicit gate test
- Fixed 3 failures in `test_verification_agents.py` — added mock Orrick data to CV integration tests
- Added `test_orrick_gate_forces_tier_d`, `test_low_orrick_score_limits_tier`, `test_no_orrick_data_flag`, `test_orrick_gate_overrides_cv`
- Suite: 403 pass, 13 fail (down from 320 pass, 20 fail)

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
- 7-agent pipeline with VRAM-efficient model grouping
- Orrick-gated 6-component confidence scoring (auto-Tier D without Orrick data)
- Abstraction presentation layer (deterministic template summaries)
- Run archiver (dated output folders per extraction run)
- Fixed Orrick metadata key mismatches in `_build_context()`
- Fixed reset cascade FK violation
- Fixed `v_state_ai_regulation_matrix` INNER->LEFT JOIN (48->172 rows)
