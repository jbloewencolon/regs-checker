"""LLM provider abstraction — routes calls to local models via OpenAI-compatible API.

All extraction and discovery tasks now use local models (LM Studio, llama.cpp,
vLLM, Ollama, etc.) via the OpenAI chat completions API format. The Anthropic
API provider has been archived.

Configuration via environment variables:
  REGS_LOCAL_LLM_URL           — Base URL for local server (e.g. http://localhost:1234)
  REGS_LOCAL_LLM_MODEL         — Model name for discovery tasks (e.g. "openai/gpt-oss-20b")
  REGS_LOCAL_EXTRACTION_MODEL  — Model name for extraction tasks
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import structlog

from src.core.config import settings

logger = structlog.get_logger()


@dataclass
class LLMUsage:
    """Token usage from an LLM call, provider-agnostic."""

    input_tokens: int
    output_tokens: int


@dataclass
class LLMResponse:
    """Response from an LLM call, provider-agnostic."""

    text: str
    usage: LLMUsage
    model_id: str
    stop_reason: str | None = None


class BaseLLMProvider(ABC):
    """Abstract base for LLM providers."""

    @abstractmethod
    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 16384,
        temperature: float = 0.0,
        model_override: str | None = None,
        reasoning_effort: str | None = None,
        top_p: float | None = None,
    ) -> LLMResponse:
        """Make a single LLM call. Returns provider-agnostic response.

        Args:
            model_override: If provided, use this model instead of the default.
            reasoning_effort: "low", "medium", or "high". Supported by models
                              like openai/gpt-oss-20b; ignored by others.
        """

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Return the default model identifier for tracking."""


