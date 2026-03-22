# Regs Checker — Engineering Handoff

**Date:** 2026-03-21
**Branch:** `claude/ai-policy-audit-analysis-Y6kiZ`
**Previous handoffs:** Archived in `archive/` (2026-03-11, 2026-03-18)

---

## 1. What This Project Is

Regs Checker is an AI-powered pipeline that extracts structured legal obligations from US state and federal AI legislation. It discovers bills from the Orrick PDF tracker and IAPP, fetches full text from primary legislature URLs, splits it into passages, runs 6 extraction agents to produce structured data (obligations, definitions, thresholds, ambiguities, rights, compliance mechanisms), scores confidence, routes low-confidence items through human review, and syncs approved results to a Policy Navigator product database.

**Core design principle:** Inference is offline. Serving is instant. The LLM is a build tool, not a runtime dependency.

---

## 2. Current Architecture

### Three-phase pipeline

1. **Discovery + Fetch** (`src/ingestion/pipeline.py`): Scrape legislative trackers, download PDFs/HTML, parse and chunk into passage-level `NormalizedSourceRecord` rows. Store raw bytes in MinIO.
2. **Extraction** (`src/ingestion/extractor.py`): Run 6 AI agents against each passage. All LLM inference happens here — offline, batch-capable, never in the serving path.
3. **Serving** (`src/api/routes/v1.py`): Zero-latency REST API over pre-extracted data. PostgreSQL queries only.

### LLM provider abstraction (`src/core/llm_provider.py`)

| Provider | Class | Default use | Protocol |
|----------|-------|-------------|----------|
| `anthropic` | `AnthropicProvider` | Cloud extraction | Anthropic SDK |
| `local` | `LocalLLMProvider` | Discovery + local extraction | OpenAI-compatible (LM Studio) |

Two factory functions route by task:
- `get_discovery_provider()` — reads `settings.discovery_provider` (default: `"local"`)
- `get_extraction_provider()` — reads `settings.extraction_provider` (default: `"anthropic"`)

The local provider speaks the OpenAI `/v1/chat/completions` protocol and works with LM Studio, Ollama, llama.cpp, or vLLM.

### The 6 extraction agents

| Agent | File | model_override | Task |
|-------|------|----------------|------|
| `obligation` | `src/agents/obligation.py` | `qwen/qwen3.5-9b` | Obligations + timeline + enforcement |
| `definition_actor` | `src/agents/definition_actor.py` | `openai/gpt-oss-20b` | Definitions + actors + framework refs |
| `threshold_exception` | `src/agents/threshold_exception.py` | `openai/gpt-oss-20b` | Thresholds + exceptions |
| `ambiguity` | `src/agents/ambiguity.py` | `openai/gpt-oss-20b` | Vague/conflicting language analysis |
| `rights_protection` | `src/agents/rights_protection.py` | `openai/gpt-oss-20b` | Individual rights (notice, opt-out, appeal) |
| `compliance_mechanism` | `src/agents/compliance_mechanism.py` | `openai/gpt-oss-20b` | Audits, assessments, reporting mandates |

All inherit from `BaseExtractionAgent` (`src/agents/base.py`).

### Why these model assignments

**Qwen 3.5 9B for obligation:** The obligation agent is the most demanding extractive agent — it must copy verbatim spans from dense legal text. Qwen is the most reliable local model for faithful extraction (verbatim quoting, clean JSON, correct abstention). It runs slower (~45 seconds per passage) but produces high-quality output.

**GPT-OSS 20B for the other 5:** These agents either do meta-analysis (ambiguity) or extract more structured/formulaic content (definitions, thresholds, rights, compliance). GPT-OSS responds in 1-2 seconds with clean structured output. It was chosen over DeepSeek-R1 because:

- **DeepSeek-R1 spends all output tokens on chain-of-thought** (`<think>...</think>` blocks) before producing any JSON. With the 4096-token local output budget, it routinely truncated — producing 43 seconds of reasoning followed by an incomplete JSON fragment.
- **GPT-OSS produces clean output immediately.** No `<think>` blocks, no wasted tokens on reasoning. 1-2 second response time vs 43 seconds.
- The ambiguity agent was the last to move from DeepSeek to GPT (commit `0599bf0`). It was originally on DeepSeek because ambiguity analysis benefits from chain-of-thought reasoning, but the truncation failures made it unusable.

**Fallback model:** When any agent's `model_override` fails, it falls back to the extraction provider's default model. For local extraction, this is `qwen/qwen3.5-9b` (changed from `deepseek/deepseek-r1-0528-qwen3-8b` in commit `f11422f` because DeepSeek as fallback was wasting 90 seconds per failure producing nothing).

