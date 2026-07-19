"""Tests for LLM provider abstraction layer."""

import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.core.llm_provider import (
    LLMResponse,
    LLMUsage,
    LocalLLMProvider,
    NvidiaLLMProvider,
    _classify_429_body,
    _compute_backoff_seconds,
    _parse_retry_after_seconds,
    _RateLimited,
    get_provider,
    get_extraction_provider,
    _provider_cache,
)


class TestLLMUsage:
    def test_creation(self):
        usage = LLMUsage(input_tokens=100, output_tokens=50)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50


# ---------------------------------------------------------------------------
# NIM-0b — 429 body classification
# ---------------------------------------------------------------------------


class TestClassify429Body:
    def test_empty_body_is_unclassified(self):
        assert _classify_429_body("") == "429_unclassified"

    def test_rate_limit_phrase_classified_transient(self):
        assert _classify_429_body("You exceeded 40 requests per minute") == "rate_limited_transient"

    def test_too_many_requests_classified_transient(self):
        assert _classify_429_body("429 Too Many Requests") == "rate_limited_transient"

    def test_credit_balance_classified_allowance_exhausted(self):
        assert _classify_429_body("Insufficient credit balance") == "allowance_exhausted"

    def test_trial_ended_classified_allowance_exhausted(self):
        assert _classify_429_body("Your trial has ended") == "allowance_exhausted"

    def test_bare_quota_mention_is_unclassified_not_guessed(self):
        # A bare "quota" mention is deliberately ambiguous — RPM throttling
        # is often phrased as "queries per minute quota" too, so this must
        # NOT be silently treated as allowance exhaustion (the exact
        # over-claim the NIM review flagged in the old single-label logging).
        assert _classify_429_body("quota exceeded") == "429_unclassified"

    def test_quota_with_per_minute_phrase_classified_transient(self):
        # "per minute" is the decisive signal even though "quota" also appears.
        assert _classify_429_body("queries per minute quota exceeded") == "rate_limited_transient"

    def test_explicit_quota_exhausted_phrase_classified_allowance(self):
        # A specific, unambiguous phrase (not bare "quota") can still land
        # as allowance-exhausted.
        assert _classify_429_body("Monthly quota exhausted for this API key") == "allowance_exhausted"

    def test_case_insensitive(self):
        assert _classify_429_body("TOO MANY REQUESTS PER MINUTE") == "rate_limited_transient"

    def test_unrelated_body_is_unclassified(self):
        assert _classify_429_body("Something went wrong") == "429_unclassified"


# ---------------------------------------------------------------------------
# NIM-0c — Retry-After parsing
# ---------------------------------------------------------------------------


