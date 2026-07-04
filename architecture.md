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

#### Passage-level agents (6 agents per triaged passage)

Signal-based routing (`src/ingestion/routing.py: route_by_signal()`, wrapped by `extractor.py: _route_agents_by_signal()`) checks each passage for keyword signals and skips agents unlikely to find content. **Falls back to running all agents only when *zero* signals fire**, or when signals cover ≥ (n-1) agents (ambiguous/dense passage) — a passage with exactly one matching signal routes to only that agent. (EA0-2, 2026-07-03: this doc previously said "fewer than 2 signals" — that was never true of the code; the ≥1-signal threshold is deliberate and pinned by `tests/unit/test_routing_recall.py`.) The `triage_recall_sample_rate` setting (`src/core/config.py`, default 0.05) is the compensating control: that fraction of passages bypass routing entirely and run all agents, so single-signal false-narrowing can be measured over time rather than going undetected. Tuning the signal threshold itself is gated on the EA1 gold-standard eval set (see `tasks.md`) so any change is measured against real recall/precision, not guessed. All agents use `google/gemma-4-26b-a4b` via LM Studio; token budget is doubled at call time to reserve half for Gemma's `<think>` blocks. Configured `max_tokens` in `config/agent_models.json` are pre-doubling values.

| Agent | Extracts |
|---|---|
| `obligation` | Obligations, timelines, enforcement, safe harbor, consent requirements, `interpretation_risks` |
| `definition_actor` | Definitions, actor mappings, framework refs |
| `threshold_exception` | Scope/temporal/exemption thresholds; typed numeric fields (revenue, employees, consumer data) |
| `rights_protection` | Individual rights (opt-out, appeal, disclosure); protected categories; `interpretation_risks` |
| `compliance_mechanism` | Audits, bias testing, red teaming, NIST alignment, reporting, data retention |
| `preemption` | Federal preemption signals, Commerce Clause tensions, cross-law references |

**Ambiguity agent retired (Phase 1B):** The standalone `ambiguity` agent no longer runs. Ambiguity findings are embedded as `interpretation_risks: list[InterpretationRisk]` directly on `ObligationPayload` and `RightsProtectionPayload` — zero extra LLM calls, findings attached to the obligation they affect. Its source was deleted with `src/ingestion/_archived/` (RC3-3; retrievable from git history). `ExtractionType.ambiguity` enum value kept read-only for existing DB rows.

**Bill enforcement context injection:** Before extraction, the obligation agent receives a `BILL ENFORCEMENT & PENALTIES` context block assembled from enforcement-pattern sections of the same bill (`src/core/bill_context.py`). Enables cross-section penalty attribution (penalty in §X attributed to obligation in §Y).

#### Bill-level agents (3 agents, once per law)

After all passage extraction for a document version, three agents run with the full bill text, producing one upserted row in `bill_level_extractions` per law. Solves cross-section context problems that per-passage agents cannot resolve.

| Agent | Output table | Extracts |
|---|---|---|
| `enforcement_agent` | `law_enforcement_details` | Enforcing body, max penalty, penalty unit, cure period, private right of action, criminal penalties |
| `applicability_agent` | `law_triggering_thresholds` | Covered entities/sectors, AI system types, size thresholds, geographic scope, key exemptions |
| `compliance_timeline_agent` | `law_obligation_flags` | Effective date, enforcement start, key deadlines, assessment frequency, response windows |

#### Per-extraction processing (all agents)
- Pydantic v2 schema validation of payload
- Unicode-normalized evidence span verification (verified spans gate confidence score)
- Orrick similarity scoring via token Jaccard
- 6-component confidence score → tier A/B/C/D
- Deterministic plain-English summary from `summary_generator.py`
- Adaptive token retry: if `stop_reason=length`, retries at 2× budget up to `local_extraction_max_tokens` cap
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
    -> bill_level_extractions  (one row per law per bill-level agent)
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
- **LM Studio** on localhost:1234 with `google/gemma-4-26b-a4b` loaded
- **Docker Desktop** for local Postgres (port 5434)
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
- **Gap**: Tests predate the current 6-agent + 3-bill-level-agent pipeline. No tests for: bill-level agents (enforcement, applicability, compliance_timeline), interpretation_risks embedding, signal-based routing, adaptive token retry, failed_extraction_attempts, retag endpoint, or JSON repair strategies 3–5.