### Model grouping for VRAM efficiency (`extractor.py:490-503`)

Agents are grouped by `model_override` to minimize VRAM model swaps in LM Studio:

```
Group 1 (GPT): ambiguity, definition_actor, threshold_exception, rights_protection, compliance_mechanism
  → Run in parallel via ThreadPoolExecutor (5 agents, same model, ~3 seconds total)

Group 2 (Qwen): obligation
  → Runs alone (~45 seconds)

Groups run sequentially. Agents within a group run concurrently.
```

This prevents LM Studio from thrashing between models. One model loads, all its agents run, then the next model loads.

### Evidence span verification (`base.py:298-358`)

Every extracted field must include `evidence_spans` — verbatim quotes from the source passage. The base agent verifies each span:

1. **Whitespace-normalized matching:** Both span and passage are normalized (collapse all whitespace to single spaces) before comparison. This was added in commit `4b1a463` because LLMs often insert/remove line breaks in verbatim quotes, causing exact-match failures.
2. **Case-insensitive fallback:** If normalized match fails, tries case-insensitive.
3. **Unverified spans lower confidence score** (computed in `src/core/confidence.py`), which determines review tier (A through D).

**This is the single most important constraint for model selection.** A model that paraphrases instead of quoting verbatim produces low-confidence extractions that flood the review queue.

### Confidence scoring (`src/core/confidence.py`)

5 weighted components:
- Schema validity (20%) — did Pydantic validation pass?
- Evidence grounding (30%) — % of fields with verified verbatim quotes
- Completeness (20%) — % of optional fields filled
- Source quality (15%) — parse quality from ingestion
- Orrick alignment (15%) — token similarity vs Orrick's tracker metadata

Tiers: A (>=85%), B (>=70%), C (>=50%), D (<50%)

---

## 3. What Changed This Session (2026-03-21)

### The problem

Extraction was running at 11.3% progress (24/149 passages) with an ETA of **1286 hours**. Each passage was taking 6-8 minutes due to cascading failures.

### Root causes identified and fixed

| # | Problem | Fix | Commit | Reasoning |
|---|---------|-----|--------|-----------|
| 1 | GPT model name wrong | `gpt-oss-20b` → `openai/gpt-oss-20b` | `fcc3303` | LM Studio requires the `openai/` prefix. Without it, all GPT agent calls returned 500 errors. |
| 2 | `EvidenceSpan.field_name` rejected `None` | Made field optional | `80f16da` | Pydantic strict validation was rejecting valid extractions where the LLM omitted `field_name`. This caused entire passages to fail. |
| 3 | Reasoning models ran out of output tokens | 2x token budget for DeepSeek/Qwen | `80f16da` | DeepSeek-R1 and Qwen 3.5 use internal reasoning tokens that count against `max_tokens`. The default 4096 wasn't enough — models truncated mid-JSON. Doubled to 8192 for reasoning models. |
| 4 | DeepSeek-R1 spent 43s thinking, produced nothing | Moved ambiguity + rights_protection to GPT | `0599bf0` | DeepSeek's chain-of-thought consumed all output tokens before producing JSON. GPT produces the same analysis in 1-2 seconds. This eliminated ~6 minutes of wasted time per passage. |
| 5 | Fallback model was DeepSeek (also broken) | Changed fallback to Qwen | `f11422f` | When GPT agents failed, they fell back to DeepSeek which spent another 43 seconds thinking and failing. Qwen as fallback actually succeeds. |
| 6 | Evidence spans failed exact matching | Whitespace-normalized matching | `4b1a463` | LLMs insert/remove whitespace in "verbatim" quotes. Before this fix, evidence verification was at 0%, making all extractions Tier C/D regardless of actual quality. |
| 7 | Truncated extractions looked like valid data | Flag `truncated: true` in metadata | `44d2f2a` | When `stop_reason == "length"`, the extraction is incomplete but was being stored as valid. Now flagged and surfaced in the review queue so truncated results can be identified and re-processed. |
| 8 | ETA averaged across all historical runs | Use current run's live rate | `f11422f` | The ETA was averaging old slow runs (with DeepSeek failures) with new fast runs. Now uses the running job's actual throughput, or the most recent completed job. |

### Expected performance after these fixes

```
Per passage:
  GPT group (5 agents, parallel): ~3 seconds
  Qwen group (obligation):        ~45 seconds
  Total:                           ~48 seconds

149 passages × 48 seconds = ~2 hours (down from 1286 hours)
```

