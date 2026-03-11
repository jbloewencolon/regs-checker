# AI Legal Corpus: Simplification & Efficiency Analysis

**From the Desks of:**
- **Legal Knowledge Architect** — legal correctness
- **Senior Data Platform Architect** — technical architecture
- **Product / Technical Program Lead** — execution and productization

**To:** Applied NLP Lead · Knowledge Graph Lead · Regulatory Review Lead · DevOps Lead · Backend/API Lead

**Date:** March 2026

---

## Executive Summary

The five-phase specification is thorough, well-reasoned, and architecturally defensible. It was clearly written by people who understand both the legal domain and data engineering. That said, after a joint review, we believe the current design carries approximately **40% more structural complexity than the problem demands at launch**, primarily through premature decomposition, infrastructure over-diversification, and sequencing choices that front-load difficulty. Below we present **12 concrete simplifications** organized into three tiers: things that reduce cost with zero precision loss, things that trade marginal theoretical capability for major execution speed, and things that restructure the phasing itself to deliver value earlier.

---

## Tier 1: Pure Efficiency Gains (Zero Precision/Accuracy Trade-off)

### 1. Collapse 9 Extraction Agents into 4 Composite Agents

**Current state:** Phase 2 defines 9 separate agents (Definition, Actor, Threshold, Obligation, Exception, Enforcement, Timeline, Framework, Ambiguity), each with its own detect → extract → self-check cycle. That's up to **27 LLM calls per passage** if all agents fire.

**Proposed change:** Consolidate into 4 agents:

| New Agent | Absorbs | Rationale |
|-----------|---------|-----------|
| **Obligation Agent** (primary) | Obligation + Timeline + Enforcement | These are structurally co-located in legislative text. An obligation's timing and penalty are almost always in the same clause or adjacent clause. One prompt with a richer schema extracts all three in a single pass. |
| **Definition & Actor Agent** | Definition + Actor Mapping + Framework | Definitions, actor roles, and framework references are all "what do the words mean" tasks. They operate on preamble/definitions sections and produce lookup tables. One pass over the definitions section handles all three. |
| **Threshold & Exception Agent** | Threshold + Exception | Both are "boundary condition" extractions — when does the obligation apply, and when doesn't it? They share context needs (the obligation they modify) and can be co-extracted. |
| **Ambiguity Agent** | Ambiguity (unchanged) | This is genuinely different — it's a meta-analysis agent. Keep it separate. |

**Impact:**
- LLM calls per passage drop from up to 27 to **up to 12** (4 agents × 3 passes).
- Prompt engineering effort drops by ~50% (4 prompt templates instead of 9).
- Cross-agent consistency checks (Phase 2 Section 7.5) become largely **intra-agent** consistency, which is cheaper and more reliable.
- The richer per-agent schema actually *improves* extraction quality because the model sees related fields in context (e.g., extracting an obligation alongside its enforcement mechanism gives the model better signal for both).

**Precision/accuracy effect:** Neutral to positive. Research consistently shows that co-extracting related fields in a single structured prompt outperforms piecemeal extraction, because the model can use cross-field signal. The only risk is longer output schemas, which Claude Sonnet handles comfortably within the 20K output token budget.

**Who owns this:** Applied NLP Lead, with review from Legal Knowledge Architect on schema consolidation.

---

### 2. Eliminate the Detection Pass (Pass 1) as a Separate LLM Call

**Current state:** Every extraction task starts with a Haiku call asking "does this passage contain an extractable object?" If yes, Sonnet does the full extraction.

**Proposed change:** Merge detection into the extraction prompt. Instruct the extraction model to return `{"detected": false, "reason": "..."}` when nothing is extractable, instead of making a separate call. Sonnet at temperature 0.0 is perfectly capable of abstaining.

**Impact:**
- Eliminates **one LLM call per passage per agent**. With 4 agents, that's 4 fewer API round-trips per passage.
- Haiku cost savings are modest per-call but compound across thousands of passages.
- Reduces total pipeline latency by ~20% (each Haiku call was budgeted at 500ms).

**Precision/accuracy effect:** Neutral. The detection question ("is there an obligation here?") is trivially easy for Sonnet. Haiku was chosen for cost, but the cost savings of removing the call entirely exceed the cost difference between Haiku and Sonnet for a binary classification.

**Who owns this:** Applied NLP Lead.

---

### 3. Replace Self-Check (Pass 3) with Constrained Decoding + Rule Validation

**Current state:** After extraction, a second Sonnet call reviews the extraction against the source passage. If it fails, the extraction is retried once with feedback.

