# Regs Checker — Handoff Document

## What This Is

Regs Checker is an AI-powered pipeline that extracts structured legal obligations from US state AI legislation. It discovers bills from the Orrick PDF tracker and IAPP, fetches the full text from primary legislature URLs, splits it into passages, runs extraction agents (obligations, definitions, thresholds, ambiguities), scores confidence, and pushes approved results to a Policy Navigator product database.

**Current state:** 180 laws ingested, 9,182 passages parsed, 28,885 extractions produced. Default model: `claude-haiku-4-5-20251001`. 100/100 unit tests passing.

---

## How to Run

### Prerequisites

- Docker & Docker Compose
- Python 3.11+
- An Anthropic API key (for extraction; discovery/fetch steps are free)

### 1. Start Infrastructure

```bash
cd docker && docker compose up -d
```

This starts:
| Service    | Port  | Purpose                              |
|------------|-------|--------------------------------------|
| PostgreSQL | 5432  | Application database                 |
| MinIO      | 9000  | S3-compatible artifact storage       |
| MinIO UI   | 9001  | MinIO admin console                  |
| FastAPI    | 8000  | Dashboard + API server               |
| Dagster    | 3000  | Orchestration UI (optional)          |

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env and set at minimum:
#   REGS_ANTHROPIC_API_KEY=sk-ant-...
#   REGS_DATABASE_URL=postgresql://regs:regs@localhost:5432/regs_checker
```

### 3. Install Dependencies

```bash
pip install -e ".[dev]"
```

### 4. Start the Dashboard

```bash
uvicorn src.api.app:app --reload
```

Open **http://localhost:8000/dashboard/** in your browser.

---

## What to Expect in the UI

The dashboard has three pages accessible via the top navigation tabs:

### Pipeline (`/dashboard/`)

This is the main control center. At the top you'll see:

- **Progress ring** — a circular SVG gauge showing overall pipeline completion as a percentage
- **Per-step progress bars** — each pipeline step (Discovery, Fetch & Parse, Extraction, Review, Sync) gets its own bar with completed/total counts
- **ETA** — estimated time remaining, computed from actual extraction job durations. Shows "Calculating..." until enough data exists to estimate a rate
- **Processing rate** — items/minute throughput once extraction jobs have run

Below the progress section are the **7 pipeline steps**, each with action buttons:

| Step | Name                    | Type      | What It Does                                              |
|------|-------------------------|-----------|-----------------------------------------------------------|
| 1    | Find New Laws           | Automated | Parses Orrick PDF + IAPP for new AI bills                 |
| 2    | Fetch & Parse           | Automated | Downloads PDFs/HTML, extracts text, splits into passages  |
| 3    | Prepare for Extraction  | Automated | Bundles passages into batch files in `export/`            |
| 4    | Extract with Claude     | Manual    | You paste batch files into Claude and save JSON results   |
| 5    | Import Results          | Automated | Validates JSON, verifies evidence, computes confidence    |
| 6    | Review Extractions      | Manual    | Approve/reject items, focus on Tier C and D               |
| 7    | Sync to Policy Nav      | Automated | Pushes approved extractions to the product database       |

**Automated** steps run with one click. **Manual** steps require your action (pasting into Claude or reviewing extractions).

There's also a collapsed "API Extraction" section at the bottom — this sends passages directly to the Anthropic API instead of manual copy/paste. Costs ~$0.02-0.05 per bill.

### Analytics (`/dashboard/analytics`)

Visual analysis of extraction quality:

- **Confidence distribution** — horizontal bar chart showing how many extractions fall into each tier:
  - **Tier A** (>=85%): auto-approve candidates
  - **Tier B** (>=70%): standard review
  - **Tier C** (>=50%): needs detailed review
  - **Tier D** (<50%): likely extraction failure
- **Score histogram** — 10-bucket distribution of raw confidence scores (0.0–1.0)
- **Extractions by type** — card grid showing counts per type (obligation, definition, threshold, exception, enforcement, timeline, framework_ref, ambiguity, actor_mapping)
- **Model comparison table** — if you've run extractions with multiple models, this shows side-by-side: count, avg confidence, tier breakdown, and % A+B quality
- **By jurisdiction** — bar chart of extractions per state with avg confidence
- **Run Evaluation** button — executes the gold-standard test harness (tests against annotated fixtures in `tests/fixtures/gold_standard/`)
- **Run Comparison** button — compares extraction quality across models

### Review (`/dashboard/review`)

The review queue with three tabs: Pending, Approved, Rejected.

Each row shows:
- **Tier badge** (A/B/C/D)
- **Extraction type** tag
- **Summary** of the extracted content
- **Document** (jurisdiction + bill citation)
- **Confidence breakdown** — 5 mini horizontal bars showing the individual components:
  - Schema validity (20% weight) — did Pydantic validation pass?
  - Evidence grounding (30%) — what % of fields have verified verbatim quotes?
  - Completeness (20%) — what % of optional fields are filled?
  - Source quality (15%) — parse quality from ingestion
  - Orrick alignment (15%) — token similarity vs Orrick's metadata
- **Model** — which model produced the extraction
- **Approve/Reject** buttons (on pending items)

A color-coded legend below the table explains the breakdown components.

---

## How Progress % and ETA Work

The overall progress percentage is a **weighted average** across pipeline steps:

| Step           | Weight |
|----------------|--------|
| Discovery      | 5%     |
| Fetch & Parse  | 10%    |
| Extraction     | 50%    |
| Review         | 30%    |
| Sync           | 5%     |

Extraction is weighted heaviest because it's the bottleneck.

The **ETA** is calculated by:
1. Looking at the last 10 completed `extraction_jobs` in the database
2. Computing actual items/minute from their `started_at` → `completed_at` durations
3. Dividing remaining unextracted passages by that rate
4. If no extraction jobs exist yet, ETA shows "Calculating..."

The progress section auto-refreshes every 10 seconds. The header stats bar refreshes every 15 seconds.

---

## CLI Alternative

Everything the UI does can also be run from the command line:

```bash
# Discovery
python -m src.scripts.seed_pipeline --mode pdf