---

## 4. Key Design Decisions and Their Reasoning

### Why 6 agents instead of 1?

Each agent has a focused extraction schema and system prompt tuned for one type of legal content. A single agent trying to extract obligations, definitions, thresholds, rights, compliance mechanisms, AND ambiguities from one passage would need a massive prompt and would produce lower quality results. The multi-agent approach also enables per-agent model selection and parallel execution.

### Why local LLM instead of Anthropic API?

The project is pivoting to air-gapped local inference. The original pipeline used Claude Haiku via the Anthropic API. The current branch runs extraction locally via LM Studio with three models loaded (GPT-OSS 20B, Qwen 3.5 9B, DeepSeek-R1 8B). This eliminates API costs and enables fully offline operation.

### Why LM Studio instead of Ollama?

Switched in commit `5177280`. LM Studio provides better control over loaded models, supports the OpenAI-compatible API that `LocalLLMProvider` already speaks, and handles multi-model VRAM management. The code works with either — `LocalLLMProvider` just needs the base URL changed.

### Why per-agent model_override instead of one model for everything?

Different agents have different needs:
- **Extractive agents** (obligation) need faithful verbatim quoting → Qwen excels here
- **Structured output agents** (definition, threshold, rights, compliance) need clean JSON fast → GPT excels here
- **Meta-analysis agents** (ambiguity) need interpretive analysis → GPT works; DeepSeek-R1 was theoretically better but truncated every time

The `model_override` class attribute on each agent (added via `BaseExtractionAgent`) feeds through `_call_llm` → `provider.call(model_override=...)` → LM Studio loads the right model.

### Why model grouping with sequential execution?

LM Studio on consumer hardware (32GB VRAM) can only run one model at a time efficiently. Running GPT and Qwen simultaneously causes VRAM thrashing and OOM. The grouping logic (`_group_agents_by_model` in `extractor.py:490-503`) ensures all agents using the same model run together before switching to the next model.

### Why whitespace-normalized span matching instead of exact?

LLMs reliably reproduce the words of a passage but inconsistently handle whitespace — they collapse line breaks, add spaces around punctuation, or normalize tabs. Requiring exact byte-for-byte matches caused 0% evidence verification even when the spans were substantively correct. The normalized matching (collapse all whitespace to single spaces, then substring match) preserves the integrity check while tolerating benign whitespace differences.

### Why Qwen as the fallback model?

The fallback model (`local_extraction_model` config) is what agents use when their preferred `model_override` fails. It needs to be a model that reliably produces valid output. DeepSeek-R1 was the original fallback, but it fails on most extraction tasks due to output token exhaustion. Qwen is the most reliable local model for extraction — it produces clean JSON with good verbatim quoting.

---

## 5. How to Run

### Prerequisites

- Docker & Docker Compose
- Python 3.11+
- LM Studio running on `localhost:1234` with these models loaded:
  - `openai/gpt-oss-20b`
  - `qwen/qwen3.5-9b`

### Start infrastructure

```bash
cd docker && docker compose up -d
```

| Service    | Port | Purpose                        |
|------------|------|--------------------------------|
| PostgreSQL | 5434 | Application database           |
| MinIO      | 9000 | S3-compatible artifact storage |
| FastAPI    | 8000 | Dashboard + API server         |

### Configure environment

```bash
cp .env.example .env
# Set at minimum:
#   REGS_EXTRACTION_PROVIDER=local
#   REGS_LOCAL_LLM_URL=http://localhost:1234
```

### Install and run

```bash
pip install -e ".[dev]"
uvicorn src.api.app:app --reload
```

Open **http://localhost:8000/dashboard/**

### CLI commands

```bash
# Discovery
python -m src.scripts.seed_pipeline --mode pdf

# Fetch documents
python -m src.scripts.seed_pipeline --mode fetch --limit 10

# API extraction (local or cloud depending on REGS_EXTRACTION_PROVIDER)
python -m src.scripts.seed_pipeline --mode extract --limit 20

# Run tests
pytest tests/
```

---

## 6. Dashboard UI

### Pipeline (`/dashboard/`)

Progress ring + per-step bars for: Discovery → Fetch & Parse → Extraction → Review → Sync.

**ETA calculation** (`src/api/progress.py`): Uses the currently running extraction job's live throughput (passages/minute) if one is active. Falls back to the most recent completed job's rate. Does NOT average across historical runs (changed in commit `f11422f` to avoid poisoning by old slow runs).

### Analytics (`/dashboard/analytics`)

