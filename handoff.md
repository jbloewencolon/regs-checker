# Regs-Checker: Engineering Handoff

## Date: 2026-03-18

---

## 1. What This Project Is

Regs-checker is an AI Legal Corpus platform that scrapes US state and federal AI legislation, extracts structured regulatory obligations using LLM agents, and serves the results via a REST API. It powers a downstream product called Policy Navigator.

### Three-phase architecture

1. **Ingestion** (`src/ingestion/pipeline.py`): Scrape legislative documents (PDF/HTML) from state legislatures and trackers (Orrick, IAPP). Store raw bytes in MinIO. Parse and chunk into passage-level `NormalizedSourceRecord` rows.

2. **Extraction** (`src/ingestion/extractor.py`): Run 4 consolidated AI agents against each passage to extract structured obligations, definitions, thresholds, exceptions, and ambiguities. All LLM inference happens here — offline, batch-capable, never in the serving path.

3. **Serving** (`src/api/routes/v1.py`): Zero-latency REST API over pre-extracted data. PostgreSQL queries, materialized views, recursive CTEs. No LLM calls at request time.

### Key design principle

Inference is offline. Serving is instant. The LLM is a build tool, not a runtime dependency.

---

## 2. Current LLM Architecture

### Provider abstraction (`src/core/llm_provider.py`)

The system uses a strategy pattern with two provider implementations:

| Provider | Class | Default use | Model |
|---|---|---|---|
| `anthropic` | `AnthropicProvider` | Extraction agents | `claude-haiku-4-5-20251001` |
| `local` | `LocalLLMProvider` | Discovery agent | `llama-3.1-8b` via OpenAI-compatible API |

The factory function `get_provider(provider_type)` returns a cached singleton. Two convenience functions route by task:

```python
get_discovery_provider()   # reads settings.discovery_provider  (default: "local")
get_extraction_provider()  # reads settings.extraction_provider (default: "anthropic")
```

`LocalLLMProvider` speaks the OpenAI `/v1/chat/completions` protocol via `httpx`. It already works with Ollama, llama.cpp, vLLM, or any OpenAI-compatible server. No code changes are needed to point it at Ollama.

### The 4 extraction agents

| Agent | File | Task | LLM need |
|---|---|---|---|
| `ObligationAgent` | `src/agents/obligation.py` | Extract obligations + timeline + enforcement | Extractive (verbatim spans) |
| `DefinitionActorAgent` | `src/agents/definition_actor.py` | Extract definitions + actor roles + framework refs | Extractive (verbatim spans) |
| `ThresholdExceptionAgent` | `src/agents/threshold_exception.py` | Extract thresholds + exceptions | Extractive (verbatim spans) |
| `AmbiguityAgent` | `src/agents/ambiguity.py` | Meta-analysis of vague/conflicting language | Reasoning (interpretive) |

All four inherit from `BaseExtractionAgent` (`src/agents/base.py`), which:
- Calls `get_extraction_provider()` in `__init__` (line 53) — all agents share one provider
- Resolves prompts from YAML templates (`prompts/*.yml`) with Jinja2, falling back to inline Python strings
- Parses JSON output, validates with Pydantic strict mode
- Verifies evidence spans via string matching (`_verify_evidence_spans`, line 211)
- Tracks token usage and prompt hashes

### The quality gate: evidence span verification

This is the most critical piece of the system. Every extracted field must include an `evidence_spans` array containing verbatim text quotes. The base agent verifies each span via:

```python
if span.text in passage:  # exact substring match
    verified = True
```

If the LLM paraphrases instead of quoting verbatim, the span fails verification. Unverified spans lower the confidence score (computed in `src/core/confidence.py`), which determines the review tier (A through D) and whether the extraction needs human review.

**This is the single most important constraint for model selection.** A model that reasons beautifully but paraphrases its evidence will produce low-confidence extractions that flood the review queue.

### Discovery agent (`src/agents/discovery.py`)

Separate from the extraction agents. Uses `get_discovery_provider()` (default: local LLM). Handles bill classification ("is this AI legislation?") and metadata extraction (title, jurisdiction, bill number). No evidence span requirement — classification and metadata don't need verbatim quotes.

---

## 3. The Planned Pivot: Air-Gapped Local Inference

### Goal

Deprecate the Anthropic API dependency. Move to 100% local inference using Ollama on an AMD Radeon R9700 (RDNA 4, 32GB VRAM).

### What was proposed

1. **Ollama** on `localhost:11434` as the inference backend
2. **DeepSeek-R1-32B** as the primary model for all agents
3. **AnythingLLM** as a RAG orchestration layer with LanceDB
4. **32K context window** (`num_ctx: 32768`)
5. **Dual-model strategy**: 3B model for discovery, 32B for extraction

