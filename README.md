# Regs Checker

AI-powered regulatory obligation extraction and compliance serving platform. Ingests legislative text from ~180 US state and federal AI laws, extracts structured obligations using 7 local LLM agents, validates against Orrick law firm data, and syncs results to the Policy Navigator product database.

## Current State

| Metric | Value |
|---|---|
| Laws in CSV | 243 |
| Extraction agents | 7 |
| Default extraction model | `openai/gpt-oss-20b` (GPT group) + `qwen3-...` (obligation) |
| LLM runtime | LM Studio (local, http://localhost:1234) |
| Local database | Docker Postgres on port 5434 |
| Dashboard | http://localhost:8000/dashboard |

## Architecture

```
  data/fact_laws.csv  +  output/law_sources/
         │
         ▼ local_ingest.py (seed + parse)
┌─────────────────────────────────────────────────────┐
│              Local Docker Postgres (port 5434)       │
│  sources → document_families → document_versions    │
│  → ingestion_jobs → raw_artifacts                   │
│  → normalized_source_records (passages)             │
│  → section_triage_results                           │
│  → extractions → review_queue                       │
└──────────────────────┬──────────────────────────────┘
                       │ sync_to_supabase.py
                       ▼
┌─────────────────────────────────────────────────────┐
│            Regs Checker Supabase (us-east-1)        │
│         wjxlimjpaijdogyrqtxc                        │
│         16 pipeline tables                          │
└──────────────────────┬──────────────────────────────┘
                       │ sync_extractions.py + rollup_matrix.py
                       ▼
┌─────────────────────────────────────────────────────┐
│          Policy Navigator Supabase (us-east-2)      │
│         aaxxunfarlhmydvohsrm                        │
│  synced_extractions → law_enforcement_details       │
│  → law_obligation_flags → law_triggering_thresholds │
│  → jurisdictional_conflicts                         │
│  → v_state_ai_regulation_matrix (view, 172+ rows)   │
└─────────────────────────────────────────────────────┘
```

## Quick Start

```powershell
# Start everything (Docker, migrations, browser, server)
python start.py
```

`start.py` handles: loading `.env`, testing DB connection, starting Docker if needed, running Alembic migrations, opening the browser, and launching uvicorn on port 8000.

Dashboard: **http://localhost:8000/dashboard**

## Pipeline Steps (in order)

Run each step from the dashboard UI, or via the CLI:

```powershell
# 1. Seed laws from CSV + ingest local source files
python -m src.scripts.seed_pipeline --mode seed-local

# 2. Triage passages for AI relevance
python -m src.scripts.seed_pipeline --mode triage

# 3. Run extraction agents against triaged passages
python -m src.scripts.seed_pipeline --mode extract

# 4. Review extractions at http://localhost:8000/dashboard/review

# 4.5 Generate plain-English summaries from verified payloads
python -m src.scripts.seed_pipeline --mode generate-summaries

# 5. Sync to Regs Checker Supabase
python -m src.scripts.sync_to_supabase

# 6. Sync to Policy Navigator Supabase
python -m src.scripts.sync_extractions

# 7. Roll up matrix detail tables
python -m src.scripts.rollup_matrix
```

## Project Structure

```
src/
  api/
    routes/
      dashboard.py        # HTMX pipeline dashboard + reset controls
      review_routes.py    # Review queue (approve / reject / revise)
      tracker_routes.py   # Orrick/IAPP tracker CRUD
      v1.py               # Product API (/v1/)
    app.py                # FastAPI app entry point
    progress.py           # Real-time SSE progress streaming
  agents/                 # 7 extraction agents
    base.py               #   BaseExtractionAgent: LLM call, retry, JSON repair,
                          #   evidence span verification, think-block stripping
    obligation.py         #   Obligations + timelines + enforcement (co-extraction)
    definition_actor.py   #   Definitions + actor mappings + framework refs
    threshold_exception.py#   Thresholds + exceptions + compute FLOPS
    ambiguity.py          #   Vague language + conflicting provisions
    rights_protection.py  #   Individual rights (opt-out, appeal, disclosure)
    compliance_mechanism.py#  Audits, bias testing, red teaming, NIST, reporting
    preemption.py         #   Federal preemption + Commerce Clause tensions
    section_triage.py     #   AI-relevance filter (keyword → Orrick → LLM)
  ingestion/
    local_ingest.py       # Seeds 243 laws from CSV; ingests local files
    parser.py             # HTML / PDF / TXT → passage chunking
    extractor.py          # Extraction orchestrator (triage, agents, confidence, archiver)
    pipeline.py           # End-to-end pipeline entry point
  schemas/
    extraction.py         # Pydantic v2 schemas for all 11 extraction payload types
    api.py                # API request/response schemas
  db/
    models.py             # 16 SQLAlchemy ORM tables + enums
    engine.py             # DB engine + session management
    views.py              # Materialized views
  core/
    confidence.py         # 6-component Orrick-gated confidence scoring
    orrick_validation.py  # Token Jaccard similarity vs Orrick metadata
    summary_generator.py  # Abstraction presentation layer (deterministic templates)
    run_archiver.py       # Dated output folders per extraction run
    payload_adapter.py    # Regs Checker → Policy Navigator payload format adapter
    config.py             # Settings (env vars, model config)
    bill_context.py       # Per-bill definitions + scope context injection
    circuit_breaker.py    # Abort extraction on consecutive LLM failures
  scripts/
    seed_pipeline.py      # Main CLI entry point (all pipeline modes)
    sync_to_supabase.py   # Local Docker → Regs Checker Supabase
    sync_extractions.py   # Regs Checker → Policy Navigator (incremental cursor)
    rollup_matrix.py      # Aggregate synced_extractions → 4 matrix detail tables
    sync_monitor.py       # Cross-database health monitor
prompts/                  # Versioned YAML prompt templates (one per agent)
data/                     # CSV law metadata (fact_laws.csv, dim tables)
output/
  law_sources/            # Pre-fetched source files (HTML, PDF, TXT)
  law_texts/              # Pre-extracted plain text fallback
  extraction_runs/        # Dated output folders: YYYY-MM-DD_HHMMSS_extract/
docker/                   # Docker Compose (Postgres + MinIO)
alembic/                  # Database migrations
_archived/                # Archived code (old fetchers, Anthropic provider, etc.)
```

## Extraction Agents

7 agents run against each triaged passage. Agents are grouped by model to minimize VRAM swaps in LM Studio:

| Agent | Extracts | Model Group |
|---|---|---|
| `obligation` | Obligations, timelines, enforcement | Qwen (~45s) |
| `definition_actor` | Definitions, actor mappings, framework refs | GPT-OSS 20B |
| `threshold_exception` | Thresholds, exceptions, compute FLOPS, sectors | GPT-OSS 20B |
| `ambiguity` | Vague terms, conflicting provisions | GPT-OSS 20B |
| `rights_protection` | Individual rights (opt-out, appeal, disclosure) | GPT-OSS 20B |
| `compliance_mechanism` | Audits, bias testing, red teaming, NIST, reporting | GPT-OSS 20B |
| `preemption` | Federal preemption, Commerce Clause tensions | GPT-OSS 20B |

**Agent selection:** All agents run by default (recall-safe). Only definitive boilerplate (TOC, enacting clauses, separator lines) skips all agents. The LLM abstains on passages where it finds nothing relevant.

**Multi-extraction:** Each agent can return multiple items per passage (wrapped in an `"extractions": [...]` array).

**JSON repair:** The base agent handles three common local-LLM JSON defects: trailing commas, concatenated objects, and double-encoded array items.

## Confidence Scoring

Each extraction receives a confidence score (0.0–1.0) and tier (A/B/C/D) from 6 components:

| Component | Weight | Notes |
|---|---|---|
| Schema validity | 10% | Pydantic v2 validation pass/fail |
| Evidence grounding | 20% | Proportion of evidence spans verified by string match |
| Completeness | 10% | Proportion of optional fields populated |
| Source quality | 5% | Parse quality score from ingestion |
| **Orrick alignment** | **30%** | Token Jaccard similarity vs Orrick key_requirements + enforcement |
| Cross-validation | 25% | Post-extraction accuracy score (redistributed if not yet run) |

**Orrick Gate:** If no Orrick data exists for the law, the extraction is automatically **Tier D** regardless of other scores. Only law-firm-validated extractions can reach Tiers A/B/C.

| Tier | Score | Meaning |
|---|---|---|
| A | ≥ 0.85 | Auto-approve candidate |
| B | ≥ 0.70 | Standard review |
| C | ≥ 0.50 | Detailed review required |
| D | < 0.50 or no Orrick data | Human review required |

## Abstraction Presentation Layer

Extraction payloads contain deterministic data (booleans, integers, verbatim quotes). The `summary_generator.py` module converts these into plain-English summaries **after** extraction for UI display only.

- The raw payload is always the authoritative data source.
- Summaries are stored in `extraction.metadata_["plain_summary"]`.
- Summaries are generated automatically during extraction and available via the "Generate Summaries" step.

## Run Archiver

Each extraction run creates a timestamped folder under `output/extraction_runs/`:

```
output/extraction_runs/
  2026-04-03_143022_extract/
    run_summary.json     — timing, counts, token usage
    extractions.csv      — full export of all extractions from this run
    agent_stats.json     — per-agent performance breakdown
```

## Sync Pipeline

### Local → Regs Checker Supabase
`sync_to_supabase.py` — copies pipeline data from local Docker Postgres to the Regs Checker Supabase project.

### Regs Checker → Policy Navigator
`sync_extractions.py` — incremental cursor sync. Uses `law_document_bridge` in Policy Navigator to map `document_family_id` → `law_id`. Idempotent upserts via `ON CONFLICT DO NOTHING`.

`rollup_matrix.py` — aggregates `synced_extractions` into 4 matrix detail tables:
- `law_enforcement_details` — private right of action, max penalty, cure period
- `law_obligation_flags` — bias testing, red teaming, NIST, audits, transparency, reporting
- `law_triggering_thresholds` — compute FLOPS, sectors, exemptions
- `jurisdictional_conflicts` — preemption signals and conflict types

The `v_state_ai_regulation_matrix` view in Policy Navigator assembles all of the above into a single queryable matrix (172+ rows as of last run).

## Environment Variables

| Variable | Purpose |
|---|---|
| `REGS_DATABASE_URL` | Local Docker Postgres (`postgresql://regs:regs@127.0.0.1:5434/regs_checker`) |
| `REGS_SUPABASE_URL` | Regs Checker Supabase connection string |
| `REGS_POLICY_NAVIGATOR_URL` | Policy Navigator Supabase connection string |
| `LM_STUDIO_URL` | Local LLM endpoint (default: `http://localhost:1234`) |
| `REGS_API_PORT` | Dashboard port (default: `8000`) |

## Design Principles

- **Immutability-first:** Raw artifacts are content-addressable (SHA-256). Never overwrite.
- **Evidence spans on every field:** All extracted fields require a verbatim quote from the source passage, verified by string matching.
- **Abstention as first-class output:** Agents return `detected: false` rather than hallucinating. No gap-filling.
- **Orrick-gated confidence:** Law-firm validation is required for production tiers. Unvalidated extractions are always Tier D.
- **Lossless extraction, lossy presentation:** The pipeline extracts strict booleans/integers/quotes. Plain-English summaries are generated separately and never feed back into downstream systems.
- **Recall over precision for agent selection:** All agents run by default. False positives (abstentions) are cheap. False negatives (missed obligations) are not acceptable for audit-grade work.
- **VRAM-efficient local inference:** Agents are grouped by model so LM Studio loads each model once per passage batch, not once per agent call.

## Archive

- `_archived/` — Archived ingestion connectors, web scrapers, AnthropicProvider
- `src/ingestion/_archived/` — Old URL-fetching pipeline (replaced by local_ingest.py)
- `archive/` — Historical planning documents (Initial Project Plan, LEADERSHIP_STRUCTURE, original HANDOFF)