**Proposed change:** Drop the self-check LLM call. Instead:
- Use Pydantic v2 strict mode and JSON schema validation (already in Pass 4) as the primary quality gate.
- Add **evidence span verification** to the rule-based validator: confirm each `evidence_span.text` appears verbatim in the passage. This is the most valuable check the self-check was doing, and it's a simple string operation.
- Keep the retry-on-failure mechanism, but trigger it from rule validation failures, not from an LLM opinion.

**Impact:**
- Eliminates **one more LLM call per passage per agent**. Combined with #2 above, each passage now requires only **1 LLM call per agent** instead of 3.
- Total LLM calls per passage: **4** (one per consolidated agent) instead of the original theoretical maximum of **27**.
- Cost reduction: ~70% fewer LLM API calls across the extraction pipeline.

**Precision/accuracy effect:** Slightly positive. Self-check via LLM is a known source of false negatives (the model second-guesses correct extractions). Evidence span verification via string matching is actually more reliable for the specific check that matters: "did you hallucinate this quote?" Rule-based validation for impossible values is deterministic and doesn't hallucinate.

**Who owns this:** Applied NLP Lead + Backend/API Lead.

---

### 4. Use PostgreSQL Recursive CTEs Instead of Neo4j for Phase 3

**Current state:** Phase 3 introduces Neo4j Community Edition as a separate graph database with its own backup strategy, migration system, staging/production databases, materialization pipeline, and Cypher query templates.

**Proposed change:** Model the graph relationships as **tables and foreign keys in Postgres**, and use recursive CTEs for path traversal. Specifically:

- `obligation_dependencies` table: links obligations to definitions, thresholds, exceptions (replaces Neo4j edges)
- `applicability_conditions` table: models the AND/OR/NOT expression tree as an adjacency list with `parent_id`, `node_type`, `ordinal`
- `amendment_lineage` table: links document versions (already partially exists as `predecessor_id` chains)

Use recursive CTEs like:
```sql
WITH RECURSIVE dep_tree AS (
  SELECT * FROM obligation_dependencies WHERE obligation_id = ?
  UNION ALL
  SELECT d.* FROM obligation_dependencies d
  JOIN dep_tree t ON d.parent_id = t.child_id
  WHERE depth < 5
)
```

**Impact:**
- **Eliminates an entire infrastructure component** (Neo4j), its Docker service, its backup strategy, its migration system, its staging/production split, and its dedicated Python driver.
- DevOps complexity drops significantly. One database to back up, monitor, and scale.
- The materialization pipeline (Phase 3, Section 7 — five Dagster stages for extract/resolve/build-nodes/build-edges/write) collapses into standard SQL INSERT/UPDATE operations triggered by Phase 2 completion.
- Cypher query templates are replaced by SQL queries, which the entire team already knows.

**Precision/accuracy effect:** Identical. The query patterns described in Phase 3 (applicability traversal to depth 5, dependency tracing, amendment lineage, jurisdictional comparison) are all well within Postgres recursive CTE capability at the data volumes described (tens of thousands of obligations, not millions). Neo4j's advantage is at millions of nodes with deep, variable-length traversals. This corpus won't hit that scale for years, if ever.

**Risk:** If the corpus grows to millions of interconnected obligations across 50+ jurisdictions with deep cross-reference chains, Postgres CTEs will slow down. **Mitigation:** The table schema is designed so that a future migration to Neo4j (or any graph store) requires only a materialization layer on top of the existing relational data. No data model change, just an additional read path.

**Who owns this:** Knowledge Graph Lead (reframed as Dependency Modeling Lead) + DevOps Lead.

---

### 5. Defer Phase 4 Bi-Temporal Modeling — Use Simple Temporal Columns First

**Current state:** Phase 4 introduces full bi-temporal modeling with `daterange` and `tstzrange` columns, a temporal state machine with 10 states and validated transitions, a propagation engine with depth limits and idempotency, court action suppression, preemption overlays, 5 materialized views, and a legal event processing pipeline.

**Proposed change:** Implement a **simple temporal model** in Phase 2 that covers 90% of real-world queries:

- Add `effective_date`, `sunset_date`, `temporal_status` (enum: `enacted`, `active`, `future_effective`, `repealed`, `stayed`) to analytic tables.
- Add a `legal_events` table (append-only) for tracking enactments, amendments, stays, and repeals.
- Add a single `current_active_obligations` materialized view.
- **Do not build** the propagation engine, the temporal state machine, the conflict resolver, the bi-temporal range columns, or the knowledge-time axis until there is demonstrated user demand for "what did we know on date X?" queries.

