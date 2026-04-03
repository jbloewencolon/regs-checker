# Regs Checker — Architecture

## What This System Does

Extracts structured legal obligations from ~180 US state and federal AI laws using local LLM agents, validates against Orrick law firm reference data, and syncs results to a downstream product database (Policy Navigator).

## Components

### 1. Ingestion (`src/ingestion/local_ingest.py`)
- Seeds 243 laws from `data/fact_laws.csv`
- Reads pre-fetched source files from `output/law_sources/` (HTML, PDF, TXT)
- Stores content-addressable raw artifacts in S3/MinIO
- Parses into passage-level `normalized_source_records` via `parser.py`

### 2. Triage (`src/agents/section_triage.py`, called from `extractor.py:run_triage`)
- 3-layer filter: keyword matching -> Orrick cross-check -> LLM generic
- Marks each passage as `relevant`, `not_relevant`, or `uncertain`
- Results stored in `section_triage_results` table
- Only `relevant` + `uncertain` passages proceed to extraction

### 3. Extraction (`src/ingestion/extractor.py`)
- 7 agents run against each triaged passage:
  - `obligation` (Qwen model) — obligations, timelines, enforcement
  - `definition_actor` (GPT-OSS 20B) — definitions, actors, framework refs
  - `threshold_exception` (GPT-OSS 20B) — thresholds, exceptions, compute FLOPS
  - `ambiguity` (GPT-OSS 20B) — vague terms, conflicting provisions
  - `rights_protection` (GPT-OSS 20B) — individual rights
  - `compliance_mechanism` (GPT-OSS 20B) — audits, bias testing, reporting
  - `preemption` (GPT-OSS 20B) — federal preemption, Commerce Clause tensions
- Agents grouped by model to minimize VRAM swaps in LM Studio
- Each extraction gets: Pydantic validation, evidence span verification, Orrick similarity scoring, 6-component confidence score, plain-English summary
- Failed attempts tracked in `failed_extraction_attempts` for retry

### 4. Confidence Scoring (`src/core/confidence.py`)
- 6 weighted components: Schema (10%), Evidence (20%), Completeness (10%), Source Quality (5%), Orrick Alignment (30%), Cross-Validation (25%)
- **Orrick Gate**: No Orrick data = automatic Tier D. Non-negotiable.
- Tiers: A >= 0.85, B >= 0.70, C >= 0.50, D < 0.50

### 5. Review (`src/api/routes/review_routes.py`)
- HTMX-powered review queue with approve/reject/retag
- Confidence component visualization
- Plain-English summaries from `summary_generator.py`

### 6. Verification (`src/agents/cross_validation.py`, `gap_detector.py`)
- Post-extraction pass using GPT for independent model diversity
- Cross-validation: checks extraction accuracy against source passage
- Gap detection: finds missed obligations
- Only runs on triaged-relevant passages

### 7. Sync Pipeline
- **Leg 1**: Local Docker Postgres -> Regs Checker Supabase (`src/scripts/sync_to_supabase.py`)
- **Leg 2**: Regs Checker Supabase -> Policy Navigator Supabase (`src/scripts/sync_extractions.py`)
- **Rollup**: Aggregates synced_extractions into matrix detail tables (`src/scripts/rollup_matrix.py`)
- `payload_adapter.py` translates between Regs Checker and Policy Navigator schemas

### 8. Dashboard (`src/api/routes/dashboard.py`)
- FastAPI + HTMX, Jinja2 templates
- Pipeline steps 1-6 all triggerable from UI
- Real-time extraction progress via SSE
- Reset handlers for each pipeline step

## Data Flow

```
CSV + local files
    |
    v
[Local Docker Postgres :5434]
    sources -> document_families -> document_versions
    -> ingestion_jobs -> raw_artifacts
    -> normalized_source_records (passages)
    -> section_triage_results
    -> extractions + review_queue
    -> failed_extraction_attempts
    |
    v (sync_to_supabase.py)
[Regs Checker Supabase - wjxlimjpaijdogyrqtxc]
    |
    v (sync_extractions.py + rollup_matrix.py)
[Policy Navigator Supabase - aaxxunfarlhmydvohsrm]
    synced_extractions
    -> law_enforcement_details
    -> law_obligation_flags
    -> law_triggering_thresholds
    -> jurisdictional_conflicts
    -> v_state_ai_regulation_matrix (SQL view)
```

## Key Dependencies

- **Python 3.11+**, FastAPI, SQLAlchemy 2.0, Pydantic v2, Alembic
- **LM Studio** on localhost:1234 with GPT-OSS 20B loaded
- **Docker Desktop** for local Postgres (port 5434) + MinIO
- **Supabase** (2 projects: Regs Checker us-east-1, Policy Navigator us-east-2)

## Known Hacks / Fragile Areas

- **`_ensure_extraction_enums()`** — Auto-adds missing Postgres enum values at extraction start using raw psycopg2 autocommit. Needed because Alembic migrations may not have run on local DB.
- **`_ensure_triage_table()`** — Auto-creates `section_triage_results` table via raw SQL if Alembic migration missing.
- **`_ensure_failed_attempts_table()`** — Same pattern for `failed_extraction_attempts`.
- **`extractor.py` is ~2600 lines** — The largest and most critical file. Houses triage, extraction, retry, verification, dependency graph, and condition parsing. High change risk.
- **MinIO often fails to start** — Docker pulls for MinIO images intermittently 500. Pipeline works without it. Startup script starts MinIO in background non-blocking.
- **Supabase projects may be paused** — When unreachable, sync steps fail. Always test with dry-run first.
- **Orrick metadata key names** — Were silently wrong for months (`"enforcement"` vs `"enforcement_penalties"`). Fixed but illustrates fragility of string-key-based metadata passing.

## Test Infrastructure

- 23 unit test files in `tests/unit/`
- 2 integration test files in `tests/integration/`
- Run: `pytest tests/`
- Config: `pyproject.toml` `[tool.pytest.ini_options]`
- **Gap**: Tests predate 7-agent pipeline. No tests for preemption agent, failed_extraction_attempts, retag endpoint, retry mechanism, or JSON truncation repair.
