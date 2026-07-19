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

import json as _json
import random

# NIM-0b — NVIDIA does not publish a documented 429 body format (per the NIM
# throughput review), so this is a best-effort keyword read, not an
# authoritative parse. Rate-limited markers are checked first and a bare
# "quota" mention is deliberately NOT treated as decisive on its own: RPM
# throttling is commonly phrased as "queries per minute quota", so that word
# alone can't distinguish transient throttling from a harder allowance wall.
_RATE_LIMITED_429_MARKERS = (
    "per minute", "per second", "per hour", "rpm", "requests per",
    "too many requests", "retry after", "slow down", "throttle",
)
_ALLOWANCE_EXHAUSTED_429_MARKERS = (
    "credit", "balance", "quota exhausted", "quota depleted",
    "trial has ended", "trial period has ended", "monthly limit",
    "plan limit", "no longer available", "exceeded your plan",
)


def _classify_429_body(body: str) -> str:
    """Classify a 429 body as transient RPM throttling vs. a harder
    allowance/quota wall vs. genuinely unclear.

    Returns "rate_limited_transient", "allowance_exhausted", or
    "429_unclassified" — the last is the honest default when the body gives
    no decisive signal either way, rather than guessing.
    """
    if not body:
        return "429_unclassified"
    text = body.lower()
    if any(marker in text for marker in _RATE_LIMITED_429_MARKERS):
        return "rate_limited_transient"
    if any(marker in text for marker in _ALLOWANCE_EXHAUSTED_429_MARKERS):
        return "allowance_exhausted"
    return "429_unclassified"


def _parse_retry_after_seconds(response: Any) -> float | None:
    """Parse a numeric Retry-After header (seconds) if present and valid.

    NVIDIA does not always send this header (per the NIM throughput review),
    and the HTTP-date form isn't handled here — an absent or unparseable
    header just falls back to the jittered exponential backoff below."""
    raw = response.headers.get("retry-after") if hasattr(response, "headers") else None
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _compute_backoff_seconds(attempt: int, retry_after: float | None = None) -> float:
    """Exponential backoff with jitter, capped, honoring Retry-After when given.

    A server-supplied Retry-After takes precedence over our own guess (it's
    the more authoritative signal of when to retry); jitter keeps several
    agents throttled at the same instant from all retrying in lockstep."""
    base = retry_after if retry_after is not None else float(2 ** attempt)
    capped = min(base, settings.nvidia_retry_backoff_cap_seconds)
    jitter_fraction = settings.nvidia_retry_jitter_fraction
    jitter = capped * random.uniform(-jitter_fraction, jitter_fraction)
    return max(0.0, capped + jitter)


