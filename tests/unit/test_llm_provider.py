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
    get_provider,
    get_extraction_provider,
    _provider_cache,
)


class TestLLMUsage:
    def test_creation(self):
        usage = LLMUsage(input_tokens=100, output_tokens=50)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50


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

        response = self._FakeResponse(429, [], body_text="quota exceeded")

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)), \
             patch("time.sleep"):  # skip real backoff delays in this test
            with pytest.raises(httpx.HTTPStatusError, match="rate/quota limit"):
                provider.call("sys", "usr")

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