class TestParseRetryAfterSeconds:
    class _FakeResponseWithHeaders:
        def __init__(self, headers: dict):
            self.headers = headers

    def test_numeric_header_parsed(self):
        response = self._FakeResponseWithHeaders({"retry-after": "12"})
        assert _parse_retry_after_seconds(response) == 12.0

    def test_missing_header_returns_none(self):
        response = self._FakeResponseWithHeaders({})
        assert _parse_retry_after_seconds(response) is None

    def test_non_numeric_header_returns_none(self):
        # HTTP-date form isn't handled — falls back to jittered backoff.
        response = self._FakeResponseWithHeaders({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
        assert _parse_retry_after_seconds(response) is None

    def test_zero_or_negative_header_returns_none(self):
        response = self._FakeResponseWithHeaders({"retry-after": "0"})
        assert _parse_retry_after_seconds(response) is None

    def test_response_without_headers_attribute_returns_none(self):
        class _NoHeaders:
            pass
        assert _parse_retry_after_seconds(_NoHeaders()) is None


# ---------------------------------------------------------------------------
# NIM-0c — jittered, capped, Retry-After-aware backoff
# ---------------------------------------------------------------------------


class TestComputeBackoffSeconds:
    @patch("src.core.llm_provider.settings")
    def test_exponential_growth_without_retry_after(self, mock_settings):
        mock_settings.nvidia_retry_backoff_cap_seconds = 100.0
        mock_settings.nvidia_retry_jitter_fraction = 0.0  # isolate the exponential curve
        assert _compute_backoff_seconds(0) == 1.0
        assert _compute_backoff_seconds(1) == 2.0
        assert _compute_backoff_seconds(2) == 4.0
        assert _compute_backoff_seconds(3) == 8.0

    @patch("src.core.llm_provider.settings")
    def test_capped_at_configured_ceiling(self, mock_settings):
        mock_settings.nvidia_retry_backoff_cap_seconds = 10.0
        mock_settings.nvidia_retry_jitter_fraction = 0.0
        # 2**10 = 1024s, far above the 10s cap
        assert _compute_backoff_seconds(10) == 10.0

    @patch("src.core.llm_provider.settings")
    def test_retry_after_takes_precedence_over_exponential(self, mock_settings):
        mock_settings.nvidia_retry_backoff_cap_seconds = 100.0
        mock_settings.nvidia_retry_jitter_fraction = 0.0
        # attempt=5 would be 32s exponential, but the server said 3s.
        assert _compute_backoff_seconds(5, retry_after=3.0) == 3.0

    @patch("src.core.llm_provider.settings")
    def test_retry_after_also_respects_cap(self, mock_settings):
        mock_settings.nvidia_retry_backoff_cap_seconds = 10.0
        mock_settings.nvidia_retry_jitter_fraction = 0.0
        assert _compute_backoff_seconds(0, retry_after=999.0) == 10.0

    @patch("src.core.llm_provider.settings")
    def test_jitter_stays_within_configured_fraction(self, mock_settings):
        mock_settings.nvidia_retry_backoff_cap_seconds = 100.0
        mock_settings.nvidia_retry_jitter_fraction = 0.25
        # attempt=2 -> base 4.0s; jitter should keep the result in [3.0, 5.0]
        for _ in range(50):
            wait = _compute_backoff_seconds(2)
            assert 3.0 <= wait <= 5.0

    @patch("src.core.llm_provider.settings")
    def test_never_returns_negative(self, mock_settings):
        mock_settings.nvidia_retry_backoff_cap_seconds = 1.0
        mock_settings.nvidia_retry_jitter_fraction = 1.0  # max jitter, could go negative pre-clamp
        for _ in range(50):
            assert _compute_backoff_seconds(0) >= 0.0


# ---------------------------------------------------------------------------
# NIM-0b — _RateLimited carries classification + retry-after
# ---------------------------------------------------------------------------


class TestRateLimitedException:
    def test_defaults(self):
        exc = _RateLimited(request=MagicMock(), response=MagicMock())
        assert exc.classification == "429_unclassified"
        assert exc.body_excerpt == ""
        assert exc.retry_after_seconds is None

    def test_carries_explicit_values(self):
        exc = _RateLimited(
            request=MagicMock(),
            response=MagicMock(),
            classification="rate_limited_transient",
            body_excerpt="too many requests per minute",
            retry_after_seconds=5.0,
        )
        assert exc.classification == "rate_limited_transient"
        assert exc.body_excerpt == "too many requests per minute"
        assert exc.retry_after_seconds == 5.0


class TestLLMResponse:
    def test_creation(self):
        resp = LLMResponse(
            text="hello",
            usage=LLMUsage(input_tokens=10, output_tokens=5),
            model_id="test-model",
            stop_reason="end_turn",
        )
        assert resp.text == "hello"
        assert resp.model_id == "test-model"
        assert resp.stop_reason == "end_turn"

    def test_stop_reason_default_none(self):
        resp = LLMResponse(
            text="hi",
            usage=LLMUsage(input_tokens=1, output_tokens=1),
            model_id="m",
        )
        assert resp.stop_reason is None


class TestLocalLLMProvider:
    @patch("src.core.llm_provider.settings")
    def test_model_id_prefix(self, mock_settings):
        mock_settings.local_llm_url = "http://localhost:8080"
        mock_settings.local_llm_model = "llama-3.1-8b"
        provider = LocalLLMProvider()
        assert provider.model_id == "llama-3.1-8b-local"

    @patch("src.core.llm_provider.settings")
    def test_raises_without_url(self, mock_settings):
        mock_settings.local_llm_url = ""
        mock_settings.local_llm_model = "llama-3.1-8b"
        with pytest.raises(ValueError, match="Local LLM URL not configured"):
            LocalLLMProvider()

    @patch("src.core.llm_provider.settings")
    def test_call_returns_llm_response(self, mock_settings):
        mock_settings.local_llm_url = "http://localhost:8080"
        mock_settings.local_llm_model = "llama-3.1-8b"
        mock_settings.local_context_length = 131072

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {"content": '{"classified": true}'},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        }

        mock_httpx = MagicMock()
        mock_httpx.post.return_value = mock_resp

        provider = LocalLLMProvider()
        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            result = provider.call("system", "user")

        assert isinstance(result, LLMResponse)
        assert result.text == '{"classified": true}'
        assert result.usage.input_tokens == 50
        assert result.usage.output_tokens == 20
        assert result.model_id == "llama-3.1-8b-local"

    @patch("src.core.llm_provider.settings")
    def test_call_raises_on_empty_response(self, mock_settings):
        mock_settings.local_llm_url = "http://localhost:8080"
        mock_settings.local_llm_model = "llama-3.1-8b"
        mock_settings.local_context_length = 131072

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
            "usage": {},
        }

        mock_httpx = MagicMock()
        mock_httpx.post.return_value = mock_resp

        provider = LocalLLMProvider()
        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            with pytest.raises(ValueError, match="Empty response from local LLM"):
                provider.call("system", "user")


