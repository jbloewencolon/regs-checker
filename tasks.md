# Regs Checker — Tasks

## Active Tasks

- **Phase 6 — Full reset + re-seed + ingest + triage + extract + sync (READY TO EXECUTE)**
  - Pre-flight done: smart routing, title disambiguation, regulatory_category, 4 URL swaps, MN omnibus trim.
  - User runs: `python scripts/reset_pipeline.py`, then dashboard Steps 1→2→3→5 (`--clear`).
  - 16 laws with still-quarantined source text will be skipped on re-ingest (see `output/law_texts_quarantine/NEEDED_SOURCES.md`).

- **Obtain correct source text for 16 quarantined laws** — See `output/law_texts_quarantine/NEEDED_SOURCES.md`. Place correct bill text in `output/law_texts/<canonical_law_id>.txt`.

- **TN quarantine files contain TX bill content** — TX SB 1188, SB 2373, SB 815, SB 20, SB 1621 may be legitimate TX AI laws. Decide whether to add as new TX entries in `fact_laws.csv`.

- **Merge feature branch to main** — All work on `claude/setup-project-scaffolding-9ApZR`. Needs review and merge after Phase 6 validation.

## Recently Completed

### Triage Switched to Qwen2.5-3B-Instruct — 2026-04-04
- Root cause: GPT-OSS 20B is a reasoning model — burns all tokens on `<think>` blocks even for simple binary classification
- `config.py`: Added `local_triage_model = "qwen2.5-3b-instruct"` config key (overridable via `REGS_LOCAL_TRIAGE_MODEL` env var)
- `section_triage.py`: LLM call now uses `model_override=settings.local_triage_model` (removed `reasoning_effort="low"` — not needed for non-reasoning model)
- Removed `reasoning_effort="low"` workaround — Qwen2.5-3B-Instruct doesn't do chain-of-thought
- **Files modified**: `src/core/config.py`, `src/agents/section_triage.py`

### Passage Explosion Fixed (14,968 → ~1,300 passages) — 2026-04-04
- Removed sub-section markers `(a)`, `(b)`, `(1)` from section regex in `parser.py`
- `_split_on_paragraphs()` rewritten with chunk merging (TARGET=3k, MAX=15k chars)
- `_segment_text()` also merges small adjacent section matches (TARGET=3k chars)
- **File modified**: `src/ingestion/parser.py`

### Triage Error Visibility in Dashboard — 2026-04-04
- Added `GET /dashboard/api/triage-results` endpoint showing decision/method breakdown
- LLM failures (method=passthrough) shown first with red rows + count badge
- Quality/confidence issues shown in separate table
- **File modified**: `src/api/routes/dashboard.py`, `templates/dashboard.html`

### S3/MinIO Bypass for Local Ingestion — 2026-04-04
- `local_ingest.py` now stores `local://` reference instead of uploading to MinIO
- Passes `content_bytes` directly to parser (no S3 round-trip)
- `parser.py`: `parse_and_normalize()` accepts `content_bytes: bytes | None = None`
- Fixed `fetch_started_at` / `fetch_completed_at` always NULL for local files
- **Files modified**: `src/ingestion/local_ingest.py`, `src/ingestion/parser.py`

### Law Tracker Rewired to data/fact_laws.csv (241 laws) — 2026-04-04
- Replaced stale `static/ai_law_tracker.csv` (191 rows) with `data/fact_laws.csv` (241 laws)
- Updated tracker headers: Jurisdiction, Title, Bill#, AI Scope, Eff. Date, Status, Source, URL
- **Files modified**: `src/api/routes/tracker_routes.py`, `src/api/routes/_dashboard_helpers.py`, `templates/dashboard.html`

### Pipeline Reset Script — 2026-04-04
- `scripts/reset_pipeline.py`: FK-safe reset using savepoints; added `legal_events` before `document_versions`
- **File modified**: `scripts/reset_pipeline.py`

### LLM Limits Maxed for GPT-OSS 20B (128k context) — 2026-04-04
- `config.py`: context window 32k→128k, extraction max_tokens 50k→65k
- `llm_provider.py`: default max_tokens 4k/8k→16k
- `parser.py`: paragraph oversplit 2k→15k chars
- `bill_context.py`: definitions 3k→30k, scope 2k→20k, structure 500→5k
- `section_triage.py`: definitions 2k→30k, scope 1.5k→20k, structure 500→5k, neighbors 300→3k, triage max_tokens 8k→16k

### Bug Sweep (4 fixes) — 2026-04-04
- `local_ingest.py`: Added `iapp_scope`/`iapp_section` to family metadata (was silently dropped)
- `confidence.py`: Fixed empty strings counting as "filled" in completeness scoring
- `dashboard.py`: Fixed `reset_fetch_all` missing ExtractionJob/FailedExtractionAttempt cascade delete
- `fact_laws.csv`: Fixed law_id=143 missing source_id (set to "1" Orrick)
- Created `scripts/reset_pipeline.py` — FK-safe full pipeline reset with verification + sequence reset

### Data Alignment Complete — 2026-04-04
- CSV deduplicated: 244→241 rows (merged 4 IAPP→Orrick duplicates)
- 187 Orrick titles corrected from legacy DB via fuzzy matching
- 87 bill numbers recovered from old corrupted titles
- `iapp_scope` and `iapp_section` columns added to fact_laws.csv

## Bugs / Issues

### BUG-1: Laws missing Orrick data → auto Tier D — ACCEPTED
Only 2 Orrick laws + 53 IAPP active bills lack Orrick data. The 53 IAPP bills are pending legislation — the Orrick gate legitimately flags them. Accept Tier D for these.

### BUG-2: Failed extraction retry — FIXED
### BUG-3: Supabase sync "not configured" — FIXED

## Next Tasks (after extraction completes)

- **Sync local → Supabase** — Dashboard Step 5. Supabase truncated 2026-04-04.
- **Sync Regs Checker → Policy Navigator** — Dashboard Step 6.
- **Run rollup matrix** — `python -m src.scripts.rollup_matrix`
- **Review test coverage** — 403 pass, 13 fail. 7 DB-required, 5 stale mocks, 1 stale ref.
- **Write handoff document** — HANDOFF_DOCUMENT.md for CS undergrad audience.

## Blocked Tasks
- **Cross-validation scoring** — Needs verification pass after extraction.

## Questions / Clarifications Needed
- Target extraction count? Previous run: ~28k from ~9k passages.
- Sync to Policy Navigator: all types or approved-only?
- Is MinIO/S3 actually needed? Pipeline works without it.