### What we evaluated and decided

| Component | Verdict | Rationale |
|---|---|---|
| Ollama as backend | **Keep** | Drop-in compatible with existing `LocalLLMProvider` |
| R1-32B for all agents | **Modify** | R1 is strong at reasoning but weak at verbatim extraction. Use Qwen2.5-32B for extractive agents, R1 only for ambiguity |
| AnythingLLM + RAG | **Drop** | The system doesn't use RAG. There is no semantic search step. Passages are processed exhaustively, not retrieved. AnythingLLM adds a redundant chunking pipeline, a vector DB nothing queries, and a service to maintain |
| 32K context | **Reduce to 8K** | Passages are 350-2000 chars (~100-500 tokens). System prompts add ~500-1000 tokens. 8K context covers the actual workload with massive headroom and saves ~4.5GB VRAM |
| Dual-model strategy | **Keep** | Maps directly onto existing `discovery_provider` / `extraction_provider` split |
| Per-agent model selection | **Add (Approach 1)** | See Section 4 below |

### Additional issues identified

1. **Batch API hard dependency**: `src/ingestion/extractor.py` has a direct `import anthropic` for the Anthropic Batch API (50% cost discount, 24h turnaround). This is not routed through the provider abstraction. It must be disabled or guarded when running local-only.

2. **Prompt template tuning**: The YAML prompts in `prompts/*.yml` and inline system prompts were tuned for Claude Haiku's behavior. Different models (Qwen2.5, R1) have different instruction-following patterns and will need prompt iteration.

3. **R1 `<think>` tags**: DeepSeek-R1 wraps its chain-of-thought reasoning in `<think>...</think>` tags that appear in the output. The current `_strip_code_fences` method (line 164 of `base.py`) does not handle these. They will corrupt JSON parsing.

---

## 4. Path Forward: Approach 1 (Per-Agent Model Override)

### The idea

Add an optional `model_override` parameter to `LocalLLMProvider.call()`. Each agent declares its preferred model as a class attribute. The base agent passes it through to the provider. If `model_override` is `None`, the global config model is used (backward compatible).

This lets us assign:
- **Qwen2.5-32B-Instruct** to obligation, definition/actor, and threshold/exception agents (extractive faithfulness — these agents need verbatim span copying)
- **DeepSeek-R1-32B** to the ambiguity agent (reasoning about vagueness, which benefits from chain-of-thought)
- **Llama-3.2-3B** (or similar small model) to the discovery agent (classification only, no spans needed)

### Exact files to modify

#### Step 1: Add `model_override` to `LocalLLMProvider.call()`

**File**: `src/core/llm_provider.py`
**Location**: Line 162, the `call` method signature
**Change**: Add an optional `model_override: str | None = None` parameter. Use it in the payload construction.

Before (line 162-180):
```python
def call(
    self,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> LLMResponse:
    import httpx

    payload = {
        "model": self._model,
        ...
    }
```

After:
```python
def call(
    self,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    model_override: str | None = None,
) -> LLMResponse:
    import httpx

    effective_model = model_override or self._model

    payload = {
        "model": effective_model,
        ...
    }
```

Also update `model_id` in the returned `LLMResponse` to use `effective_model` so that tracking is accurate:

```python
return LLMResponse(
    text=text,
    usage=usage,
    model_id=f"local:{effective_model}",  # was: self.model_id
    stop_reason=finish_reason,
)
```

Update the debug log similarly:
```python
logger.debug(
    "local_llm_response",
    model=effective_model,  # was: self._model
    ...
)
```

**Note**: `AnthropicProvider.call()` does NOT need this parameter. It won't be used once we deprecate Anthropic. But to keep the interface clean, add it to `BaseLLMProvider.call()` with a default of `None` and ignore it in `AnthropicProvider`.

#### Step 2: Add `model_override` to `BaseLLMProvider` abstract interface

**File**: `src/core/llm_provider.py`
**Location**: Line 54, the abstract `call` method

```python
@abstractmethod
def call(
    self,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 8192,
    temperature: float = 0.0,
    model_override: str | None = None,
) -> LLMResponse:
```

In `AnthropicProvider.call()` (line 82), add the parameter but ignore it:

```python
def call(
    self,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 8192,
    temperature: float = 0.0,
    model_override: str | None = None,  # ignored — Anthropic uses self._model
) -> LLMResponse:
```

#### Step 3: Add `model_override` class attribute to `BaseExtractionAgent`

**File**: `src/agents/base.py`
**Location**: Line 46-49, the class body

Before:
```python
class BaseExtractionAgent(ABC):
    agent_name: str = "base"
    max_retries: int = 1
```