class TestGetProvider:
    def setup_method(self):
        _provider_cache.clear()

    def teardown_method(self):
        _provider_cache.clear()

    @patch("src.core.llm_provider.LocalLLMProvider")
    @patch("src.core.llm_provider.settings")
    def test_get_local_provider(self, mock_settings, mock_cls):
        mock_settings.llm_provider = "local"
        mock_instance = MagicMock()
        mock_instance.model_id = "local:llama-3.1-8b"
        mock_cls.return_value = mock_instance

        provider = get_provider("local")
        assert provider is mock_instance

    @patch("src.core.llm_provider.settings")
    def test_unknown_provider_raises(self, mock_settings):
        mock_settings.llm_provider = "unknown"
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_provider("unknown")

    @patch("src.core.llm_provider.LocalLLMProvider")
    @patch("src.core.llm_provider.settings")
    def test_caches_provider(self, mock_settings, mock_cls):
        mock_settings.llm_provider = "local"
        mock_instance = MagicMock()
        mock_instance.model_id = "local:llama-3.1-8b"
        mock_cls.return_value = mock_instance

        p1 = get_provider("local")
        p2 = get_provider("local")
        assert p1 is p2
        mock_cls.assert_called_once()

    @patch("src.core.llm_provider.NvidiaLLMProvider")
    @patch("src.core.llm_provider.settings")
    def test_get_nvidia_provider(self, mock_settings, mock_cls):
        mock_settings.llm_provider = "nvidia"
        mock_instance = MagicMock()
        mock_instance.model_id = "openai-gpt-oss-120b-nvidia"
        mock_cls.return_value = mock_instance

        provider = get_provider("nvidia")
        assert provider is mock_instance

    @patch("src.core.model_config.get_config")
    @patch("src.core.llm_provider.NvidiaLLMProvider")
    @patch("src.core.llm_provider.settings")
    def test_get_extraction_provider_nvidia(self, mock_settings, mock_cls, mock_get_config):
        # Config store provider is the runtime source of truth.
        mock_get_config.return_value.provider = "nvidia"
        mock_settings.extraction_provider = "local"
        mock_settings.llm_provider = "local"
        mock_instance = MagicMock()
        mock_instance.model_id = "openai-gpt-oss-120b-nvidia"
        mock_cls.return_value = mock_instance

        provider = get_extraction_provider()
        assert provider is mock_instance

    @patch("src.core.model_config.get_config")
    @patch("src.core.llm_provider.LocalLLMProvider")
    @patch("src.core.llm_provider.settings")
    def test_get_extraction_provider_local_default(self, mock_settings, mock_cls, mock_get_config):
        mock_get_config.return_value.provider = "local"
        mock_settings.extraction_provider = "local"
        mock_settings.llm_provider = "local"
        mock_settings.local_extraction_model = "google/gemma-4-26b-a4b"
        mock_instance = MagicMock()
        mock_instance.model_id = "google-gemma-4-26b-a4b-local"
        mock_cls.return_value = mock_instance

        provider = get_extraction_provider()
        assert provider is mock_instance

    @patch("src.core.model_config.get_config")
    @patch("src.core.llm_provider.NvidiaLLMProvider")
    @patch("src.core.llm_provider.settings")
    def test_config_store_provider_overrides_settings(self, mock_settings, mock_cls, mock_get_config):
        # Even when settings say "local", the config-store toggle wins.
        mock_get_config.return_value.provider = "nvidia"
        mock_settings.extraction_provider = "local"
        mock_settings.llm_provider = "local"
        mock_instance = MagicMock()
        mock_instance.model_id = "openai-gpt-oss-120b-nvidia"
        mock_cls.return_value = mock_instance

        provider = get_extraction_provider()
        assert provider is mock_instance
        mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# NvidiaLLMProvider
