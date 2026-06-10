# Engineering Handoff: Regs Checker Extraction Pipeline

**Date:** 2026-03-11 (updated)
**Branch:** `claude/define-leadership-structure-H5Jvc`
**Session:** All work across PRs #9–#13 plus unmerged commits on the feature branch.

---

## What Was Built

This session took the project from "ingestion pipeline with 9,182 passages in Supabase" to a fully operational extraction pipeline with 28,885 extractions synced, plus a production-grade sync mechanism to push extractions into the Policy Navigator product database.

### 1. AI Extraction Pipeline (`src/ingestion/extractor.py`, `src/agents/base.py`)

**The core extraction loop:**
- 4 consolidated agents (obligation, definition_actor, threshold_exception, ambiguity) run against each passage
- Concurrent execution via `ThreadPoolExecutor` (max_workers=4)
- Multi-extraction support: one passage can produce multiple extractions
- Pydantic v2 validation + evidence span verification via string matching
- Confidence scoring and automatic review queue routing

**Files created/modified:**
- `src/agents/base.py` — Base agent class with LLM calling, retry logic, JSON parsing
- `src/agents/obligation.py` — Obligation + timeline + enforcement co-extraction
- `src/agents/definition_actor.py` — Definition + actor mapping + framework reference co-extraction
- `src/agents/threshold_exception.py` — Threshold + exception co-extraction
- `src/agents/ambiguity.py` — Vague/ambiguous language detection
- `src/ingestion/extractor.py` — Pipeline orchestrator (the main file)
- `src/schemas/extraction.py` — Pydantic output schemas for all extraction types

### 2. LLM Response Handling Fixes

Three bugs were found and fixed during the first extraction runs:

**Empty response handling** (commit `4486075`):
- `response.content[0].text` assumed the first content block was always text type
- Fixed to iterate blocks and find the first `type == "text"` block
- Added `ValueError` on empty responses with diagnostic logging (stop_reason, content_types)
- Added debug logging of raw response preview before `json.loads()`

**Markdown code fence stripping** (commit `79dca80`):
- Claude was wrapping JSON output in ` ```json ... ``` ` code fences
- Added `_strip_code_fences()` static method as a belt-and-suspenders parser fix
- Added `"Return only raw JSON with no markdown formatting, no code fences, and no preamble."` to every system prompt to prevent fences at the source

**Pydantic type coercion** (commit `f0b1392`):
- `ThresholdExceptionPayload.threshold_value`: Claude returned `50000` (int) instead of `"50000"` (str). Added `field_validator` to coerce int/float to str.
- `ActorMapping.responsibilities`: Claude returned `"build stuff"` (str) instead of `["build stuff"]` (list). Added `field_validator` to wrap bare strings.
- These two mismatches caused ~637 validation failures in the batch run.

### 3. Six Cost Optimizations (commit `1aac50f`)

The full corpus at Sonnet pricing was estimated at ~$150+. These optimizations bring it to ~$3–4:

| Optimization | Mechanism | Estimated Savings |
|---|---|---|
| **Skip tiny passages** | Filter <150 chars before API call | ~38% token reduction |
| **Merge adjacent fragments** | Combine <300 char passages from same section | 5–10x fewer API calls per list |
| **Selective agent routing** | Keyword pre-screening skips irrelevant agents | 40–60% fewer agent calls |
| **Haiku model** | Default changed from Sonnet to `claude-haiku-4-5-20251001` | ~20x cheaper |
| **Batch API** | `--batch` flag routes through `/v1/messages/batches` | 50% discount |
| **Orrick context injection** | `key_requirements` from tracker metadata in prompts | Higher quality per token |

**Keyword patterns** (in `extractor.py`):
- `obligation`: skipped unless passage contains modal verbs (`shall`, `must`, `may not`, `prohibited`, `required`)
- `threshold_exception`: skipped unless numbers, dates, or conditionals (`unless`, `except`, `within`, etc.)
- `definition_actor`: skipped unless definitional language (`means`, `defined as`, `shall mean`, `includes`)
- `ambiguity`: always runs