# Fetch documents
python -m src.scripts.seed_pipeline --mode fetch --limit 10

# Export for manual extraction
python -m src.scripts.seed_pipeline --mode export-passages --limit 30

# Import Claude's JSON results
python -m src.scripts.seed_pipeline --mode import-extractions --input export/batch_001_results.json

# API extraction (paid)
python -m src.scripts.seed_pipeline --mode extract --limit 20

# Batch API (50% discount, 24h turnaround)
python -m src.scripts.seed_pipeline --mode extract --batch
python -m src.scripts.seed_pipeline --mode batch-results --batch-id msgbatch_01VGY...

# Evaluation
python -m src.scripts.seed_pipeline --mode evaluate

# Sync to Policy Navigator
python -m src.scripts.sync_extractions

# Run tests
pytest tests/
```

---

## API Endpoints

### Dashboard (HTML + HTMX)
- `GET /dashboard/` — Pipeline page
- `GET /dashboard/analytics` — Analytics page
- `GET /dashboard/review?status=pending&page=1` — Review queue

### Product API (JSON)
- `GET /v1/obligations?jurisdiction=CO&subject=AI&min_confidence=B` — Query obligations
- `GET /v1/obligations/{id}` — Single obligation
- `GET /v1/obligations/{id}/dependencies?max_depth=5` — Dependency tree
- `GET /v1/matrix?jurisdiction=CO` — Compliance matrix
- `GET /v1/changes?since=2024-01-01` — Change feed

### Internal API (JSON)
- `GET /internal/review/queue?status=pending` — Review queue items
- `POST /internal/review/queue/{id}/action` — Submit review decision

### Health
- `GET /health` — Returns `{"status": "healthy"}`
- `GET /docs` — OpenAPI documentation

---

## Architecture

```
User Browser
    │
    ├── /dashboard/ ──── HTMX ──── FastAPI (port 8000)
    │                                  │
    │                          ┌───────┴───────┐
    │                     PostgreSQL        MinIO (S3)
    │                     (15 tables)      (raw PDFs)
    │
    ├── /v1/ ──── JSON API ──── (same FastAPI app)
    │
    └── Dagster UI (port 3000, optional orchestration)
```

### Database Tables (15)
sources, document_families, document_versions, ingestion_jobs, raw_artifacts, normalized_source_records, extractions, extraction_jobs, review_queue, review_actions, legal_events, obligation_dependencies, applicability_conditions, api_keys, export_jobs

### Key Files
| File | Purpose |
|------|---------|
| `src/api/app.py` | FastAPI app entry point |
| `src/api/routes/dashboard.py` | Dashboard + pipeline API |
| `src/api/progress.py` | Progress tracking + ETA |
| `src/ingestion/extractor.py` | Multi-agent extraction pipeline |
| `src/core/confidence.py` | 5-component confidence scoring |
| `src/core/orrick_validation.py` | Orrick similarity scoring |
| `src/evaluation/harness.py` | Gold-standard evaluation |
| `src/scripts/seed_pipeline.py` | CLI orchestrator |
| `templates/dashboard.html` | Pipeline UI |
| `templates/analytics.html` | Analytics UI |
| `templates/review.html` | Review queue UI |
| `static/css/style.css` | All styling |

---

## Pending Database Migrations

Two Alembic migrations exist but may not yet be applied to your database. If you see errors about missing columns, run:

```bash
python -m alembic upgrade head
```

### Migration: `b7d4e1f3a502` — Manual review status + AI suggested URL
- Adds `requires_manual_review` value to the `ingestionstatus` enum
- Adds `ai_suggested_url` (Text, nullable) column to `ingestion_jobs` — set by the VerificationAgent when a fetch URL is stale

Without this migration, the "Fetch & Parse" step will fail with:
```
(psycopg2.errors.UndefinedColumn) column ingestion_jobs.ai_suggested_url does not exist
```

### Migration: `c9e2a4d5b703` — Structured URL columns
- Adds `primary_source_url` (Text, nullable) to `document_families`
- Adds `orrick_reference_url` (Text, nullable) to `document_families`
- Adds `iapp_reference_url` (Text, nullable) to `document_families`

---

## Known Issues Fixed

### `IngestionStatus` enum mismatch in progress tracking (fixed)
`src/api/progress.py` previously referenced `IngestionStatus.completed_with_warnings` and `IngestionStatus.running`, which do not exist in the enum. The actual enum values are: `pending`, `fetching`, `fetched`, `parsing`, `parsed`, `normalizing`, `completed`, `failed`, `requires_manual_review`. This caused a 500 error on `/dashboard/`. Fixed by using the correct enum values (`fetching`, `parsing`, `normalizing` for in-progress jobs).