After:
```python
class BaseExtractionAgent(ABC):
    agent_name: str = "base"
    max_retries: int = 1
    model_override: str | None = None  # per-agent model preference
```

#### Step 4: Pass `model_override` through in `_call_llm`

**File**: `src/agents/base.py`
**Location**: Line 175-197, the `_call_llm` method

Before (line 192):
```python
response = self._provider.call(
    system_prompt=system_prompt,
    user_prompt=prompt,
    max_tokens=settings.extraction_max_tokens,
    temperature=settings.extraction_temperature,
)
```

After:
```python
response = self._provider.call(
    system_prompt=system_prompt,
    user_prompt=prompt,
    max_tokens=settings.extraction_max_tokens,
    temperature=settings.extraction_temperature,
    model_override=self.model_override,
)
```

#### Step 5: Set model overrides on each agent

**File**: `src/agents/obligation.py` (line 14)
```python
class ObligationAgent(BaseExtractionAgent):
    agent_name = "obligation"
    model_override = "qwen2.5:32b-instruct-q4_K_M"
```

**File**: `src/agents/definition_actor.py` (line 14)
```python
class DefinitionActorAgent(BaseExtractionAgent):
    agent_name = "definition_actor"
    model_override = "qwen2.5:32b-instruct-q4_K_M"
```

**File**: `src/agents/threshold_exception.py` (line 15)
```python
class ThresholdExceptionAgent(BaseExtractionAgent):
    agent_name = "threshold_exception"
    model_override = "qwen2.5:32b-instruct-q4_K_M"
```

**File**: `src/agents/ambiguity.py` (line 15)
```python
class AmbiguityAgent(BaseExtractionAgent):
    agent_name = "ambiguity"
    model_override = "deepseek-r1:32b"
```

#### Step 6: Add `<think>` tag stripping for R1

**File**: `src/agents/base.py`
**Location**: After `_strip_code_fences` (line 164)

Add a new static method:
```python
@staticmethod
def _strip_think_tags(text: str) -> str:
    """Remove DeepSeek R1 <think>...</think> reasoning blocks from output."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
```

