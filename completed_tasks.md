# Regs Checker — Completed Tasks

## Recently Completed (current session — still matters for reasoning)

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

### New Unit Tests (73 tests, all passing)
- `test_discriminate_extraction_type.py` — 25 tests for extraction type routing across all 7 agents
- `test_summary_generator.py` — 32 tests covering all 12 template-based summary types
- `test_repair_truncated_json.py` — 16 tests documenting both repair strategies and known limitations

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
