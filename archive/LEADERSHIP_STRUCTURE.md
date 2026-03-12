# Leadership Structure & Developer Handoff Analysis

**Date:** March 2026
**Status:** Active

---

## Co-Equal Leadership Triad

| Role | Responsibility Domain | Decision Authority |
|------|----------------------|-------------------|
| **Legal Knowledge Architect** | Legal correctness, regulatory fidelity, evidence standards | Veto on extraction schema changes, gold-standard annotations, evidence span requirements |
| **Senior Data Platform Architect** | Technical architecture, data modeling, infrastructure | Veto on schema changes, infrastructure decisions, performance architecture |
| **Product / Technical Program Lead** | Execution sequencing, productization, stakeholder delivery | Veto on scope/timeline changes, phasing decisions, resource allocation |

All three leaders must concur on changes that cross domain boundaries (e.g., a schema change that affects legal fidelity, or a phasing decision that requires architectural rework).

---

## Team Roles & Ownership Map

| Role | Primary Ownership | Key Files |
|------|------------------|-----------|
| **Applied NLP / LLM Extraction Lead** | Extraction agents, prompt engineering, evaluation harness | `src/agents/*.py`, `src/schemas/extraction.py`, `src/evaluation/harness.py`, `src/core/confidence.py` |
| **Knowledge Graph / Semantic Systems Lead** | Dependency modeling, applicability conditions, graph queries | `src/db/views.py` (recursive CTEs), `obligation_dependencies` + `applicability_conditions` tables |
| **Regulatory / Policy Review Lead** | Gold-standard annotations, legal event modeling, jurisdiction validation | `tests/fixtures/gold_standard/*.json`, `legal_events` table, temporal status workflows |
| **DevOps / Platform Reliability Lead** | Docker, Dagster, CI/CD, database operations, monitoring | `docker/`, `docker/dagster.yaml`, `alembic/`, deployment infrastructure |
| **Backend / API Lead** | FastAPI routes, database engine, API schemas, auth middleware | `src/api/`, `src/db/engine.py`, `src/schemas/api.py`, `src/api/middleware/auth.py` |

---

## Repository Evaluation Summary

### What Exists (Completed)

- **15-table consolidated database schema** with full Alembic migration (`14c51c9b2e02`)
- **4 consolidated extraction agents** (obligation, definition_actor, threshold_exception, ambiguity) implementing Recommendations #1-#3
- **Single-pass extraction pipeline** — 1 LLM call per agent per passage (down from 27)
- **Evidence span verification** via string matching (replaces self-check LLM call)
- **Unified FastAPI application** with `/internal/` review UI + `/v1/` product API routes
- **Confidence scoring** — 4-component weighted model with A/B/C/D tiering
- **Evaluation harness** with precision/recall/F1 metrics and 2 gold-standard fixtures
- **Dagster pipeline definitions** for ingestion + extraction assets
- **Materialized view SQL** for served_obligations, current_active_obligations, compliance matrix
- **Recursive CTE** for obligation dependency traversal (PostgreSQL, no Neo4j)
- **Content-addressable artifact storage** with SHA-256 deduplication
- **Source connector framework** with registry pattern and Colorado + Federal NIST stubs

### What Needs Work (From Developer Handoff)

#### Phase 1: Fix Dagster (DevOps Lead — 1-2 hours)
- **Bug:** `docker/dagster.yaml` line 24+ contains Markdown prose (backtick fence) — truncate after line 23
- **Bug:** `docker/docker-compose.yml` dagster-webserver missing `DAGSTER_DATABASE_URL` env var
- **Owner:** DevOps / Platform Reliability Lead

#### Phase 2: Build LegiScan Ingestion Connector (Backend/API Lead — 3-5 days)
- Create `src/connectors/legiscan.py` using the existing `@register_connector` pattern
- Map LegiScan API to `sources` → `document_families` → `document_versions` → `raw_artifacts`
- Prioritize Colorado data to validate against existing gold-standard fixtures
- **Owner:** Backend / API Lead + Applied NLP Lead

#### Phase 3: Wire End-to-End Extraction Pipeline (Applied NLP Lead — 2-3 days)
- Test full pipeline: ingest → parse → extract → confidence score → review queue
- Expand gold-standard from 2 to 20 test cases (Colorado SB205)
- Tune extraction prompts against evaluation harness
- **Owner:** Applied NLP Lead + Regulatory Review Lead (annotations)

#### Phase 4: Dashboard (Deferred)
- Use Supabase Studio as stopgap for project owner
- HTMX templates for `/internal/` review routes are not yet written
- Full front-end (React/Next.js) is a Phase 3 maturation concern

#### Phase 5: Dagster Scheduling (DevOps Lead — 1-2 days)
- Daily ingestion schedule exists in code (`definitions.py`) but is `STOPPED`
- Enable after Phases 2+3 are validated

---

## Key Divergences: Initial Project Plan vs. Developer Handoff

| Topic | Initial Project Plan | Handoff Document | Resolution |
|-------|---------------------|-----------------|------------|
| **Jurisdictions** | Colorado + Federal NIST (2) | CA, TX, NY, FL via LegiScan | Build LegiScan connector but keep Colorado SB205 as validation anchor |
| **Data source** | Direct source connectors | LegiScan API (30K req/month free) | LegiScan is pragmatic for breadth; maintain direct connectors for depth |
| **Front-end** | HTMX review UI (server-rendered) | React/Next.js dashboard | Defer; use Supabase Studio as stopgap |
| **Extraction prompts** | Sophisticated agent-specific prompts with abstention | Simple system/user JSON prompt | Use existing agent architecture (it's better) |

---

## Known Bugs (Prioritized)

1. **[Blocking]** `docker/dagster.yaml:24` — Markdown code fence embedded in YAML. Remove lines 24+.
2. **[Blocking]** `docker/docker-compose.yml` — `dagster-webserver` missing `DAGSTER_DATABASE_URL` environment variable.
3. **[Minor]** `src/schemas/extraction.py:146` — Forward reference to `ExceptionItem`. Reorder class definitions.
4. **[Medium]** `src/api/routes/v1.py:46-66` — SQL string interpolation for view names. Harden against future misuse.

---

## Non-Negotiable Design Principles (Preserved)

These are the load-bearing walls. The team must not compromise on:

1. **Immutability-first** for raw artifacts (content-addressable, SHA-256)
2. **Evidence spans** on every extracted field (verbatim source text)
3. **Abstention as first-class output** (no hallucinated gap-filling)
4. **Confidence tiering** with human review routing (A/B/C/D → review queue)
5. **Full provenance chain** from served obligation to source passage
6. **Versioned prompt templates** tracked in git
7. **Pydantic v2 strict mode** validation on all extraction outputs
8. **Evaluation harness** with gold-standard benchmarks
9. **Dagster** for pipeline orchestration with asset-based lineage

---

## Recommended Execution Sequence

| Week | Focus | Owner | Deliverable |
|------|-------|-------|-------------|
| 1 | Fix Dagster + get LegiScan API key + minimal connector | DevOps + Backend/API | Dagster running; first document ingested |
| 2 | End-to-end pipeline test with real Colorado SB205 data | Applied NLP + Regulatory Review | Extraction results in review queue |
| 3 | Expand gold-standard to 10+ fixtures; tune prompts | Applied NLP + Regulatory Review | Evaluation harness F1 > 0.80 per agent |
| 4 | Apply materialized views; test `/v1/obligations` with real data | Backend/API + Knowledge Graph | Product API returning real obligations |
| 5 | Dagster scheduling; LegiScan multi-state expansion | DevOps + Backend/API | Automated daily ingestion |