Add `import re` at the top of the file (it's not currently imported in `base.py`).

Then in the `extract` method (line 106), apply it before JSON parsing:

Before:
```python
cleaned = self._strip_code_fences(raw_output)
parsed = json.loads(cleaned)
```

After:
```python
cleaned = self._strip_code_fences(raw_output)
cleaned = self._strip_think_tags(cleaned)
parsed = json.loads(cleaned)
```

#### Step 7: Update `.env.example`

**File**: `.env.example`

Add after the Anthropic section:

```bash
# ---------------------------------------------------------------------------
# Local LLM (Ollama / vLLM / llama.cpp — OpenAI-compatible API)
# ---------------------------------------------------------------------------
# REGS_LOCAL_LLM_URL=http://localhost:11434
# REGS_LOCAL_LLM_MODEL=qwen2.5:32b-instruct-q4_K_M

# ---------------------------------------------------------------------------
# Provider routing — set both to "local" for air-gapped operation
# ---------------------------------------------------------------------------
# REGS_DISCOVERY_PROVIDER=local
# REGS_EXTRACTION_PROVIDER=local
```

#### Step 8: Guard the Anthropic Batch API import

**File**: `src/ingestion/extractor.py`

Anywhere the Batch API is used (search for `import anthropic` or `client.messages.batches`), wrap in a conditional:

```python
if settings.extraction_provider == "anthropic":
    import anthropic
    # ... batch API logic
else:
    raise ValueError(
        "Batch API requires extraction_provider='anthropic'. "
        "Local models process synchronously — no batch mode needed."
    )
```

Since local inference has zero per-token cost, the 50% batch discount is irrelevant. The synchronous path already works for local models.

---

## 5. VRAM Budget

With `num_ctx: 8192` (recommended) on the R9700 (32GB VRAM):

| Component | VRAM | Notes |
|---|---|---|
| Qwen2.5-32B (Q4_K_M) | ~20 GB | Primary extraction model |
| KV cache @ 8K context | ~1.5 GB | Per-request, freed after completion |
| Llama-3.2-3B (Q4_K_M) | ~2 GB | Discovery model (kept loaded) |
| OS + driver overhead | ~1.5 GB | ROCm or Vulkan runtime |
| **Total** | **~25 GB** | **~7 GB headroom** |

With `OLLAMA_MAX_LOADED_MODELS=2`, keep the discovery 3B model and one 32B model in VRAM simultaneously.

When the ambiguity agent needs R1 instead of Qwen2.5, Ollama swaps models. This takes 8-15 seconds on NVMe. See Section 6 for how to minimize swaps.

---

## 6. Model Swap Latency: The Concurrency Problem

### The problem

`extract_single_record` (line 342 of `extractor.py`) runs all selected agents **concurrently** via `ThreadPoolExecutor(max_workers=4)`. If the obligation agent (Qwen2.5) and ambiguity agent (R1) are both selected for the same passage, they submit concurrent requests to Ollama with different model names. Ollama serializes these — it loads one model, runs it, unloads it, loads the other. Under concurrent load this causes VRAM thrashing.

### The mitigation (not yet implemented — future work)

Restructure the extraction loop to batch by model. Instead of:

```
For each passage:
    Run all selected agents concurrently  ← agents may need different models
```

Do:

```
Phase 1: For each passage, run Qwen2.5 agents (obligation, definition, threshold)
Phase 2: For each passage, run R1 agents (ambiguity)
```

This keeps Ollama on one model per phase. The Qwen2.5 agents can still run concurrently with each other (they share a model). The ambiguity agent runs in a second pass.

**Estimated effort**: ~50 lines of changes in `extractor.py`. Moderate complexity — restructures the main extraction loop from a per-passage concurrent model to a two-phase sequential model. Not blocking for initial deployment; the single-model-at-a-time swap penalty is tolerable for small runs. Becomes important if processing hundreds of passages.

### Alternative: start with one model for everything

If model swapping proves too disruptive, use Qwen2.5-32B for all four agents initially. The ambiguity agent's quality may be slightly lower than with R1, but Qwen2.5 is still a strong model. Specialize later once you've validated extraction quality and established baseline metrics.

---

## 7. What to Validate Before Cutting Over

### Build a parallel evaluation harness

Before switching from Haiku to local models, run a side-by-side comparison:

1. Pick 50 passages from existing `NormalizedSourceRecord` rows (mix of obligation-heavy, definition-heavy, and ambiguous text).
2. Process each passage through both Haiku and the local model using the same prompts.
3. Compare:
   - **Evidence span verification rate**: What percentage of spans pass `span.text in passage`?
   - **Confidence tier distribution**: How many extractions land in Tier A vs B vs C vs D?
   - **JSON validity rate**: How often does output fail `json.loads` or Pydantic validation?
   - **Abstention accuracy**: Does the model correctly abstain on irrelevant passages?

This is a weekend of work that prevents a month of debugging.

### Prompt tuning expectations

- **Qwen2.5**: Generally follows JSON schema instructions well. May need explicit "do not include any text before or after the JSON" instructions. Test with the existing YAML templates first — they may work with minor adjustments.

- **DeepSeek-R1**: Will include `<think>...</think>` blocks. The Step 6 stripping handles this. May need stronger emphasis on verbatim quoting ("copy the EXACT characters from the passage, do not rephrase or clean up the text"). R1's reasoning mode actively encourages it to process and transform text, which fights against verbatim extraction.

---

## 8. Files Referenced in This Document

### Core system
- `src/core/config.py` — Configuration (Pydantic settings, env vars)
- `src/core/llm_provider.py` — Provider abstraction (the main file to modify)
- `src/core/confidence.py` — Confidence scoring rules

### Agents
- `src/agents/base.py` — Base extraction agent (the second file to modify)
- `src/agents/obligation.py` — Obligation + timeline + enforcement
- `src/agents/definition_actor.py` — Definitions + actors + framework refs
- `src/agents/threshold_exception.py` — Thresholds + exceptions
- `src/agents/ambiguity.py` — Ambiguity meta-analysis
- `src/agents/discovery.py` — Bill classification (already on local LLM)
- `src/agents/prompt_loader.py` — YAML template loading with Jinja2

### Ingestion
- `src/ingestion/extractor.py` — Extraction orchestrator (concurrent agent execution)
- `src/ingestion/pipeline.py` — Fetch, store, parse pipeline
- `src/ingestion/connector.py` — Source connectors (Colorado, Federal, Orrick)

### Prompt templates
- `prompts/obligation.yml`
- `prompts/definition_actor.yml`
- `prompts/threshold_exception.yml`
- `prompts/ambiguity.yml`

### Config
- `.env.example` — Environment variable template
- `pyproject.toml` — Dependencies (note: `anthropic>=0.40.0` can be moved to optional after pivot)

---

## 9. What Was NOT Changed in This Session

No code was modified. This session was analysis and planning only. The outputs are:

1. This handoff document
2. Evaluation of the original pivot proposal (AnythingLLM dropped, context window reduced, per-agent model selection recommended)
3. Feasibility assessment of Approach 1 with exact file locations and code changes specified

The next engineer should implement the changes described in Section 4, validate with the evaluation harness described in Section 7, then tune prompts as needed.