**Passage merging** (`_merge_short_passages()`):
- Sorts by `(document_version_id, ordinal)`
- Adjacent passages under 300 chars from same document are joined with `\n`
- Caps merged output at 2000 chars to avoid context overflow
- Each `MergedPassage` tracks all its source `NormalizedSourceRecord` objects for DB writes

### 4. Batch API Support (commits `1aac50f`, `732731c`, `1594ba3`)

**Submission** (`_run_batch_extraction()` in `extractor.py`):
- Builds all (passage, agent, prompt) combinations
- Encodes `custom_id` as `{record_ids}_{agent_name}` with sanitized characters (`[a-zA-Z0-9_-]`, max 64 chars)
- Submits via `client.messages.batches.create()`

**Retrieval** (`retrieve_batch_results()` in `extractor.py`):
- Polls batch status, iterates `.results()` when complete
- Parses `custom_id` back to record IDs + agent name (handles compound names like `threshold_exception` by backtracking underscores)
- Runs the same JSON parse → Pydantic validate → confidence score → DB write pipeline as synchronous mode

**CLI:**
```bash
# Submit batch
python -m src.scripts.seed_pipeline --mode extract --batch

# Retrieve results
python -m src.scripts.seed_pipeline --mode batch-results --batch-id msgbatch_01VGYkKdLkMjsacQdLVRBnfv
```

### 5. Dagster Refactor (commit `1aac50f`)

`src/dagster_pipelines/assets.py` was refactored from ~150 lines of duplicated agent-iteration logic to a thin wrapper around the shared `run_extraction()` function (~50 lines). All optimizations (short passage skip, merge, selective routing, etc.) are automatically inherited.

### 6. Sync Infrastructure (commit `33511d3`)

Two new scripts for the Phase 3 Data Architect deliverables:

**`src/scripts/sync_extractions.py`** — Regs Checker Supabase → Policy Navigator Supabase:
- Reads `law_document_bridge` from Policy Navigator to resolve `document_family_id` → `law_id`
- Cursor-based incremental sync: `MAX(system_a_extraction_id)` in `synced_extractions`
- Idempotent upserts: `ON CONFLICT (system_a_extraction_id) DO NOTHING`
- Carries: payload, evidence_spans, confidence, tier, review_status, section_path, passage_text, timestamps
- Batch size 500, periodic commits
- Source (Regs Checker) is **read-only** — only SELECTs, never writes

**`src/scripts/sync_monitor.py`** — Health check across both Supabase instances:
- Queries source for extraction counts by type, tier, status
- Queries target for synced counts and bridge coverage
- Calculates sync lag (source total - target total)
- Alert thresholds calibrated against batch run baselines:
  - `>40%` Tier C → quality regression (investigate before next batch)
  - `>65%` ambiguity → over-flagging (ambiguity agent may need tuning)
  - `>500` sync lag → sync stalled (run sync_extractions.py)
  - Empty bridge → no syncing possible
- `--json` flag for CI integration
- Exit code 1 when alerts fire

**`src/scripts/sync_to_supabase.py`** — Unchanged. Still handles local Docker → Regs Checker Supabase. Different pipeline leg, not in competition.

---

## Architecture: Data Flow

```
Local Docker PG                          Regs Checker Supabase          Policy Navigator Supabase
(development)                            (wjxlimjpaijdogyrqtxc)         (aaxxunfarlhmydvohsrm)

15 pipeline tables ──sync_to_supabase──> 15 pipeline tables
                                         28,885 extractions ──sync_extractions──> synced_extractions
                                                                                  (via law_document_bridge)

                                         ◄──────── sync_monitor ────────►
                                         (SELECT only)                   (SELECT only)
```

---

## Environment Variables

