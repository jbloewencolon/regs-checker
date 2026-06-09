"""Tests for LLM provider abstraction layer."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from src.core.llm_provider import (
    BaseLLMProvider,
    LLMResponse,
    LLMUsage,
    LocalLLMProvider,
    get_provider,
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
