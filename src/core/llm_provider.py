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
    ) -> LLMResponse:
        import httpx

        effective_model = model_override or self._model

        # Reasoning models (DeepSeek-R1, Qwen3 in thinking mode) use output
        # tokens for <think> blocks before producing JSON.  Double the budget
        # so the actual answer isn't truncated after the thinking phase.
        is_reasoning = any(
            tag in effective_model.lower()
            for tag in ("deepseek-r1", "qwen3", "gpt-oss")
        )
        adjusted_max = max_tokens * 2 if is_reasoning else max_tokens

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

        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort

        # Reasoning models (DeepSeek-R1, Qwen3) spend thousands of tokens
        # on chain-of-thought before producing JSON — they need a longer
        # timeout than normal models to avoid client disconnects.
        is_reasoning_model = any(
            tag in effective_model.lower()
            for tag in ("deepseek-r1", "qwen3", "gpt-oss")
        )
        request_timeout = 300.0 if is_reasoning_model else 120.0

        response = httpx.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
            timeout=request_timeout,
        )
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

        effective_model_id = self.normalize_model_id(effective_model)

        logger.debug(
            "local_llm_response",
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
    else:
        raise ValueError(f"Unknown LLM provider: {resolved!r}. Use 'local'.")

    _provider_cache[resolved] = provider
    logger.info("llm_provider_created", provider=resolved, model=provider.model_id)
    return provider


def get_discovery_provider() -> BaseLLMProvider:
    """Get the provider configured for discovery tasks."""
    return get_provider(settings.discovery_provider)


def get_extraction_provider() -> BaseLLMProvider:
    """Get the provider configured for extraction tasks.

    Uses ``local_extraction_model`` as the base model. Per-agent
    ``model_override`` attributes still take precedence at call time.
    """
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