| Variable | Used By | Purpose |
|---|---|---|
| `REGS_DATABASE_URL` | All pipeline code | Local Docker PG (development) |
| `REGS_SUPABASE_URL` | sync_to_supabase, sync_extractions (source), sync_monitor (source) | Regs Checker Supabase |
| `REGS_POLICY_NAVIGATOR_URL` | sync_extractions (target), sync_monitor (target) | Policy Navigator Supabase |
| `REGS_ANTHROPIC_API_KEY` | Extraction agents | Claude API access |

---

## Current State

| Metric | Value |
|---|---|
| Laws ingested | 180 |
| Passages in Regs Checker Supabase | 9,182 |
| Extractions in Regs Checker Supabase | 28,885 |
| Extractions in Policy Navigator | 28,885 (synced 2026-03-11) |
| Default extraction model | `claude-haiku-4-5-20251001` |
| Unit tests passing | 100/100 |

---

## What's Not Done / Next Steps

### Immediate (before next extraction batch)

1. ~~**Run sync_monitor.py against live Supabase**~~ — **DONE (2026-03-11).** Zero sync lag, 28,885 in both databases.

2. ~~**Run sync_extractions.py**~~ — **DONE (2026-03-11).** First cross-system sync: 28,885 extractions, zero skipped, zero errors. Required fixing IPv6 connectivity (switched to Supabase connection pooler) and a schema mismatch between the sync script and the product table.

3. **Merge this PR** — Feature branch has all work. Create PR and merge to main.

4. ~~**Set env vars**~~ — **DONE.** Both Supabase URLs configured.

### Short-term

5. **Recover 637 failed extractions** — Run `python -m src.scripts.seed_pipeline --mode recover`. This new mode finds passages with partial extraction coverage (some agents succeeded, others failed due to pre-fix Pydantic validation bugs) and re-runs only the missing agents. Test with `--limit 10` first.

6. **Sonnet re-extraction for Tier C** — The pipeline defaults to Haiku for cost. Low-confidence (Tier C) extractions could be re-run with Sonnet (`REGS_EXTRACTION_MODEL=claude-sonnet-4-20250514`) for higher quality. This is a targeted re-run, not a full corpus pass.

7. **Unit tests for new code** — The sync scripts (`sync_extractions.py`, `sync_monitor.py`) and the extraction pipeline optimizations (passage merging, agent selection, batch API) have no dedicated unit tests. The existing 100 tests pass but don't cover these new paths.

### Medium-term

8. **Batch results retrieval automation** — Currently manual (`--mode batch-results --batch-id <id>`). Could be automated to poll batch status and process when complete.

9. **Prompt template versions** — YAML templates exist in `prompts/` but the agents also have inline fallback prompts. The Orrick `key_requirements` context injection is only in the inline prompts, not the YAML templates. These should be unified.

10. **Bridge coverage gaps** — `sync_extractions.py` logs `skipped_no_bridge` count. Any extraction whose document family isn't in `law_document_bridge` silently won't sync. The monitor reports bridge coverage but doesn't create missing entries.

---

## File Index

### Modified (this session)
- `src/agents/base.py` — LLM call, response parsing, retry, code fence stripping
- `src/agents/obligation.py` — Orrick context injection
- `src/agents/definition_actor.py` — Orrick context injection
- `src/agents/threshold_exception.py` — Orrick context injection
- `src/agents/ambiguity.py` — Orrick context injection
- `src/ingestion/extractor.py` — Pipeline orchestrator (passage filtering, merging, agent selection, batch API)
- `src/schemas/extraction.py` — Pydantic type coercion validators
- `src/dagster_pipelines/assets.py` — Refactored to use shared `run_extraction()`
- `src/scripts/seed_pipeline.py` — Added `--batch`, `--batch-id`, `--mode batch-results`
- `src/core/config.py` — Added `supabase_url`, `policy_navigator_url`; changed default model to Haiku
- `.env.example` — Added Supabase connection string templates

### Created (this session)
- `src/scripts/sync_extractions.py` — Incremental extraction sync
- `src/scripts/sync_monitor.py` — Cross-database health monitor