class _RateLimited(Exception):
    """Internal signal for a 429 response — carries the request/response so
    the retry loop can build the existing HTTPStatusError message on final
    exhaustion, without httpx.stream()'s context manager having already
    closed the response by the time the retry loop's except clause runs.

    Also carries the NIM-0b classification and a short body excerpt, so
    logs and downstream telemetry can distinguish transient RPM throttling
    from a harder allowance wall instead of collapsing both into one
    "quota exhausted" label."""

    def __init__(
        self,
        request: Any,
        response: Any,
        classification: str = "429_unclassified",
        body_excerpt: str = "",
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__("NVIDIA rate limited (429)")
        self.request = request
        self.response = response
        self.classification = classification
        self.body_excerpt = body_excerpt
        self.retry_after_seconds = retry_after_seconds


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

    # How long we tolerate silence between streamed chunks before treating the
    # call as stalled. With streaming, silence normally means "nothing is
    # happening" rather than "still generating" (which shows up as steady,
    # if slow, chunk arrivals and never trips this) — EXCEPT for reasoning
    # models, which can spend well over a minute "thinking" server-side
    # before emitting a single byte, especially on dense legal passages.
    # NVIDIA's hosted endpoint does not appear to stream any interim signal
    # during that phase, so a short idle timeout kills calls that were
    # working fine and would have succeeded — this is exactly what the old
    # blind 300s whole-response wait tolerated without anyone noticing.
    # Reasoning models get a longer allowance; everything else keeps the
    # tighter one, since non-reasoning models should start streaming almost
    # immediately and a stall there really does mean stuck.
    _IDLE_TIMEOUT_SECONDS = 60.0
    _IDLE_TIMEOUT_REASONING_SECONDS = 180.0
    _REASONING_MODEL_TAGS = ("deepseek-r1", "qwen3", "gpt-oss")

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

        from src.core.cancellation import OperationCancelled, is_cancelled

        effective_model = model_override or self._model

        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            # Streamed (not stream: False) so callers can tell "still
            # generating" (chunks keep arriving) from "actually stuck" (no
            # chunk within _IDLE_TIMEOUT_SECONDS) instead of blocking blind
            # for up to 300s with zero signal either way.
            "stream": True,
            "stream_options": {"include_usage": True},
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
            "Accept": "text/event-stream",
        }

        # Reasoning models can go quiet server-side for well over a minute
        # before their first streamed byte — give them the longer allowance
        # so that phase isn't mistaken for a stall (see _IDLE_TIMEOUT_REASONING_SECONDS).
        is_reasoning = any(tag in effective_model.lower() for tag in self._REASONING_MODEL_TAGS)
        idle_timeout = (
            self._IDLE_TIMEOUT_REASONING_SECONDS if is_reasoning else self._IDLE_TIMEOUT_SECONDS
        )

        # connect/write/pool bound the request setup; read bounds each
        # individual chunk — this IS the idle-stall detector.
        timeout = httpx.Timeout(
            connect=30.0, read=idle_timeout, write=30.0, pool=30.0,
        )

        def _sleep_cancellable(seconds: float) -> None:
            """Sleep in small increments so a cancel during backoff is
            noticed within ~0.5s instead of waiting out the full backoff."""
            elapsed = 0.0
            step = 0.5
            while elapsed < seconds:
                if is_cancelled():
                    raise OperationCancelled(
                        "Extraction cancelled by operator (during retry backoff)."
                    )
                time.sleep(min(step, seconds - elapsed))
                elapsed += step

        from src.core.llm_rate_limiter import get_rate_limiter
        from src.core.llm_rate_telemetry import get_llm_rate_telemetry

        telemetry = get_llm_rate_telemetry()
        rate_limiter = get_rate_limiter()
        was_rate_limited_this_call = False

        _max_retries = settings.nvidia_max_retries
        for attempt in range(_max_retries + 1):
            if is_cancelled():
                raise OperationCancelled("Extraction cancelled by operator.")

            try:
                # NIM-1a: block here (not just react to a 429 afterward) if
                # this model is already at its configured RPM cap — the
                # guardrail that lets concurrency be raised into the
                # headroom NIM-0a's telemetry measures, without reproducing
                # the throttling problem faster. A cancellable sleep so a
                # cancelled run doesn't sit through a full pacing wait.
                pacing_wait_s = rate_limiter.acquire(
                    effective_model, settings.nvidia_rpm_limit, sleep_fn=_sleep_cancellable,
                )
                if pacing_wait_s > 0:
                    telemetry.record_pacing_wait(effective_model, pacing_wait_s)
                telemetry.record_request(effective_model)
                result = self._stream_chat_completion(
                    payload, headers, timeout, effective_model,
                )
                telemetry.record_tokens(
                    effective_model, result.usage.input_tokens, result.usage.output_tokens,
                )
                if was_rate_limited_this_call:
                    telemetry.record_rate_limited_recovered(effective_model)
                return result
            except OperationCancelled:
                raise
            except httpx.TransportError as exc:
                # Connection-level failures (server drops the socket, read/idle
                # timeout, DNS/connect errors) — including the idle-stall
                # ReadTimeout above, since httpx.ReadTimeout is itself a
                # TransportError subclass. These are transient, so retry with
                # jittered exponential backoff (NIM-0c: jitter keeps several
                # concurrently-throttled agents from retrying in lockstep).
                if attempt < _max_retries:
                    wait_s = _compute_backoff_seconds(attempt)
                    logger.warning(
                        "nvidia_transport_error_retrying",
                        model=effective_model,
                        attempt=attempt + 1,
                        wait_s=round(wait_s, 2),
                        error=str(exc)[:200],
                    )
                    _sleep_cancellable(wait_s)
                    continue
                logger.error(
                    "nvidia_transport_error_exhausted",
                    model=effective_model,
                    error=str(exc)[:200],
                )
                raise
            except _RateLimited as exc:
                was_rate_limited_this_call = True
                telemetry.record_rate_limited(effective_model)
                if attempt < _max_retries:
                    wait_s = _compute_backoff_seconds(attempt, retry_after=exc.retry_after_seconds)
                    logger.warning(
                        "nvidia_rate_limited_retrying",
                        model=effective_model,
                        attempt=attempt + 1,
                        wait_s=round(wait_s, 2),
                        classification=exc.classification,
                        retry_after_honored=exc.retry_after_seconds is not None,
                    )
                    _sleep_cancellable(wait_s)
                    continue
                # NIM-0b: was "nvidia_quota_exhausted" — that name asserted an
                # exhausted balance we never verified. Every retry-exhausted
                # 429 lands here whether it's sustained RPM throttling or a
                # genuine allowance wall; `classification` is our best-effort
                # read of which (see _classify_429_body), not a claim either
                # way is confirmed.
                telemetry.record_rate_limited_exhausted(effective_model)
                logger.warning(
                    "nvidia_429_exhausted",
                    model=effective_model,
                    status=429,
                    classification=exc.classification,
                    body_excerpt=exc.body_excerpt,
                )
                http_err = httpx.HTTPStatusError(
                    "NVIDIA rate/quota limit hit (429).  Check credits and RPM limits "
                    "in your NVIDIA account before retrying.",
                    request=exc.request,
                    response=exc.response,
                )
                # Carried through as a plain attribute (not part of httpx's
                # constructor) so extractor.py's error classifier can surface
                # the finer-grained read without changing its existing
                # "quota_error" bucket — see _classify_429_detail.
                http_err.nvidia_429_classification = exc.classification
                raise http_err from None

        raise RuntimeError(f"NVIDIA call exhausted all {_max_retries} retries for {effective_model}")

    def _stream_chat_completion(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: Any,
        effective_model: str,
    ) -> LLMResponse:
        """Perform one streamed chat-completion attempt.

        Raises httpx.TransportError (incl. ReadTimeout on stall),
        _RateLimited on 429, httpx.HTTPStatusError on other HTTP errors, or
        src.core.cancellation.OperationCancelled if cancelled mid-stream.
        """
        import httpx

        from src.core.cancellation import OperationCancelled, is_cancelled

        text_parts: list[str] = []
        finish_reason: str | None = None
        usage_data: dict[str, Any] = {}
        saw_reasoning_content = False

        with httpx.stream(
            "POST",
            f"{self._base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout,
        ) as response:
            if response.status_code in (401, 403):
                response.read()
                raise httpx.HTTPStatusError(
                    f"NVIDIA auth/entitlement error (HTTP {response.status_code}) — "
                    "verify NVIDIA_API_KEY and model access in your NVIDIA account.",
                    request=response.request,
                    response=response,
                )
            if response.status_code == 429:
                response.read()
                body = response.text[:500] if response.text else ""
                raise _RateLimited(
                    request=response.request,
                    response=response,
                    classification=_classify_429_body(body),
                    body_excerpt=body[:300],
                    retry_after_seconds=_parse_retry_after_seconds(response),
                )
            if response.status_code >= 400:
                response.read()
                body = response.text[:500] if response.text else ""
                raise httpx.HTTPStatusError(
                    f"NVIDIA API HTTP {response.status_code} for model {effective_model}: {body}",
                    request=response.request,
                    response=response,
                )

            for line in response.iter_lines():
                if is_cancelled():
                    raise OperationCancelled("Extraction cancelled by operator (mid-stream).")
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = _json.loads(data_str)
                except _json.JSONDecodeError:
                    continue  # ignore malformed keep-alive/comment lines

                # The final chunk when stream_options.include_usage is honoured
                # has empty/absent choices and a top-level usage object.
                if chunk.get("usage"):
                    usage_data = chunk["usage"]

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    text_parts.append(delta["content"])
                if delta.get(self._REASONING_CONTENT_KEY):
                    saw_reasoning_content = True
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

        text = "".join(text_parts).strip()

        if saw_reasoning_content:
            logger.warning(
                "nvidia_unexpected_reasoning_content",
                model=effective_model,
            )

        usage = LLMUsage(
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
        )
        if not usage_data:
            # stream_options.include_usage wasn't honoured — token accounting
            # for this call will under-report rather than silently look
            # normal. Visible in run_summary token totals if this recurs.
            logger.warning("nvidia_stream_usage_missing", model=effective_model)

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
