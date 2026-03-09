# regs-checker

AI Legal Corpus: Regulatory obligation extraction, analysis, and serving platform.

Extracts structured obligations from legislative text using LLM-powered agents,
with human-in-the-loop review and full provenance tracking.

## Architecture

Built on the simplified 3-phase architecture with 12 concrete optimizations:

- **4 consolidated extraction agents** (down from 9) — 85% fewer LLM calls per passage
- **Single PostgreSQL database** with recursive CTEs for graph queries (no Neo4j)
- **Unified FastAPI app** with `/internal/` review UI and `/v1/` product API
- **~15 core tables** with materialized views (down from ~35)
- **Simple temporal model** with append-only legal events
- **Dagster** for pipeline orchestration with asset-based lineage

## Project Structure

```
src/
  api/              # FastAPI app — /internal/ + /v1/ routes
    routes/
    middleware/
  agents/           # 4 consolidated extraction agents
  ingestion/        # Source connectors and document parsers
  schemas/          # Pydantic v2 models (extraction + API)
  db/               # SQLAlchemy models, views, engine
  evaluation/       # Gold-standard evaluation harness
  dagster_pipelines/# Dagster asset definitions
  core/             # Config, confidence scoring
tests/
  fixtures/gold_standard/  # Annotated test cases
docker/             # Docker Compose + Dockerfile
alembic/            # Database migrations
prompts/            # Versioned prompt templates
```

## Quick Start

```bash
# Start infrastructure
cd docker && docker compose up -d

# Run API server
uvicorn src.api.app:app --reload

# Run Dagster UI
dagster dev -m src.dagster_pipelines.definitions

# Run tests
pytest tests/
```

## Jurisdictions

Launch with 2 jurisdictions (Recommendation #9):
1. **Colorado** — SB205 (most mature U.S. state AI law)
2. **Federal** — NIST AI RMF / Executive Orders

California added as third jurisdiction after pipeline validation.

## Key Design Principles (Non-Negotiable)

- Immutability-first for raw artifacts
- Evidence spans on every extracted field
- Abstention as first-class output (no hallucinated gap-filling)
- Confidence tiering with human review routing
- Full provenance chain from served obligation to source passage
- Content-addressable artifact storage (SHA-256)
- Pydantic v2 strict mode validation on all outputs
