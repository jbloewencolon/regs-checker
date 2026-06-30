# Regs Checker

AI-powered regulatory obligation extraction and compliance serving platform. Ingests legislative text from ~232 US state and federal AI laws, extracts structured obligations using local LLM agents, validates against Orrick law firm data, and syncs results to the Policy Navigator product database.

## Current State

| Metric | Value |
|---|---|
| Laws in CSV | 232 |
| Passage-level agents | 6 (obligation, definition_actor, threshold_exception, rights_protection, compliance_mechanism, preemption) |
| Bill-level agents | 3 (enforcement, applicability, compliance_timeline) |
| Primary extraction model | `openai/gpt-oss-120b` via NVIDIA API (clause + bill-level agents) |
| Secondary extraction model | `meta/llama-3.1-8b-instruct` via NVIDIA API (triage, definition_actor, preemption) |
| LLM runtime | NVIDIA hosted API (`integrate.api.nvidia.com`) — requires `NVIDIA_API_KEY` |
| Fallback runtime | LM Studio (local, http://localhost:1234) — `provider: local` in `agent_models.json` |
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
│  → bill_level_extractions (one per law per agent)   │
│  → failed_extraction_attempts                       │
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

For a detailed setup guide see [`SETUP.md`](SETUP.md). For a quick-start returning developer path see [`QUICKSTART.md`](QUICKSTART.md).

Dashboard: **http://localhost:8000/dashboard**

## Pipeline Steps (in order)

Run each step from the dashboard UI, or via the CLI:

```powershell
# 1. Seed laws from CSV + ingest local source files
python -m src.scripts.seed_pipeline --mode seed-local

# 1b. (Optional) Enrich Orrick metadata for IAPP-only laws
python -m src.scripts.seed_pipeline --mode enrich-orrick

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
  agents/                 # Extraction agents
    base.py               #   BaseExtractionAgent: LLM call, retry, JSON repair (5 strategies),
                          #   evidence span verification, think-block stripping, Unicode norm.
    bill_level_base.py    #   BillLevelAgent: full-bill extraction base (one row per law)
    obligation.py         #   Obligations + timelines + enforcement + safe harbor + consent
    definition_actor.py   #   Definitions + actor mappings + framework refs
    threshold_exception.py#   Scope/temporal/exemption thresholds + typed numeric fields
    rights_protection.py  #   Individual rights (opt-out, appeal, disclosure, protected categories)
    compliance_mechanism.py#  Audits, bias testing, red teaming, NIST, reporting, retention
    preemption.py         #   Federal preemption + Commerce Clause + cross-law refs
    section_triage.py     #   AI-relevance filter (keyword → Orrick → LLM)
    enforcement_agent.py  #   Bill-level: penalties, enforcing body, PRA, cure period
    applicability_agent.py#   Bill-level: covered entities, sectors, size thresholds, exemptions
    compliance_timeline_agent.py # Bill-level: effective dates, deadlines, response windows
  ingestion/
    local_ingest.py       # Seeds 232 laws from CSV; ingests local files
    orrick_enrichment.py  # Two-phase Orrick metadata enrichment (backfill + LLM generate)
    parser.py             # HTML / PDF / TXT → passage chunking
    extractor.py          # Extraction orchestrator (triage, agents, confidence, archiver)
    pipeline.py           # End-to-end pipeline entry point
  schemas/
    extraction.py         # Pydantic v2 schemas for all extraction payload types
    api.py                # API request/response schemas
  db/
    models.py             # 18 SQLAlchemy ORM tables + enums
    engine.py             # DB engine + session management
    views.py              # Materialized views
  core/
    confidence.py         # 6-component Orrick-gated confidence scoring
    orrick_validation.py  # Token Jaccard similarity vs Orrick metadata
    summary_generator.py  # Abstraction presentation layer (deterministic templates)
    run_archiver.py       # Active session folder; archives on reset
    llm_provider.py       # LocalLLMProvider + NvidiaLLMProvider; provider selected by agent_models.json
    payload_adapter.py    # Regs Checker → Policy Navigator payload format adapter
    config.py             # Settings (env vars, model config)
    bill_context.py       # Per-bill definitions + scope + enforcement context injection
    circuit_breaker.py    # Abort extraction on consecutive LLM failures
    extraction_monitor.py # Per-agent stats: call count, success rate, avg duration
    model_config.py       # Load/reload agent model config from agent_models.json
  scripts/
    seed_pipeline.py      # Main CLI entry point (all pipeline modes)
    sync_to_supabase.py   # Local Docker → Regs Checker Supabase
    sync_extractions.py   # Regs Checker → Policy Navigator (incremental cursor)
    rollup_matrix.py      # Aggregate synced_extractions → 4 matrix detail tables
    sync_monitor.py       # Cross-database health monitor
prompts/                  # Versioned YAML prompt templates (one per agent)
config/
  agent_models.json       # Per-agent model, max_tokens, context_length, temperature
data/                     # CSV law metadata (fact_laws.csv, dim tables)
output/
  law_sources/            # Pre-fetched source files (HTML, PDF, TXT)
  law_texts/              # Pre-extracted plain text fallback
  law_texts_quarantine/   # Mismatched or problematic source files
  extraction_runs/
    active/               # Current session (rebuilt on each batch)
      run_summary.json
      extractions.csv
      low_confidence_extractions.csv   # Tier C/D extractions for offline review
      low_confidence_extractions.jsonl
      agent_stats.json
    2026-04-02_143022_extract/         # Archived previous session (after reset)
docker/                   # Docker Compose (Postgres)
alembic/                  # Database migrations
_archived/                # Archived code (old fetchers, Anthropic provider, etc.)
docs/                     # Strategy + phased plans (see Documentation below)
archive/                  # Historical planning docs + dated engineering handoffs
```

## Extraction Agents

### Passage-Level Agents (run once per triaged passage)

All 6 agents run against each AI-relevant passage via signal-based routing. Routing checks passage text for keyword signals and runs only the agents likely to find something, with a full-recall fallback when fewer than 2 signals match.

| Agent | Extracts |
|---|---|
| `obligation` | Obligations, timelines, enforcement, safe harbor, consent requirements |
| `definition_actor` | Definitions, actor mappings, framework refs |
| `threshold_exception` | Scope/temporal/exemption thresholds, typed numeric fields (revenue, employees, consumer data) |
| `rights_protection` | Individual rights (opt-out, appeal, disclosure), protected categories |
| `compliance_mechanism` | Audits, bias testing, red teaming, NIST, reporting, data retention |
| `preemption` | Federal preemption signals, Commerce Clause tensions, cross-law references |

**NVIDIA provider (active):** Heavy agents (`obligation`, `threshold_exception`, `compliance_mechanism`, `rights_protection`, bill-level agents) use `openai/gpt-oss-120b`. Lighter agents (`triage`, `definition_actor`, `preemption`) use `meta/llama-3.1-8b-instruct`. Configure via `config/agent_models.json` → `"provider": "nvidia"`.

**Local fallback:** Switching `"provider"` to `"local"` routes all agents through LM Studio at `http://localhost:1234`. Local config defaults to `google/gemma-4-26b-a4b`.

**Bill enforcement context:** The obligation agent receives a curated `BILL ENFORCEMENT & PENALTIES` context block assembled from enforcement-pattern sections of the same bill, enabling cross-section penalty attribution.

**Agent routing:** Signal-based routing (`_route_agents_by_signal()`) skips agents unlikely to find content in a passage. Falls back to running all agents when fewer than 2 signals fire or 5+ signals fire (catch-all).

**Multi-extraction:** Each agent can return multiple items per passage (wrapped in an `"extractions": [...]` array).

**JSON repair:** The base agent applies 5 sequential repair strategies on malformed LLM output:
1. Markdown fence stripping
2. Truncated JSON closure (string + bracket repair)
3. Concatenated-objects extraction
4. Escape sequence fixing
5. Key whitespace stripping (tab-prefixed keys)

### Bill-Level Agents (run once per law with full bill text)

Three agents run after all passage extraction for each law, producing one structured record per law that maps directly to product tables.

| Agent | Output table | Extracts |
|---|---|---|
| `enforcement_agent` | `law_enforcement_details` | Enforcing body, max penalty, penalty unit, cure period, PRA, criminal penalties |
| `applicability_agent` | `law_triggering_thresholds` | Covered entities/sectors, AI system types, size thresholds, geographic scope, exemptions |
| `compliance_timeline_agent` | `law_obligation_flags` | Effective date, enforcement start, key deadlines, assessment frequency, response windows |

Bill-level extractions upsert on `(document_version_id, agent_name)` — re-runs update in place.

## Confidence Scoring

Each extraction receives a confidence score (0.0–1.0) and tier (A/B/C/D). Active weighted signals:

| Component | Weight | Notes |
|---|---|---|
| **Orrick alignment** | **50%** | Token Jaccard similarity vs Orrick key_requirements + enforcement |
| **Evidence grounding** | **35%** | Proportion of evidence spans verified by 4-tier string match; broad-span penalty |
| Citation quality | 15% | Section reference specificity (subsection > § > generic) |
| Cross-validation | +10% (phases in) | Second-LLM CV score; scales base weights by 0.90 when present |

Diagnostic-only (not in weighted total): schema validity, completeness, source quality, source_grounding_score, tracker_alignment_score, schema_completeness_score.

**Orrick Gate:** If no Orrick data exists for the law, the extraction is automatically **Tier D** regardless of other scores. Use `--mode enrich-orrick` to generate Orrick summaries for IAPP-only laws and break this gate.

| Tier | Score | Meaning |
|---|---|---|
| A | ≥ 0.85 | Auto-approve candidate |
| B | ≥ 0.70 | Standard review |
| C | ≥ 0.50 | Detailed review required |
| D | < 0.50 or no Orrick data | Human review required |

**Low-confidence persistence:** At the end of every extraction run, Tier C and D extractions are written to `output/extraction_runs/active/low_confidence_extractions.{csv,jsonl}`. These files survive extraction resets (the active folder is archived to a timestamped copy before any reset).

## Abstraction Presentation Layer

Extraction payloads contain deterministic data (booleans, integers, verbatim quotes). The `summary_generator.py` module converts these into plain-English summaries **after** extraction for UI display only.

- The raw payload is always the authoritative data source.
- Summaries are stored in `extraction.metadata_["plain_summary"]`.
- Summaries are generated automatically during extraction and available via the "Generate Summaries" step.

## LLM Provider

Two providers are supported, selected by `"provider"` in `config/agent_models.json`:

### NVIDIA (active — `"provider": "nvidia"`)

Routes calls to `integrate.api.nvidia.com` via `NvidiaLLMProvider`. Requires `NVIDIA_API_KEY` in `.env`.

Key behaviors:
- **Per-agent model routing**: `openai/gpt-oss-120b` for complex agents; `meta/llama-3.1-8b-instruct` for lighter agents.
- **reasoning_effort coercion**: NVIDIA accepts `low`/`medium`/`high` only — `off`/`none`/`disabled` are coerced to `low` automatically.
- **Transport-error retry**: `httpx.TransportError` (RemoteProtocolError, ReadTimeout, ConnectError) retried with exponential backoff (1/2/4/8/16 s, 5 attempts).
- **429 rate-limit retry**: same backoff on quota exhaustion.
- **Think-block stripping**: removes `<think>...</think>` reasoning traces from output before JSON parsing (gpt-oss emits these).
- **Bare-array normalization**: top-level JSON arrays (from llama-3.1-8b) normalized to `{"extractions": [...]}` envelope.

### Local fallback (`"provider": "local"`)

Routes calls to LM Studio (or any OpenAI-compatible local server) at `REGS_LOCAL_LLM_URL` (default `http://localhost:1234`). Local config defaults to `google/gemma-4-26b-a4b`.

Key behaviors:
- **Token doubling** for reasoning models (Gemma, DeepSeek-R1, Qwen3, gpt-oss): sends `max_tokens × 2` to reserve half for `<think>` blocks.
- **Adaptive retry** on token exhaustion (`stop_reason="length"`): doubles budget up to `local_extraction_max_tokens` cap.
- **reasoning_effort caching**: caches models that reject the parameter (HTTP 400) so subsequent calls skip it.
- **Channel-thought recovery**: if Gemma emits `<|channel>thought` tokens that LM Studio can't parse (HTTP 400), extracts the JSON from the error body after the `<channel|>` marker.
- **Loop detection**: detects repetitive output and truncates at the third repetition; returns `stop_reason="loop"`.

Per-agent model, token budget, and temperature are configured in `config/agent_models.json` and hot-reloadable via the `/dashboard/models` page.

## Run Archiver

Extraction runs accumulate into a single `active` folder. A full reset archives the active folder to a timestamped copy first.

```
output/extraction_runs/
  active/                              ← current session (always up-to-date)
    run_summary.json                   — timing, counts, token usage
    extractions.csv                    — ALL extractions currently in DB
    low_confidence_extractions.csv     — Tier C/D for offline review
    low_confidence_extractions.jsonl   — same data, one JSON object per line
    agent_stats.json                   — per-agent performance breakdown
  2026-04-02_143022_extract/           ← archived previous session
    (same files, preserved at reset time)
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
| `NVIDIA_API_KEY` | API key for `integrate.api.nvidia.com` (required when `provider=nvidia`) |
| `REGS_EXTRACTION_PROVIDER` | `nvidia` (default) or `local` — selects provider at startup |
| `REGS_LOCAL_LLM_URL` | Local LLM endpoint for the `local` provider (default: `http://localhost:1234`) |
| `REGS_LOCAL_LLM_MODEL` | Default model for discovery tasks (local provider only) |
| `REGS_LOCAL_EXTRACTION_MODEL` | Base model for extraction tasks when using the local provider |
| `REGS_SUPABASE_URL` | Regs Checker Supabase connection string |
| `REGS_POLICY_NAVIGATOR_URL` | Policy Navigator Supabase connection string |
| `REGS_API_PORT` | Dashboard port (default: `8000`) |

See `SETUP.md` for the full `.env` template.

## Design Principles

- **Immutability-first:** Raw artifacts are content-addressable (SHA-256). Never overwrite.
- **Evidence spans on every field:** All extracted fields require a verbatim quote from the source passage, verified by string matching with Unicode normalization.
- **Abstention as first-class output:** Agents return `detected: false` rather than hallucinating. No gap-filling.
- **Orrick-gated confidence:** Law-firm validation is required for production tiers. Unvalidated extractions are always Tier D.
- **Lossless extraction, lossy presentation:** The pipeline extracts strict booleans/integers/quotes. Plain-English summaries are generated separately and never feed back into downstream systems.
- **Recall over precision for agent selection:** Signal-based routing skips clearly-irrelevant agents; falls back to all agents when uncertain. False positives (abstentions) are cheap. False negatives (missed obligations) are not acceptable for audit-grade work.
- **Product-table population:** Bill-level agents produce one structured record per law mapped directly to product tables (`law_enforcement_details`, `law_triggering_thresholds`, `law_obligation_flags`), enabling compliance decision support without cross-section reasoning at passage level.
- **Provider-switchable inference:** Extraction runs against either the NVIDIA hosted API (`gpt-oss-120b` + `llama-3.1-8b`) or a local OpenAI-compatible server (LM Studio/Ollama). Provider is set in `agent_models.json`; switching requires no code changes.

## Documentation

| Doc | Purpose |
|---|---|
| [`architecture.md`](architecture.md) | Reality-based system map: components, data flow, known hacks, fragile areas |
| [`docs/data_dictionary.md`](docs/data_dictionary.md) | Plain-English reference to every extracted field + the taxonomy (for business/product/review teams) |
| [`tasks.md`](tasks.md) | Active + upcoming work queue — the current source of truth for what's next |
| [`completed_tasks.md`](completed_tasks.md) | Log of recently completed work that still matters |
| [`SETUP.md`](SETUP.md) / [`QUICKSTART.md`](QUICKSTART.md) | Full setup guide / 2-minute returning-developer path |
| [`docs/taxonomy_strategy_summary.md`](docs/taxonomy_strategy_summary.md) | Taxonomy redesign — decisions log |
| [`docs/taxonomy_dev_plan.md`](docs/taxonomy_dev_plan.md) | Taxonomy redesign — phased dev plan (Phase 0 prerequisite + execution sequence) |
| [`docs/pipeline_rebuild_plan.md`](docs/pipeline_rebuild_plan.md) | Alternative path — gated ground-up rebuild proposal |

The taxonomy redesign and the pipeline rebuild are **mutually exclusive strategic paths**; see each plan's own framing for how they relate.

## Archive

- `_archived/` — Archived ingestion connectors, web scrapers, AnthropicProvider
- `src/ingestion/_archived/` — Old URL-fetching pipeline and retired ambiguity agent
- `archive/` — Historical planning documents and dated engineering handoffs