Confidence distribution, extraction type counts, model comparison, jurisdiction breakdown, gold-standard evaluation runner.

### Review (`/dashboard/review`)

Queue with Pending/Approved/Rejected tabs. Each row shows tier badge, extraction type, confidence breakdown (5 mini bars), and approve/reject buttons. Truncated extractions are flagged with a warning icon.

---

## 7. File Index

### Core system
| File | Purpose |
|------|---------|
| `src/core/config.py` | Pydantic settings from environment variables |
| `src/core/llm_provider.py` | Provider abstraction (Anthropic + Local) |
| `src/core/confidence.py` | 5-component confidence scoring |
| `src/core/orrick_validation.py` | Orrick tracker similarity scoring |

### Agents
| File | Model | Purpose |
|------|-------|---------|
| `src/agents/base.py` | — | Base class: LLM calling, retry, JSON parse, evidence verification |
| `src/agents/obligation.py` | Qwen 3.5 9B | Obligations + timeline + enforcement |
| `src/agents/definition_actor.py` | GPT-OSS 20B | Definitions + actors + framework refs |
| `src/agents/threshold_exception.py` | GPT-OSS 20B | Thresholds + exceptions |
| `src/agents/ambiguity.py` | GPT-OSS 20B | Vague/ambiguous language analysis |
| `src/agents/rights_protection.py` | GPT-OSS 20B | Individual rights (notice, opt-out, appeal) |
| `src/agents/compliance_mechanism.py` | GPT-OSS 20B | Audits, assessments, reporting |
| `src/agents/discovery.py` | Qwen 3.5 9B | Bill classification (separate provider) |

### Pipeline
| File | Purpose |
|------|---------|
| `src/ingestion/extractor.py` | Extraction orchestrator (agent grouping, parallelism, dedup) |
| `src/ingestion/pipeline.py` | Fetch, store, parse pipeline |
| `src/ingestion/connector.py` | Source connectors (Colorado, Federal, Orrick) |

### API
| File | Purpose |
|------|---------|
| `src/api/app.py` | FastAPI entry point |
| `src/api/routes/dashboard.py` | Dashboard + pipeline control |
| `src/api/routes/v1.py` | Product API (obligations, matrix, changes) |
| `src/api/progress.py` | Progress tracking + ETA |

### Sync
| File | Purpose |
|------|---------|
| `src/scripts/sync_extractions.py` | Regs Checker → Policy Navigator sync |
| `src/scripts/sync_monitor.py` | Cross-database health monitor |

---

## 8. Known Issues / Next Steps

### Immediate

1. **Re-run extraction** — The current run was poisoned by DeepSeek failures. Restart the extraction pipeline; it will skip already-processed passages (dedup by content hash) and process remaining ones at ~48 seconds each.

2. **DeepSeek-R1 model may be unnecessary** — With all agents now on GPT or Qwen, DeepSeek-R1 is only used as a reasoning model detector (to double token budgets). Consider unloading it from LM Studio to free VRAM.

### Short-term

3. **Prompt tuning for GPT-OSS** — The system prompts were originally tuned for Claude Haiku. GPT-OSS may benefit from different instruction patterns. Monitor extraction quality and adjust.

4. **Passage-level parallelism** — Passages are processed sequentially (one at a time). With a fast enough GPU, processing 2-3 passages concurrently could halve total time. Requires LM Studio to handle concurrent requests to the same model.

5. **Recover truncated extractions** — Passages flagged with `truncated: true` in metadata need re-processing. The truncation flag (commit `44d2f2a`) makes these identifiable in the review queue.

### Medium-term

6. **Gold-standard evaluation** — Run the evaluation harness (`/dashboard/analytics` → Run Evaluation) to measure extraction quality with the new model assignments against annotated fixtures.

7. **Bridge coverage gaps** — `sync_extractions.py` silently skips extractions whose document family isn't in `law_document_bridge`. The sync monitor reports this but doesn't create missing entries.

---

## 9. Archived Documents

Previous handoffs are in `archive/`:
- `archive/HANDOFF.md` — Original 2026-03-11 handoff (initial extraction pipeline build)
- `archive/HANDOFF-2026-03-11.md` — Updated 2026-03-11 (sync infrastructure, batch API)
- `archive/handoff-2026-03-18.md` — 2026-03-18 (local LLM pivot plan, per-agent model override design)
- `archive/regs-checker-developer-handoff.docx` — Original developer handoff document
- `archive/LEADERSHIP_STRUCTURE.md` — Team structure document
- `archive/Initial Project Plan.md` — Original project plan
