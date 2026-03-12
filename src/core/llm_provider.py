"""LLM provider abstraction — routes calls to Anthropic API or local models.

Supports two providers:
  - anthropic: Claude API (Haiku for extraction — production quality)
  - local: Any OpenAI-compatible API server (llama.cpp, vLLM, Ollama, etc.)

The local provider targets Llama 3.1 8B (Q4/Q5 quantized) for discovery tasks
(bill classification, metadata extraction). Extraction agents continue to use
Anthropic Haiku for legal precision and evidence span quality.

Configuration via environment variables:
  REGS_LLM_PROVIDER          — "anthropic" (default) or "local"
  REGS_LOCAL_LLM_URL         — Base URL for local server (e.g. http://localhost:8080)
  REGS_LOCAL_LLM_MODEL       — Model name for local server (e.g. "llama-3.1-8b")
  REGS_DISCOVERY_PROVIDER    — Provider for discovery agent specifically ("local" default)
  REGS_EXTRACTION_PROVIDER   — Provider for extraction agents specifically ("anthropic" default)
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
        max_tokens: int = 8192,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Make a single LLM call. Returns provider-agnostic response."""

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Return the model identifier for tracking."""


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude API provider (Haiku for extraction)."""

    def __init__(self) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.extraction_model

    @property
    def model_id(self) -> str:
        return self._model

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 8192,
        temperature: float = 0.0,
    ) -> LLMResponse:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = ""
        for block in response.content:
            if block.type == "text":
                raw_text = block.text
                break

        if not raw_text.strip():
            raise ValueError(
                f"Empty text response from Anthropic "
                f"(stop_reason={response.stop_reason}, "
                f"content_types={[b.type for b in response.content]})"
            )

        return LLMResponse(
            text=raw_text,
            usage=LLMUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            model_id=self._model,
            stop_reason=response.stop_reason,
        )


class LocalLLMProvider(BaseLLMProvider):
    """Local LLM provider via OpenAI-compatible API (llama.cpp, vLLM, Ollama).

    Targets Llama 3.1 8B (Q4/Q5) for discovery tasks. Communicates via
    the OpenAI chat completions API format, which is supported by:
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

    @property
    def model_id(self) -> str:
        return f"local:{self._model}"

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
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }

        response = httpx.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()

        # Parse OpenAI-compatible response
        choice = data["choices"][0]
        text = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")

        usage_data = data.get("usage", {})
        usage = LLMUsage(
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
        )

        if not text or not text.strip():
            raise ValueError(
                f"Empty response from local LLM "
                f"(finish_reason={finish_reason}, model={self._model})"
            )

        logger.debug(
            "local_llm_response",
            model=self._model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            finish_reason=finish_reason,
            response_length=len(text),
        )

        return LLMResponse(
            text=text,
            usage=usage,
            model_id=self.model_id,
            stop_reason=finish_reason,
        )


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

_provider_cache: dict[str, BaseLLMProvider] = {}


def get_provider(provider_type: str | None = None) -> BaseLLMProvider:
    """Get or create an LLM provider by type.

    Args:
        provider_type: "anthropic", "local", or None (uses REGS_LLM_PROVIDER default).

    Returns:
        A cached provider instance.
    """
    resolved = provider_type or settings.llm_provider

    if resolved in _provider_cache:
        return _provider_cache[resolved]

    if resolved == "anthropic":
        provider = AnthropicProvider()
    elif resolved == "local":
        provider = LocalLLMProvider()
    else:
        raise ValueError(f"Unknown LLM provider: {resolved!r}. Use 'anthropic' or 'local'.")

    _provider_cache[resolved] = provider
    logger.info("llm_provider_created", provider=resolved, model=provider.model_id)
    return provider


def get_discovery_provider() -> BaseLLMProvider:
    """Get the provider configured for discovery tasks (default: local)."""
    return get_provider(settings.discovery_provider)


def get_extraction_provider() -> BaseLLMProvider:
    """Get the provider configured for extraction tasks (default: anthropic)."""
    return get_provider(settings.extraction_provider)