**Why this works for launch:**
- Compliance teams asking "what applies to me today?" and "what's changing in the next 90 days?" need `effective_date`, `sunset_date`, and `temporal_status`. That's it.
- The "what was the legal landscape on March 15, 2025?" question (which requires full bi-temporality) is an analyst/litigation question, not a day-to-day compliance question. It can be a v2 feature.
- Court stays and preemption overlays can be modeled as status changes (`temporal_status = 'stayed'`) with a note field, without a dedicated suppression subsystem.

**Impact:**
- Phase 4 as currently specified is ~5,800 words of implementation spec. The simplified version is ~800 words.
- Eliminates: temporal state machine, propagation engine, conflict resolver, preemption overlay subsystem, 5 materialized views, court action processing pipeline, temporal inheritance model.
- Development time for temporal capability drops from an estimated 4-6 weeks to ~1 week.

**Precision/accuracy effect:** No effect on extraction precision. Temporal *query* capability is reduced, but only for edge-case queries that most users won't need at launch. The data model supports full bi-temporality later because `legal_events` is append-only and nothing is ever deleted.

**Who owns this:** Regulatory/Policy Review Lead (validates which temporal queries are launch-critical) + Backend/API Lead.

---

## Tier 2: Structural Simplifications (Minor Trade-offs, Major Speed Gains)

### 6. Unify Phase 2 Review UI and Phase 5 Product API into One FastAPI App from Day 1

**Current state:** Phase 2 builds a FastAPI + HTMX internal review tool. Phase 5 builds a FastAPI product API with Redis caching, rate limiting, Pydantic response models, and OpenAPI docs. These are described as extensions of each other but specified separately with 40+ pages of combined endpoint definitions.

**Proposed change:** Design one FastAPI application from the start with two route groups:
- `/internal/` — review endpoints (HTMX rendered)
- `/v1/` — product API endpoints (JSON, cached, rate-limited)

Both share: auth middleware, database connections, Pydantic models, error handling, and deployment infrastructure.

**Impact:**
- One deployment artifact instead of an API that accretes over 3 phases.
- Shared Pydantic models between review UI and product API prevent the common failure mode where internal and external representations of the same obligation diverge.
- Rate limiting and caching (Phase 5 additions) are additive middleware, not architectural changes.

**Who owns this:** Backend/API Lead.

---

### 7. Replace the 5-Stage Materialization Pipeline with Postgres Triggers + Views

**Current state (Phase 3/5):** Dagster assets extract approved objects → resolve graph identity → build node payloads → build edge payloads → write incrementally. Then Phase 5 has another materialization pipeline from analytic tables → served tables.

