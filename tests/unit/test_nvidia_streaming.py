"""Unit tests for NvidiaLLMProvider's streaming call path.

Covers the switch from stream:false (one blind blocking response) to
stream:true with per-chunk idle-timeout detection and cancellation checks,
plus the retry loop's handling of transport errors, 429s, and cancellation.
Nothing here talks to a real NVIDIA endpoint — httpx.stream is mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.core.cancellation import OperationCancelled, clear_cancel, is_cancelled


@pytest.fixture(autouse=True)
def _reset_cancel_flag():
    clear_cancel()
    yield
    clear_cancel()


def _make_provider():
    from src.core.config import settings
    from src.core.llm_provider import NvidiaLLMProvider

    with patch.object(settings, "nvidia_api_key", "fake-key"):
        return NvidiaLLMProvider(base_url="https://fake.nvidia.test/v1", model="fake/model")


def _sse_lines(*chunks: dict) -> list[str]:
    lines = [f"data: {json.dumps(c)}" for c in chunks]
    lines.append("data: [DONE]")
    return lines


class _FakeResponse:
    """Mimics the subset of httpx.Response used by _stream_chat_completion."""

    def __init__(self, status_code: int, lines: list[str], body_text: str = ""):
        self.status_code = status_code
        self._lines = lines
        self.text = body_text
        self.request = MagicMock(name="request")

    def read(self) -> None:
        pass

    def iter_lines(self):
        yield from self._lines


class _FakeStreamCM:
    """Mimics the context manager returned by httpx.stream()."""

    def __init__(self, response: _FakeResponse | None = None, raise_on_enter: Exception | None = None):
        self._response = response
        self._raise_on_enter = raise_on_enter

    def __enter__(self):
        if self._raise_on_enter:
            raise self._raise_on_enter
        return self._response

    def __exit__(self, *exc_info):
        return False


class TestStreamingHappyPath:
    def test_accumulates_content_across_chunks(self):
        provider = _make_provider()
        chunks = [
            {"choices": [{"delta": {"content": "Hello "}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        ]
        response = _FakeResponse(200, _sse_lines(*chunks))
        with patch("httpx.stream", return_value=_FakeStreamCM(response)):
            result = provider.call("sys", "user")

        assert result.text == "Hello world"
        assert result.stop_reason == "stop"
        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 5
        assert result.model_id == "fake-model-nvidia"

    def test_missing_usage_data_does_not_crash(self):
        provider = _make_provider()
        chunks = [{"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}]
        response = _FakeResponse(200, _sse_lines(*chunks))
        with patch("httpx.stream", return_value=_FakeStreamCM(response)):
            result = provider.call("sys", "user")

        assert result.text == "hi"
        assert result.usage.input_tokens == 0
        assert result.usage.output_tokens == 0

    def test_reasoning_content_excluded_from_text(self):
        provider = _make_provider()
        chunks = [
            {"choices": [{"delta": {"reasoning_content": "thinking..."}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "answer"}, "finish_reason": "stop"}]},
        ]
        response = _FakeResponse(200, _sse_lines(*chunks))
        with patch("httpx.stream", return_value=_FakeStreamCM(response)):
            result = provider.call("sys", "user")

        assert result.text == "answer"
        assert "thinking" not in result.text

    def test_empty_response_raises_value_error(self):
        provider = _make_provider()
        chunks = [{"choices": [{"delta": {}, "finish_reason": "stop"}]}]
        response = _FakeResponse(200, _sse_lines(*chunks))
        with patch("httpx.stream", return_value=_FakeStreamCM(response)):
            with pytest.raises(ValueError, match="Empty response"):
                provider.call("sys", "user")

    def test_malformed_chunk_line_is_skipped_not_fatal(self):
        provider = _make_provider()
        lines = ["data: {not valid json", "data: " + json.dumps(
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}
        ), "data: [DONE]"]
        response = _FakeResponse(200, lines)
        with patch("httpx.stream", return_value=_FakeStreamCM(response)):
            result = provider.call("sys", "user")
        assert result.text == "ok"


class TestReasoningModelIdleTimeout:
    """Reasoning models (gpt-oss, deepseek-r1, qwen3) can go quiet for well
    over a minute before their first streamed byte — they need a longer
    idle-timeout allowance than instruct models, which stream almost
    immediately."""

    def _call_and_capture_timeout(self, model: str) -> float:
        provider = _make_provider()
        chunks = [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]
        response = _FakeResponse(200, _sse_lines(*chunks))
        captured = {}

        def _capture(*args, **kwargs):
            captured["timeout"] = kwargs["timeout"]
            return _FakeStreamCM(response)

        with patch("httpx.stream", side_effect=_capture):
            provider.call("sys", "user", model_override=model)
        return captured["timeout"].read

    def test_reasoning_model_gets_longer_idle_timeout(self):
        from src.core.llm_provider import NvidiaLLMProvider

        read_timeout = self._call_and_capture_timeout("openai/gpt-oss-120b")
        assert read_timeout == NvidiaLLMProvider._IDLE_TIMEOUT_REASONING_SECONDS
        assert read_timeout > NvidiaLLMProvider._IDLE_TIMEOUT_SECONDS

    def test_instruct_model_keeps_default_idle_timeout(self):
        from src.core.llm_provider import NvidiaLLMProvider

        read_timeout = self._call_and_capture_timeout("meta/llama-3.1-8b-instruct")
        assert read_timeout == NvidiaLLMProvider._IDLE_TIMEOUT_SECONDS


class TestCancellation:
    def test_cancelled_before_first_attempt_raises_immediately_no_http_call(self):
        provider = _make_provider()
        from src.core.cancellation import _cancel_event
        _cancel_event.set()

        with patch("httpx.stream") as mock_stream:
            with pytest.raises(OperationCancelled):
                provider.call("sys", "user")
            mock_stream.assert_not_called()

    def test_cancelled_mid_stream_raises_operation_cancelled(self):
        provider = _make_provider()
        from src.core.cancellation import _cancel_event

        chunks = [
            {"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "more"}, "finish_reason": "stop"}]},
        ]

        class _CancellingLines:
            def __iter__(self):
                yield f"data: {json.dumps(chunks[0])}"
                _cancel_event.set()  # cancellation arrives mid-stream
                yield f"data: {json.dumps(chunks[1])}"
                yield "data: [DONE]"

        response = _FakeResponse(200, [])
        response.iter_lines = lambda: iter(_CancellingLines())

        with patch("httpx.stream", return_value=_FakeStreamCM(response)):
            with pytest.raises(OperationCancelled):
                provider.call("sys", "user")

    def test_cancel_during_backoff_sleep_is_fast(self):
        """Cancellation during the retry backoff sleep should not block for
        the full backoff duration."""
        import time

        provider = _make_provider()
        from src.core.cancellation import _cancel_event

        call_count = {"n": 0}

        def _side_effect(*args, **kwargs):
            call_count["n"] += 1
            raise httpx.ConnectError("boom")

        with patch("httpx.stream", side_effect=_side_effect):
            # Cancel from a background thread shortly after the first
            # attempt fails, while the 1s backoff sleep is in progress.
            import threading

            def _cancel_soon():
                time.sleep(0.1)
                _cancel_event.set()

            threading.Thread(target=_cancel_soon, daemon=True).start()

            t0 = time.monotonic()
            with pytest.raises(OperationCancelled):
                provider.call("sys", "user")
            elapsed = time.monotonic() - t0

        # Should bail well within the first 1s backoff window, not run all
        # 5 retries (which would take >30s of backoff alone).
        assert elapsed < 2.0
        assert call_count["n"] == 1


class TestRetryBehavior:
    def test_rate_limit_retries_then_raises_with_guidance_message(self):
        provider = _make_provider()
        response = _FakeResponse(429, [], body_text="quota exceeded")

        with patch("httpx.stream", return_value=_FakeStreamCM(response)), \
             patch("time.sleep"):  # skip real backoff delays in this test
            with pytest.raises(httpx.HTTPStatusError, match="rate/quota limit"):
                provider.call("sys", "user")

    def test_transport_error_retries_then_raises(self):
        provider = _make_provider()

        with patch("httpx.stream", side_effect=httpx.ReadTimeout("stalled")), \
             patch("time.sleep"):
            with pytest.raises(httpx.ReadTimeout):
                provider.call("sys", "user")

    def test_transport_error_recovers_on_retry(self):
        provider = _make_provider()
        chunks = [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]
        good_response = _FakeResponse(200, _sse_lines(*chunks))

        attempts = {"n": 0}

        def _side_effect(*args, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx.ReadTimeout("stalled once")
            return _FakeStreamCM(good_response)

        with patch("httpx.stream", side_effect=_side_effect), patch("time.sleep"):
            result = provider.call("sys", "user")

        assert result.text == "ok"
        assert attempts["n"] == 2