class LocalLLMProvider(BaseLLMProvider):
    """Local LLM provider via OpenAI-compatible API (LM Studio, llama.cpp, vLLM, Ollama).

    Communicates via the OpenAI chat completions API format, which is supported by:
      - LM Studio (http://localhost:1234)
      - llama.cpp server (--host 0.0.0.0 --port 8080)
      - vLLM (python -m vllm.entrypoints.openai.api_server)
      - Ollama (ollama serve)
      - text-generation-inference
    """

    # Models confirmed to reject the reasoning_effort parameter (HTTP 400).
    # Populated at runtime on first rejection; shared across all instances so
    # subsequent calls skip the parameter entirely instead of wasting a round-trip.
    _reasoning_effort_unsupported: set[str] = set()

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._base_url = (
            base_url
            or settings.local_llm_url
        )
        self._model = (
            model
            or settings.local_llm_model
        )

        if not self._base_url:
            raise ValueError(
                "Local LLM URL not configured. Set REGS_LOCAL_LLM_URL "
                "or pass base_url to LocalLLMProvider."
            )

        logger.info(
            "local_llm_provider_init",
            base_url=self._base_url,
            model=self._model,
        )

    @staticmethod
    def normalize_model_id(model_name: str) -> str:
        """Normalize a local model name into a clean model_id.

        ``deepseek/deepseek-r1-0528-qwen3-8b`` → ``deepseek-deepseek-r1-0528-qwen3-8b-local``
        ``qwen/qwen3.5-9b`` → ``qwen-qwen3.5-9b-local``
        """
        return model_name.replace(":", "-").replace("/", "-") + "-local"

    @property
    def model_id(self) -> str:
        return self.normalize_model_id(self._model)

    @staticmethod
    def _truncate_repetition(text: str, min_block: int = 40) -> tuple[str, bool]:
        """Detect and truncate repetitive output loops.

        Some models (especially at high token budgets) fall into loops
        where they repeat the same block of text over and over.  This
        wastes tokens and produces unparseable output.

        Strategy: find the longest substring of length >= *min_block*
        that appears 3+ times.  If found, keep only up to the start of
        the third occurrence, then let the JSON repair handle closing.

        Returns (possibly_truncated_text, was_looping).
        """
        if len(text) < min_block * 3:
            return text, False

        # Try progressively smaller block sizes to find a repeating loop
        best_block = ""
        for block_len in range(min(200, len(text) // 4), min_block - 1, -1):
            # Sample from the second half of the text (loops develop later)
            mid = len(text) // 2
            sample = text[mid : mid + block_len]
            if not sample.strip():
                continue
            count = text.count(sample)
            if count >= 3:
                best_block = sample
                break

        if not best_block:
            return text, False

        # Keep up to the second occurrence, cut at the third
        first = text.index(best_block)
        second_start = text.index(best_block, first + len(best_block))
        second_end = second_start + len(best_block)
        try:
            third_start = text.index(best_block, second_end)
        except ValueError:
            return text, False

        truncated = text[:third_start].rstrip()
        logger.warning(
            "llm_output_loop_detected",
            repeated_block_len=len(best_block),
            repeat_count=text.count(best_block),
            original_len=len(text),
            truncated_len=len(truncated),
        )
        return truncated, True

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token count estimate (~4 chars per token for English text)."""
        return len(text) // 4

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 16384,
        temperature: float = 0.0,
        model_override: str | None = None,
        reasoning_effort: str | None = None,
        top_p: float | None = None,
    ) -> LLMResponse:
        import httpx

        effective_model = model_override or self._model

        # Reasoning models (DeepSeek-R1, Qwen3 in thinking mode) use output
        # tokens for <think> blocks before producing JSON.  Double the budget
        # so the actual answer isn't truncated after the thinking phase.
        # Skip doubling when reasoning is suppressed ("low" for gpt-oss, "off" for Gemma).
        is_reasoning = any(
            tag in effective_model.lower()
            for tag in ("deepseek-r1", "qwen3", "gpt-oss", "gemma")
        )
        if is_reasoning and reasoning_effort not in ("low", "off"):
            adjusted_max = max_tokens * 2
        else:
            adjusted_max = max_tokens

        # Cap max_tokens to fit within context window.
        # LM Studio needs: prompt_tokens + max_tokens <= n_ctx.
        # Use a rough estimate (4 chars ≈ 1 token) with a safety margin.
        context_limit = settings.local_context_length
        estimated_prompt_tokens = self._estimate_tokens(system_prompt + user_prompt)
        # Reserve 10% margin for tokenizer differences
        available = int(context_limit * 0.9) - estimated_prompt_tokens
        if available < 512:
            logger.warning(
                "local_llm_prompt_near_context_limit",
                model=effective_model,
                estimated_prompt_tokens=estimated_prompt_tokens,
                context_limit=context_limit,
                available=available,
            )
            available = 512  # Minimum viable output
        effective_max_tokens = min(adjusted_max, available)

        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": effective_max_tokens,
            "temperature": temperature,
            "stream": False,
        }

        if reasoning_effort is not None and effective_model not in self._reasoning_effort_unsupported:
            payload["reasoning_effort"] = reasoning_effort
        if top_p is not None:
            payload["top_p"] = top_p

        # Large models and reasoning models need longer timeouts.
        # Gemma 4 26B at 16k max_tokens can take 3+ minutes on dense
        # passages.  Use 300s for known slow models, 120s for others.
        slow_model = any(
            tag in effective_model.lower()
            for tag in ("deepseek-r1", "qwen3", "gpt-oss", "gemma", "70b", "72b")
        )
        request_timeout = 300.0 if slow_model else 120.0

        response = httpx.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
            timeout=request_timeout,
        )
        if response.status_code == 400 and payload.get("reasoning_effort") is not None:
            # Some models (e.g. Gemma 4 on LM Studio) don't support the
            # reasoning_effort parameter and return 400 when it's present.
            # Cache this so future calls skip the parameter entirely.
            self._reasoning_effort_unsupported.add(effective_model)
            logger.warning(
                "local_llm_reasoning_effort_rejected",
                model=effective_model,
                reasoning_effort=payload["reasoning_effort"],
            )
            del payload["reasoning_effort"]
            response = httpx.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                timeout=request_timeout,
            )

        # Gemma 4 structured thinking: model emits <|channel>thought\n<channel|>JSON
        # which LM Studio can't tokenize and returns HTTP 400. The actual JSON output
        # appears after the <channel|> marker in the error body — recover it.
        if response.status_code == 400:
            try:
                err_body = response.json()
                err_msg = err_body.get("error", "")
                marker = "<channel|>"
                if marker in err_msg:
                    recovered_text = err_msg[err_msg.index(marker) + len(marker):].strip()
                    if recovered_text:
                        # Validate it looks like JSON before trusting it
                        import json as _json
                        _json.loads(recovered_text)  # raises if not valid JSON
                        logger.warning(
                            "local_llm_channel_thought_recovered",
                            model=effective_model,
                            recovered_len=len(recovered_text),
                        )
                        text = recovered_text
                        finish_reason = "stop"
                        usage = LLMUsage(
                            input_tokens=0,
                            output_tokens=len(recovered_text) // 4,
                        )
                        text, was_looping = self._truncate_repetition(text)
                        return LLMResponse(
                            text=text,
                            usage=usage,
                            model_id=self.normalize_model_id(effective_model),
                            stop_reason="loop" if was_looping else finish_reason,
                        )
            except Exception:
                pass  # Not recoverable; fall through to standard error

        if response.status_code >= 400:
            # Include response body in error for diagnostics (LM Studio
            # often returns useful model-not-loaded messages in the body)
            body = response.text[:500] if response.text else ""
            raise httpx.HTTPStatusError(
                f"HTTP {response.status_code} for model {effective_model}: {body}",
                request=response.request,
                response=response,
            )
        data = response.json()

        # Parse OpenAI-compatible response
        choice = data["choices"][0]
        text = choice["message"]["content"] or ""
        finish_reason = choice.get("finish_reason")

        # gpt-oss-20b and similar reasoning models put chain-of-thought
        # in a separate "reasoning" field.  If content is empty but
        # reasoning exists, the model exhausted its token budget on
        # thinking.  Include the finish_reason in the error so callers
        # can distinguish truncation from true empty responses.
        reasoning_field = choice["message"].get("reasoning")

        usage_data = data.get("usage", {})
        usage = LLMUsage(
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
        )

        if not text.strip():
            detail = ""
            if reasoning_field and finish_reason == "length":
                detail = (
                    " Model spent all tokens on reasoning with no content produced. "
                    "Increase max_tokens."
                )
            raise ValueError(
                f"Empty response from local LLM "
                f"(finish_reason={finish_reason}, model={effective_model}){detail}"
            )

        # Strip <think> blocks that reasoning models (DeepSeek-R1, Qwen3)
        # may emit — they consume output tokens and can cause finish_reason=length
        # before the actual JSON answer is complete.
        import re
        # Match both closed <think>...</think> and unclosed <think>... (truncated)
        stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        stripped = re.sub(r"<think>.*$", "", stripped, flags=re.DOTALL).strip()
        if finish_reason == "length" and not stripped:
            raise ValueError(
                f"Empty response from local LLM "
                f"(finish_reason={finish_reason}, model={effective_model})"
            )
        if stripped != text.strip():
            logger.debug(
                "local_llm_stripped_think_block",
                model=effective_model,
                original_len=len(text),
                stripped_len=len(stripped),
            )
            text = stripped

        # Detect and truncate output loops before returning
        text, was_looping = self._truncate_repetition(text)

        effective_model_id = self.normalize_model_id(effective_model)

        logger.debug(
            "local_llm_response",
            model=effective_model,
            model_id=effective_model_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            finish_reason=finish_reason,
            response_length=len(text),
            looping_detected=was_looping,
        )

        return LLMResponse(
            text=text,
            usage=usage,
            model_id=effective_model_id,
            # When a repetition loop was detected the text has already been
            # truncated to just before the third repetition.  Report "loop"
            # rather than the raw finish_reason ("length") so the retry logic
            # in extract() does NOT escalate the token budget — more tokens
            # only extend the loop; the repaired fragment is the best we can do.
            stop_reason="loop" if was_looping else finish_reason,
        )


# ---------------------------------------------------------------------------
# NVIDIA hosted LLM provider
# ---------------------------------------------------------------------------


class NvidiaLLMProvider(BaseLLMProvider):
    """NVIDIA-hosted LLM via OpenAI-compatible API (integrate.api.nvidia.com).

    IMPORTANT: The NVIDIA base URL already includes ``/v1``.  This class
    therefore POSTs to ``{base_url}/chat/completions`` — NOT ``/v1/chat/completions``
    — to avoid a double-``/v1`` path that returns 404.

    API key is read from ``NVIDIA_API_KEY`` (no ``REGS_`` prefix).
    Provider is selected by setting ``REGS_EXTRACTION_PROVIDER=nvidia``.
    """

    # gpt-oss-120b may emit reasoning_content in the response.
    # We always use the ``content`` field for extraction output and ignore
    # reasoning_content — the caller sets reasoning_effort="off" in AgentModelConfig
    # but NVIDIA may not honour that parameter.  Stripping is defensive.
    _REASONING_CONTENT_KEY = "reasoning_content"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._base_url = (base_url or settings.nvidia_base_url).rstrip("/")
        self._model = model or settings.nvidia_extraction_model
        self._api_key = settings.nvidia_api_key

        if not self._api_key:
            raise ValueError(
                "NVIDIA_API_KEY is not set.  Add it to .env or your CI secrets "
                "before using the NVIDIA provider."
            )
        if not self._base_url:
            raise ValueError("REGS_NVIDIA_BASE_URL is not configured.")

        logger.info(
            "nvidia_llm_provider_init",
            base_url=self._base_url,
            model=self._model,
        )

    @property
    def model_id(self) -> str:
        return self._model.replace("/", "-") + "-nvidia"

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 16384,
        temperature: float = 0.0,
        model_override: str | None = None,
        reasoning_effort: str | None = None,
        top_p: float | None = None,
    ) -> LLMResponse:
        import time

        import httpx

        effective_model = model_override or self._model

        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }

        # Optional inference parameters — only include when explicitly set so that
        # omitting them lets the NVIDIA API apply its own defaults.
        if top_p is not None:
            payload["top_p"] = top_p
        if reasoning_effort is not None:
            # NVIDIA's reasoning models only accept 'low' | 'medium' | 'high'.
            # There is no true "off" — the minimum is 'low'. Coerce any
            # disable-style value (carried over from the local LM Studio config,
            # which does support "off") to 'low' so the request doesn't 400.
            effort = reasoning_effort.lower()
            if effort in ("off", "none", "disabled", "minimal"):
                effort = "low"
            payload["reasoning_effort"] = effort

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

        _max_retries = 5
        for attempt in range(_max_retries + 1):
            # NVIDIA base URL already ends in /v1 — append only /chat/completions.
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=300.0,
            )

            if response.status_code in (401, 403):
                raise httpx.HTTPStatusError(
                    f"NVIDIA auth/entitlement error (HTTP {response.status_code}) — "
                    "verify NVIDIA_API_KEY and model access in your NVIDIA account.",
                    request=response.request,
                    response=response,
                )

            if response.status_code == 429:
                if attempt < _max_retries:
                    wait_s = 2 ** attempt  # 1 s, 2 s, 4 s, 8 s, 16 s
                    logger.warning(
                        "nvidia_rate_limited_retrying",
                        model=effective_model,
                        attempt=attempt + 1,
                        wait_s=wait_s,
                    )
                    time.sleep(wait_s)
                    continue
                logger.warning(
                    "nvidia_quota_exhausted",
                    model=effective_model,
                    status=429,
                )
                raise httpx.HTTPStatusError(
                    "NVIDIA rate/quota limit hit (429).  Check credits and RPM limits "
                    "in your NVIDIA account before retrying.",
                    request=response.request,
                    response=response,
                )

            if response.status_code >= 400:
                body = response.text[:500] if response.text else ""
                raise httpx.HTTPStatusError(
                    f"NVIDIA API HTTP {response.status_code} for model {effective_model}: {body}",
                    request=response.request,
                    response=response,
                )

            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
            text = (message.get("content") or "").strip()
            finish_reason = choice.get("finish_reason")

            # Log unexpected reasoning tokens (means reasoning_effort was not honoured).
            if message.get(self._REASONING_CONTENT_KEY):
                logger.warning(
                    "nvidia_unexpected_reasoning_content",
                    model=effective_model,
                    reasoning_len=len(message[self._REASONING_CONTENT_KEY]),
                )

            usage_data = data.get("usage", {})
            usage = LLMUsage(
                input_tokens=usage_data.get("prompt_tokens", 0),
                output_tokens=usage_data.get("completion_tokens", 0),
            )

            if not text:
                raise ValueError(
                    f"Empty response from NVIDIA LLM "
                    f"(finish_reason={finish_reason}, model={effective_model})"
                )

            effective_model_id = effective_model.replace("/", "-") + "-nvidia"

            logger.debug(
                "nvidia_llm_response",
                model=effective_model,
                model_id=effective_model_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                finish_reason=finish_reason,
                response_length=len(text),
            )

            return LLMResponse(
                text=text,
                usage=usage,
                model_id=effective_model_id,
                stop_reason=finish_reason,
            )

        raise RuntimeError(f"NVIDIA call exhausted all {_max_retries} retries for {effective_model}")


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

_provider_cache: dict[str, BaseLLMProvider] = {}


def get_provider(provider_type: str | None = None) -> BaseLLMProvider:
    """Get or create an LLM provider.

    Args:
        provider_type: "local" or None (uses default). Legacy "anthropic"
                       values are silently mapped to "local".

    Returns:
        A cached provider instance.
    """
    resolved = provider_type or settings.llm_provider

    # Map legacy "anthropic" references to local
    if resolved == "anthropic":
        resolved = "local"

    if resolved in _provider_cache:
        return _provider_cache[resolved]

    if resolved == "local":
        provider = LocalLLMProvider()
    elif resolved == "nvidia":
        provider = NvidiaLLMProvider()
    else:
        raise ValueError(f"Unknown LLM provider: {resolved!r}. Use 'local' or 'nvidia'.")

    _provider_cache[resolved] = provider
    logger.info("llm_provider_created", provider=resolved, model=provider.model_id)
    return provider


def get_discovery_provider() -> BaseLLMProvider:
    """Get the provider configured for discovery tasks."""
    return get_provider(settings.discovery_provider)


def clear_provider_cache() -> None:
    """Drop all cached provider instances.

    Call this after switching providers (e.g. the dashboard provider toggle)
    so the next ``get_extraction_provider()`` rebuilds against the new backend.
    """
    _provider_cache.clear()
    logger.info("llm_provider_cache_cleared")


def get_extraction_provider() -> BaseLLMProvider:
    """Get the provider configured for extraction tasks.

    Resolution order (first non-empty wins):
    1. ``ModelConfigStore.provider`` — the runtime source of truth driven by the
       dashboard provider toggle (persisted in config/agent_models.json).
    2. ``REGS_EXTRACTION_PROVIDER`` env / settings fallback.
    3. ``REGS_LLM_PROVIDER`` env / settings fallback.

    Values: "local" → LocalLLMProvider; "nvidia" → NvidiaLLMProvider.
    Per-agent ``model_override`` attributes still take precedence at call time.
    """
    # Lazy import avoids a circular dependency at module load time.
    from src.core.model_config import get_config

    effective = (
        get_config().provider
        or settings.extraction_provider
        or settings.llm_provider
    )
    if effective == "nvidia":
        cache_key = "nvidia_extraction"
        if cache_key not in _provider_cache:
            _provider_cache[cache_key] = NvidiaLLMProvider()
            logger.info(
                "llm_provider_created",
                provider=cache_key,
                model=_provider_cache[cache_key].model_id,
            )
        return _provider_cache[cache_key]

    # Default: local
    cache_key = "local_extraction"
    if cache_key not in _provider_cache:
        _provider_cache[cache_key] = LocalLLMProvider(
            model=settings.local_extraction_model,
        )
        logger.info(
            "llm_provider_created",
            provider=cache_key,
            model=_provider_cache[cache_key].model_id,
        )
    return _provider_cache[cache_key]