**Proposed change (given Neo4j elimination from #4):**
- Use Postgres **materialized views** for the served layer. `served_obligations` becomes a materialized view over `analytic_obligations` joined with temporal and dependency data.
- Use Postgres **triggers** on `review_actions` to auto-refresh materialized views when extractions are approved.
- The compliance matrix (`served_matrix_cells`) is also a materialized view with a `REFRESH MATERIALIZED VIEW CONCURRENTLY` triggered by the publication workflow.

**Impact:**
- Eliminates 2 separate ETL/materialization codebases (Phase 3 materialization + Phase 5 serving materialization).
- Data freshness improves: materialized views refresh in seconds, not on Dagster schedules.
- Debugging is dramatically simpler: the served layer is a SQL view definition, not a multi-stage pipeline with intermediate state.

**Precision/accuracy effect:** Identical. The data transformations are the same; only the execution mechanism changes.

**Who owns this:** Backend/API Lead + DevOps Lead.

---

### 8. Simplify the Confidence Scoring Model

**Current state:** Phase 2, Section 8 defines a 7-component weighted confidence score (schema validity, evidence span quality, self-check pass, completeness, cross-agent consistency, passage quality, change recency) with 4 tiers (A/B/C/D).

**Proposed change:** With the extraction pipeline simplified (no self-check pass, fewer agents, evidence verified by string matching), the confidence model simplifies to:

| Component | Weight | Source |
|-----------|--------|--------|
| Schema validity | 0.25 | Pydantic validation (binary) |
| Evidence grounding | 0.35 | Proportion of fields with verified evidence spans |
| Completeness | 0.20 | Proportion of non-null optional fields |
| Source quality | 0.20 | Phase 1 parse quality score |

Keep the 4 tiers (A/B/C/D) with the same thresholds. Drop self-check and cross-agent components since those validation paths are eliminated or restructured.

**Impact:** Simpler to implement, explain, and debug. Fewer moving parts in the scoring pipeline.

**Who owns this:** Applied NLP Lead.

---

### 9. Start with 2 Jurisdictions, Not 5

**Current state:** Phase 1 specifies connectors for NCSL, Colorado, and California, with test fixtures across 5 states and 20 documents. The architecture is designed for multi-state scale from day one.

**Proposed change:** Launch with **Colorado and one federal source** (e.g., NIST AI RMF or the federal AI executive orders). Add California as the third jurisdiction after the full pipeline is validated end-to-end.

**Rationale:**
- Colorado SB205 is the most mature U.S. state AI law and the best test case.
- A federal source exercises a different document type (executive order / framework vs. state statute) without multiplying jurisdiction-specific connector work.
- 20 test fixtures across 5 states means 4 documents per state — too thin to catch real edge cases. 10 documents across 2 jurisdictions is denser and more useful.
- Every additional connector is ~2-3 days of development + ongoing maintenance for site structure changes. Defer until the extraction pipeline is proven.

**Impact:**
- Connector development drops from 3 custom connectors to 2.
- Test fixture curation is more focused and higher quality.
- The normalization controlled vocabularies (jurisdiction codes, document types) are validated against real data before expanding.

**Who owns this:** Product/TPM Lead + Regulatory Review Lead.

---

## Tier 3: Re-Phasing for Earlier Value Delivery

### 10. Restructure into 3 Phases Instead of 5

The current 5-phase structure front-loads infrastructure and delays product value. We propose collapsing into 3 phases:

**New Phase 1: Ingest + Extract + Review (≈ Old Phases 1 + 2)**

Deliver: Documents ingested, parsed, and structured. Extraction pipeline operational. Internal review UI functional. Gold-standard evaluation passing targets.

This is the foundation. No external product yet, but the team can demonstrate: "here is Colorado SB205, here are the 47 obligations we extracted, here is the evidence for each one, here is the confidence score, here is the review interface."

**New Phase 2: Dependencies + Temporal + Serving (≈ Old Phases 3 + 4 + 5, simplified)**

Deliver: Obligation dependency modeling (in Postgres, per #4). Simple temporal status tracking (per #5). Applicability engine. Compliance matrix. Product API with /v1/ endpoints. Change intelligence feed.

This is the product. After this phase, an API consumer can ask: "I'm a developer operating in Colorado — what applies to me?" and get a structured, explainable answer.

**New Phase 3: Scale + Harden + Extend**

Deliver: Additional jurisdictions (California, other states). Full bi-temporal modeling (if validated by user demand). Graph database migration (if Postgres CTEs hit performance limits). Multi-tenancy. OAuth. Export system. Advanced temporal queries.

This is the maturation phase. It's driven by real user feedback, not speculative architecture.

**Impact:**
- Time to first external API response drops from ~20 weeks (end of old Phase 5) to ~12 weeks (end of new Phase 2).
- Old Phase 3 (Neo4j graph) and Phase 4 (bi-temporal) no longer block product delivery.
- The team delivers a usable product after Phase 2, then iterates based on actual usage patterns.

**Who owns this:** Product/TPM Lead, with architectural guidance from Senior Data Platform Architect.

---

### 11. Build the Evaluation Harness in Phase 1, Not as a Phase 2 Afterthought

**Current state:** The gold-standard evaluation harness is described in Phase 2, Section 14, with a target of 50 test cases "by end of Sprint 6."

**Proposed change:** Build the evaluation harness and curate 20 gold-standard test cases **before writing a single extraction prompt**. The harness is the team's compass. Without it, prompt engineering is guesswork.

**Concrete steps:**
1. Regulatory Review Lead manually annotates 20 passages from Colorado SB205 with the expected extraction output for each agent type.
2. Applied NLP Lead builds the evaluation runner (compare model output to gold standard, compute precision/recall/F1 per agent).
3. All prompt development is measured against this harness from day one.

**Impact:** Prevents the most expensive failure mode in LLM-powered systems: iterating on prompts without a ground-truth benchmark. Every prompt change is immediately measurable.

**Who owns this:** Regulatory Review Lead (annotation) + Applied NLP Lead (harness code).

---

### 12. Consolidate the Database Schema

**Current state across all phases:**
- Phase 1: 13 tables
- Phase 2: ~10 new tables
- Phase 3: graph tracking tables + migration tracking
- Phase 4: ~6 new tables + column additions to existing tables
- Phase 5: ~6 new tables

Total: **~35+ tables** plus materialized views, which is a large schema surface to maintain, migrate, and reason about.

**Proposed consolidation:**

| Keep (Core) | Merge/Simplify | Defer |
|-------------|----------------|-------|
| `sources` | `fetch_jobs` + `source_discovery_events` → single `ingestion_jobs` table | `preemption_overlays` |
| `raw_artifacts` | `parse_jobs` merged into `raw_artifacts` as status columns | `temporal_conflicts` |
| `normalized_source_records` | 8 analytic tables → 1 `extractions` table with `extraction_type` discriminator + JSONB `payload` | `temporal_propagation_log` |
| `document_families` + `document_versions` | `extraction_tasks` + `extraction_runs` → single `extraction_jobs` table | Phase 3 graph tracking tables |
| `legal_events` | `served_obligations` + `served_matrix_cells` as materialized views, not tables | `court_actions` (model as legal_events) |
| `review_queue` + `review_actions` | | |
| `api_keys` + `export_jobs` | | |

**The key insight:** The 8 analytic tables (`analytic_obligations`, `analytic_definitions`, `analytic_thresholds`, `analytic_actor_mappings`, `analytic_exceptions`, `analytic_enforcement`, `analytic_timelines`, `analytic_framework_refs`) all share the same structural pattern (Phase 2, Section 2.4.2 says so explicitly). They can be a single `extractions` table with a `type` discriminator and a JSONB `payload` column, with Pydantic validation enforcing the per-type schema in application code. This cuts 8 tables to 1 and eliminates 7 Alembic migrations.

**Impact:** Schema drops from ~35 tables to ~15 core tables + materialized views. Alembic migration count drops proportionally. Cognitive load for new team members is halved.

**Risk:** JSONB columns are harder to query with SQL than typed columns. **Mitigation:** Define Postgres generated columns for the 3-4 most-queried fields per extraction type (`subject_normalized`, `modality`, `jurisdiction`), and use GIN indexes on the JSONB for everything else.

**Who owns this:** Backend/API Lead + DevOps Lead.

---

## Summary: Combined Impact

| Metric | Current Design | Simplified Design | Change |
|--------|---------------|-------------------|--------|
| Phases | 5 | 3 | -40% |
| Database tables | ~35 | ~15 + views | -57% |
| Infrastructure components | Postgres + S3 + Dagster + Neo4j + Redis | Postgres + S3 + Dagster + Redis | -1 system |
| Extraction agents | 9 | 4 | -56% |
| LLM calls per passage | Up to 27 | 4 | -85% |
| Weeks to first API response | ~20 | ~12 | -40% |
| Spec pages | ~120 | ~60 (est.) | -50% |

---

## What We Are NOT Simplifying

To be clear on what stays exactly as specified:

- **Immutability-first design for raw artifacts.** Non-negotiable.
- **Evidence spans on every extracted field.** This is the legal defensibility foundation.
- **Abstention as first-class output.** No hallucinated gap-filling.
- **Confidence tiering with human review routing.** The review workflow is essential.
- **Provenance chain from served obligation back to source passage.** Full audit trail.
- **Versioned prompt templates tracked in git.** Reproducibility.
- **Content-addressable artifact storage (SHA-256).** Deduplication integrity.
- **Pydantic v2 schema validation on all extraction outputs.** Type safety.
- **The evaluation harness and gold-standard benchmark.** Quality measurement.
- **Dagster for orchestration.** The asset-based lineage model is correct for this workload.

These are the load-bearing walls. Everything else is interior partition that can be moved.

---

## Recommended Next Steps

1. **This week:** Regulatory Review Lead begins gold-standard annotation on Colorado SB205 (20 passages). Applied NLP Lead builds evaluation harness scaffold.
2. **Week 2:** Applied NLP Lead prototypes consolidated Obligation Agent (obligation + timeline + enforcement) and measures against harness.
3. **Week 3:** Backend/API Lead stands up unified FastAPI app with `/internal/` review routes and basic ingestion pipeline.
4. **Week 4:** Knowledge Graph Lead models obligation dependencies in Postgres; validates that recursive CTEs handle the 6 query patterns from old Phase 3.
5. **Ongoing:** DevOps Lead maintains Docker Compose environment with Postgres + MinIO + Dagster only. No Neo4j. Redis added when caching is needed (new Phase 2).

---

*This analysis represents the joint recommendation of the Legal Knowledge Architect, Senior Data Platform Architect, and Product/Technical Program Lead. It is intended as a starting point for team discussion, not a unilateral directive. We welcome pushback, particularly from the Knowledge Graph Lead on recommendation #4 and the Applied NLP Lead on recommendations #1-3.*
