# Regs Checker

AI-powered regulatory obligation extraction and compliance serving platform. Ingests legislative text from 180 laws across U.S. jurisdictions, extracts structured obligations using LLM agents, and syncs results to the Policy Navigator product database.

## Current State

| Metric | Value |
|---|---|
| Laws ingested | 180 |
| Passages parsed | 9,182 |
| Extractions produced | 28,885 |
| Extractions synced to Policy Navigator | 28,885 |
| Default extraction model | `claude-haiku-4-5-20251001` |
| Unit tests | 100/100 |

## Architecture

```
                    Orrick AI Law Tracker
                           │
                      (scrape + fetch)
                           ▼
┌─────────────────────────────────────────────────┐
│              Local Docker Postgres               │
│  sources → document_families → document_versions │
│  → raw_artifacts → normalized_source_records     │
│  → extractions → review_queue                    │
└──────────────────────┬──────────────────────────┘
                       │ sync_to_supabase.py
                       ▼
┌─────────────────────────────────────────────────┐
│            Regs Checker Supabase                 │
│         (15 pipeline tables, 28,885 extractions) │
└──────────────────────┬──────────────────────────┘
                       │ sync_extractions.py
                       ▼
┌─────────────────────────────────────────────────┐
│          Policy Navigator Supabase               │
│    (synced_extractions via law_document_bridge)   │
└─────────────────────────────────────────────────┘

           sync_monitor.py watches both ▲
```

## Project Structure

```
src/
  api/                  # FastAPI app — /internal/ review + /v1/ product API
    routes/
    middleware/
  agents/               # 4 consolidated extraction agents
    base.py             #   Base agent: LLM calling, retry, JSON parsing, evidence verification
    obligation.py       #   Obligation + timeline + enforcement co-extraction
    definition_actor.py #   Definition + actor mapping + framework reference co-extraction
    threshold_exception.py # Threshold + exception co-extraction
    ambiguity.py        #   Vague/ambiguous language detection
  ingestion/            # Source connectors, parsers, extraction pipeline
    orrick_scraper.py   #   Orrick AI Law Tracker scraper
    connector.py        #   Document fetching with retry + fallback
    parser.py           #   PDF/HTML → passage chunking
    extractor.py        #   Extraction orchestrator (filtering, merging, batch API)
    pipeline.py         #   End-to-end ingestion pipeline
  schemas/              # Pydantic v2 models
    extraction.py       #   Extraction output schemas (per-agent type validation)
    api.py              #   API request/response schemas
  db/                   # SQLAlchemy ORM
    models.py           #   15 core tables
    views.py            #   Materialized views + recursive CTEs
    engine.py           #   Database engine + session management
  evaluation/           # Gold-standard evaluation harness
  dagster_pipelines/    # Dagster asset definitions + scheduling
  scripts/              # CLI entry points
    seed_pipeline.py    #   Main CLI (ingest, extract, recover, batch)
    sync_to_supabase.py #   Local Docker → Regs Checker Supabase
    sync_extractions.py #   Regs Checker → Policy Navigator sync
    sync_monitor.py     #   Cross-database health monitor
  core/
    config.py           #   Settings (env vars, model config)
    confidence.py       #   4-component confidence scoring
tests/
  unit/                 # 100 unit tests
  integration/          # E2E pipeline + API tests
  fixtures/gold_standard/  # 13 annotated test cases
prompts/                # Versioned YAML prompt templates
docker/                 # Docker Compose + Dockerfile
alembic/                # Database migrations
archive/                # Historical planning documents
```

## Quick Start

```bash
# Start infrastructure
cd docker && docker compose up -d

# Run the full pipeline (ingest → parse → extract)
python -m src.scripts.seed_pipeline --mode orrick      # Scrape Orrick tracker
python -m src.scripts.seed_pipeline --mode fetch        # Fetch document text
python -m src.scripts.seed_pipeline --mode extract      # Run extraction agents

# Or use Batch API for 50% cost discount (results in ~24h)
python -m src.scripts.seed_pipeline --mode extract --batch
python -m src.scripts.seed_pipeline --mode batch-results --batch-id <id>

# Recover partial extractions (after Pydantic fixes)
python -m src.scripts.seed_pipeline --mode recover

# Sync to Supabase
python -m src.scripts.sync_to_supabase                 # Local → Regs Checker
python -m src.scripts.sync_extractions                  # Regs Checker → Policy Navigator
python -m src.scripts.sync_monitor                      # Health check both databases

# Run API server
uvicorn src.api.app:app --reload

# Run Dagster UI
dagster dev -m src.dagster_pipelines.definitions

# Run tests
pytest tests/
```

## Environment Variables

| Variable | Purpose |
|---|---|
| `REGS_DATABASE_URL` | Local Docker Postgres (development) |
| `REGS_SUPABASE_URL` | Regs Checker Supabase instance |
| `REGS_POLICY_NAVIGATOR_URL` | Policy Navigator Supabase instance |
| `REGS_ANTHROPIC_API_KEY` | Claude API access for extraction agents |
| `REGS_EXTRACTION_MODEL` | Override default model (default: `claude-haiku-4-5-20251001`) |

## Extraction Pipeline

**4 consolidated agents** (down from 9 in the original design) each make a single LLM call per passage:

| Agent | Extracts | Keyword Trigger |
|---|---|---|
| Obligation | obligations, timelines, enforcement | `shall`, `must`, `may not`, `prohibited`, `required` |
| Definition & Actor | definitions, actor mappings, framework refs | `means`, `defined as`, `shall mean`, `includes` |
| Threshold & Exception | thresholds, exceptions, carve-outs | numbers, dates, `unless`, `except`, `exempt` |
| Ambiguity | vague terms, conflicting provisions | Always runs |

**Cost optimizations** bring full-corpus extraction from ~$150 to ~$3-4:
- Skip passages under 150 chars (boilerplate)
- Merge adjacent short passages from same section
- Keyword-based agent selection (skip irrelevant agents)
- Default to Haiku model (20x cheaper than Sonnet)
- Batch API (50% discount)
- Orrick metadata context injection

## Design Principles

- Immutability-first for raw artifacts (content-addressable, SHA-256)
- Evidence spans on every extracted field (verbatim source text)
- Abstention as first-class output (no hallucinated gap-filling)
- Confidence tiering (A/B/C/D) with human review routing
- Full provenance chain from served obligation to source passage
- Pydantic v2 strict mode validation on all outputs

## Archive

Historical planning documents are in `archive/`:
- `Initial Project Plan.md` — Original 12-recommendation simplification analysis
- `LEADERSHIP_STRUCTURE.md` — Team roles, ownership map, and execution sequence
- `HANDOFF.md` — Detailed engineering handoff with implementation notes