# ---------------------------------------------------------------------------


class TestNvidiaLLMProvider:
    @patch("src.core.llm_provider.settings")
    def test_raises_without_api_key(self, mock_settings):
        mock_settings.nvidia_api_key = ""
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        with pytest.raises(ValueError, match="NVIDIA_API_KEY is not set"):
            NvidiaLLMProvider()

    @patch("src.core.llm_provider.settings")
    def test_raises_without_base_url(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test-key"
        mock_settings.nvidia_base_url = ""
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        with pytest.raises(ValueError, match="REGS_NVIDIA_BASE_URL"):
            NvidiaLLMProvider()

    @patch("src.core.llm_provider.settings")
    def test_model_id_has_nvidia_suffix(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        provider = NvidiaLLMProvider()
        assert provider.model_id == "openai-gpt-oss-120b-nvidia"
        assert "local" not in provider.model_id

    # NvidiaLLMProvider.call() streams (stream:true) rather than making one
    # blind blocking request, so it can tell "still generating" from "truly
    # stuck" and interrupt cleanly on cancellation (TA-11). These helpers
    # build fake httpx.stream()-shaped SSE responses for that mechanism.

    def _sse_lines(self, *chunks: dict) -> list:
        import json as _json
        lines = [f"data: {_json.dumps(c)}" for c in chunks]
        lines.append("data: [DONE]")
        return lines

    class _FakeResponse:
        def __init__(self, status_code, lines, body_text=""):
            self.status_code = status_code
            self._lines = lines
            self.text = body_text
            self.request = MagicMock(name="request")

        def read(self):
            pass

        def iter_lines(self):
            yield from self._lines

    class _FakeStreamCM:
        def __init__(self, response):
            self._response = response

        def __enter__(self):
            return self._response

        def __exit__(self, *exc_info):
            return False

    @patch("src.core.llm_provider.settings")
    def test_call_posts_to_chat_completions_not_double_v1(self, mock_settings):
        """Regression guard: NVIDIA base URL already has /v1; must not double it."""
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"

        chunks = self._sse_lines(
            {"choices": [{"delta": {"content": '{"a": 1}'}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        )
        response = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)) as mock_stream:
            provider.call("sys", "usr")

        called_url = mock_stream.call_args[0][1]
        assert called_url == "https://integrate.api.nvidia.com/v1/chat/completions"
        assert "/v1/v1/" not in called_url

    @patch("src.core.llm_provider.settings")
    def test_call_includes_bearer_header(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-secret-key"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"

        chunks = self._sse_lines(
            {"choices": [{"delta": {"content": "hello"}, "finish_reason": "stop"}]},
        )
        response = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)) as mock_stream:
            provider.call("sys", "usr")

        headers = mock_stream.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer nvapi-secret-key"

    @patch("src.core.llm_provider.settings")
    def test_call_returns_llm_response(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"

        chunks = self._sse_lines(
            {"choices": [{"delta": {"content": '{"extracted": true}'}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 80, "completion_tokens": 30}},
        )
        response = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)):
            result = provider.call("system", "user")

        assert isinstance(result, LLMResponse)
        assert result.text == '{"extracted": true}'
        assert result.usage.input_tokens == 80
        assert result.usage.output_tokens == 30
        assert result.model_id == "openai-gpt-oss-120b-nvidia"
        assert result.stop_reason == "stop"

    @patch("src.core.llm_provider.settings")
    def test_call_uses_temperature_zero_by_default(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"

        chunks = self._sse_lines(
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        )
        response = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)) as mock_stream:
            provider.call("sys", "usr")

        payload = mock_stream.call_args[1]["json"]
        assert payload["temperature"] == 0.0

    @patch("src.core.llm_provider.settings")
    def test_empty_content_raises(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"

        chunks = self._sse_lines(
            {"choices": [{"delta": {}, "finish_reason": "length"}]},
        )
        response = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)):
            with pytest.raises(ValueError, match="Empty response from NVIDIA LLM"):
                provider.call("sys", "usr")

    @patch("src.core.llm_provider.settings")
    def test_401_raises_auth_error(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-bad-key"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"

        response = self._FakeResponse(401, [], body_text="Unauthorized")

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)):
            with pytest.raises(httpx.HTTPStatusError, match="auth/entitlement"):
                provider.call("sys", "usr")

    @patch("src.core.llm_provider.settings")
    def test_429_logs_quota_warning_and_raises(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        mock_settings.nvidia_max_retries = 5
        mock_settings.nvidia_retry_backoff_cap_seconds = 30.0
        mock_settings.nvidia_retry_jitter_fraction = 0.25

        response = self._FakeResponse(429, [], body_text="quota exceeded")

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)), \
             patch("time.sleep"):  # skip real backoff delays in this test
            with pytest.raises(httpx.HTTPStatusError, match="rate/quota limit") as exc_info:
                provider.call("sys", "usr")

        # NIM-0b: a bare "quota" mention alone is ambiguous (RPM throttling
        # is often phrased as "queries per minute quota" too), so this body
        # correctly lands as unclassified rather than a guessed direction.
        assert exc_info.value.nvidia_429_classification == "429_unclassified"

    @patch("src.core.llm_provider.settings")
    def test_429_retries_before_exhausting(self, mock_settings):
        """A 429 that clears within the retry budget should succeed, not
        raise — proves the retry loop (not just the terminal-failure path)
        still works with settings-driven retry count + jittered backoff."""
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        mock_settings.nvidia_max_retries = 5
        mock_settings.nvidia_retry_backoff_cap_seconds = 30.0
        mock_settings.nvidia_retry_jitter_fraction = 0.25

        rate_limited_response = self._FakeResponse(429, [], body_text="too many requests per minute")
        chunks = self._sse_lines(
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        )
        success_response = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        responses = [
            self._FakeStreamCM(rate_limited_response),
            self._FakeStreamCM(success_response),
        ]
        with patch("httpx.stream", side_effect=responses), patch("time.sleep"):
            result = provider.call("sys", "usr")

        assert result.text == "ok"

    @patch("src.core.llm_provider.settings")
    def test_429_classifies_rate_limited_body(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        mock_settings.nvidia_max_retries = 0  # exhaust immediately
        mock_settings.nvidia_retry_backoff_cap_seconds = 30.0
        mock_settings.nvidia_retry_jitter_fraction = 0.25

        response = self._FakeResponse(429, [], body_text="Too many requests per minute, slow down")

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)), patch("time.sleep"):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                provider.call("sys", "usr")

        assert exc_info.value.nvidia_429_classification == "rate_limited_transient"

    @patch("src.core.llm_provider.settings")
    def test_429_classifies_allowance_exhausted_body(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        mock_settings.nvidia_max_retries = 0
        mock_settings.nvidia_retry_backoff_cap_seconds = 30.0
        mock_settings.nvidia_retry_jitter_fraction = 0.25

        response = self._FakeResponse(429, [], body_text="Your trial has ended; insufficient credit balance")

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)), patch("time.sleep"):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                provider.call("sys", "usr")

        assert exc_info.value.nvidia_429_classification == "allowance_exhausted"

    @patch("src.core.llm_provider.settings")
    def test_reasoning_content_in_response_does_not_crash(self, mock_settings):
        """gpt-oss-120b may return reasoning_content; provider should use content only."""
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"

        chunks = self._sse_lines(
            {"choices": [{"delta": {"reasoning_content": "Let me think through this..."}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": '{"answer": 42}'}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 100}},
        )
        response = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)):
            result = provider.call("sys", "usr")

        assert result.text == '{"answer": 42}'  # content used, not reasoning_content

    @patch("src.core.llm_provider.settings")
    def test_model_override_applies(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"

        chunks = self._sse_lines(
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        )
        response = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)) as mock_stream:
            result = provider.call("sys", "usr", model_override="meta/llama-3.1-70b-instruct")

        payload = mock_stream.call_args[1]["json"]
        assert payload["model"] == "meta/llama-3.1-70b-instruct"
        assert result.model_id == "meta-llama-3.1-70b-instruct-nvidia"
