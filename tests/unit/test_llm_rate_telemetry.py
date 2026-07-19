"""Unit tests for LLMRateTelemetry — NIM-0a per-model request/429/token
telemetry.

NVIDIA exposes no balance or usage API, so this is the only reliable way to
know how close a run is to its rate-limit ceiling. Tests exercise the
telemetry object directly (rolling-window RPM, peak tracking, per-outcome
429 counters, reset-per-run) and separately confirm the real integration
point (NvidiaLLMProvider.call()) actually writes into it.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.core.llm_rate_telemetry import (
    LLMRateTelemetry,
    get_llm_rate_telemetry,
)
from src.core.llm_provider import NvidiaLLMProvider


class TestLLMRateTelemetryBasics:
    def _make(self) -> LLMRateTelemetry:
        return LLMRateTelemetry()

    def test_empty_snapshot_has_no_models(self):
        t = self._make()
        assert t.snapshot() == {}

    def test_record_request_creates_model_entry(self):
        t = self._make()
        t.record_request("openai/gpt-oss-120b")
        snap = t.snapshot()
        assert "openai/gpt-oss-120b" in snap
        assert snap["openai/gpt-oss-120b"]["requests_total"] == 1

    def test_multiple_requests_accumulate(self):
        t = self._make()
        for _ in range(5):
            t.record_request("openai/gpt-oss-120b")
        assert t.snapshot()["openai/gpt-oss-120b"]["requests_total"] == 5

    def test_models_tracked_independently(self):
        t = self._make()
        t.record_request("openai/gpt-oss-120b")
        t.record_request("meta/llama-3.1-8b-instruct")
        t.record_request("meta/llama-3.1-8b-instruct")
        snap = t.snapshot()
        assert snap["openai/gpt-oss-120b"]["requests_total"] == 1
        assert snap["meta/llama-3.1-8b-instruct"]["requests_total"] == 2

    def test_record_tokens_accumulates(self):
        t = self._make()
        t.record_tokens("openai/gpt-oss-120b", input_tokens=100, output_tokens=50)
        t.record_tokens("openai/gpt-oss-120b", input_tokens=200, output_tokens=25)
        assert t.snapshot()["openai/gpt-oss-120b"]["tokens_total"] == 375

    def test_rate_limited_seen_recovered_exhausted_are_independent_counters(self):
        t = self._make()
        model = "openai/gpt-oss-120b"
        t.record_rate_limited(model)
        t.record_rate_limited(model)
        t.record_rate_limited_recovered(model)
        t.record_rate_limited_exhausted(model)
        snap = t.snapshot()[model]
        assert snap["rate_limited_seen"] == 2
        assert snap["rate_limited_recovered"] == 1
        assert snap["rate_limited_exhausted"] == 1

    def test_reset_all_clears_every_model(self):
        t = self._make()
        t.record_request("openai/gpt-oss-120b")
        t.record_request("meta/llama-3.1-8b-instruct")
        t.reset_all()
        assert t.snapshot() == {}

    def test_pacing_wait_accumulates(self):
        """NIM-1b: cumulative time spent blocked on the NIM-1a rate limiter,
        so pacing's throughput cost is a measured number."""
        t = self._make()
        model = "openai/gpt-oss-120b"
        t.record_pacing_wait(model, 2.5)
        t.record_pacing_wait(model, 1.25)
        assert t.snapshot()[model]["pacing_wait_seconds_total"] == 3.75

    def test_zero_or_negative_pacing_wait_not_recorded(self):
        t = self._make()
        model = "openai/gpt-oss-120b"
        t.record_pacing_wait(model, 0.0)
        t.record_pacing_wait(model, -1.0)
        # No model entry should even be created for a no-op wait.
        assert t.snapshot() == {}

    def test_pacing_wait_independent_per_model(self):
        t = self._make()
        t.record_pacing_wait("openai/gpt-oss-120b", 5.0)
        t.record_pacing_wait("meta/llama-3.1-8b-instruct", 1.0)
        snap = t.snapshot()
        assert snap["openai/gpt-oss-120b"]["pacing_wait_seconds_total"] == 5.0
        assert snap["meta/llama-3.1-8b-instruct"]["pacing_wait_seconds_total"] == 1.0

    def test_rpm_current_reflects_rolling_window(self):
        t = self._make()
        model = "openai/gpt-oss-120b"
        for _ in range(10):
            t.record_request(model)
        snap = t.snapshot()[model]
        # 10 requests within the last 60s -> 10 requests/min in the window.
        assert snap["rpm_current"] == 10.0

    def test_rpm_peak_tracks_highest_observed(self):
        t = self._make()
        model = "openai/gpt-oss-120b"
        for _ in range(3):
            t.record_request(model)
        peak_after_3 = t.snapshot()[model]["rpm_peak"]
        assert peak_after_3 == 3.0

        for _ in range(7):
            t.record_request(model)
        snap = t.snapshot()[model]
        assert snap["rpm_peak"] == 10.0
        # Peak never decreases even if current activity later drops.
        assert snap["rpm_peak"] >= snap["rpm_current"]

    def test_old_requests_age_out_of_the_rolling_window(self):
        t = self._make()
        model = "openai/gpt-oss-120b"
        now = time.time()
        with patch("time.time", return_value=now - 120):
            t.record_request(model)
        # 120s ago is outside the 60s window; current RPM should not count it.
        snap = t.snapshot()[model]
        assert snap["rpm_current"] == 0.0
        # But the lifetime total still reflects it.
        assert snap["requests_total"] == 1

    def test_global_singleton(self):
        t1 = get_llm_rate_telemetry()
        t2 = get_llm_rate_telemetry()
        assert t1 is t2


class TestNvidiaProviderWritesTelemetry:
    """Confirms the real integration point: NvidiaLLMProvider.call() writes
    into the shared telemetry singleton, not just a private counter."""

    class _FakeResponse:
        def __init__(self, status_code, lines, body_text="", headers=None):
            self.status_code = status_code
            self._lines = lines
            self.text = body_text
            self.request = MagicMock(name="request")
            self.headers = headers or {}

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

    def _sse_lines(self, *chunks: dict) -> list:
        import json as _json
        lines = [f"data: {_json.dumps(c)}" for c in chunks]
        lines.append("data: [DONE]")
        return lines

    @patch("src.core.llm_provider.settings")
    def test_successful_call_records_request_and_tokens(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        mock_settings.nvidia_rpm_limit = 0  # NIM-1a: pacing disabled unless a test opts in
        mock_settings.nvidia_max_retries = 5
        mock_settings.nvidia_retry_backoff_cap_seconds = 30.0
        mock_settings.nvidia_retry_jitter_fraction = 0.25

        get_llm_rate_telemetry().reset_all()

        chunks = self._sse_lines(
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 80, "completion_tokens": 20}},
        )
        response = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(response)):
            provider.call("sys", "usr")

        snap = get_llm_rate_telemetry().snapshot()["openai/gpt-oss-120b"]
        assert snap["requests_total"] == 1
        assert snap["tokens_total"] == 100
        assert snap["rate_limited_seen"] == 0

    @patch("src.core.llm_provider.settings")
    def test_429_then_recovery_records_seen_and_recovered(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        mock_settings.nvidia_rpm_limit = 0  # NIM-1a: pacing disabled unless a test opts in
        mock_settings.nvidia_max_retries = 5
        mock_settings.nvidia_retry_backoff_cap_seconds = 30.0
        mock_settings.nvidia_retry_jitter_fraction = 0.25

        get_llm_rate_telemetry().reset_all()

        rate_limited = self._FakeResponse(429, [], body_text="too many requests per minute")
        chunks = self._sse_lines(
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        )
        success = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", side_effect=[
            self._FakeStreamCM(rate_limited), self._FakeStreamCM(success),
        ]), patch("time.sleep"):
            provider.call("sys", "usr")

        snap = get_llm_rate_telemetry().snapshot()["openai/gpt-oss-120b"]
        assert snap["requests_total"] == 2  # the 429 attempt + the successful retry
        assert snap["rate_limited_seen"] == 1
        assert snap["rate_limited_recovered"] == 1
        assert snap["rate_limited_exhausted"] == 0

    @patch("src.core.llm_provider.settings")
    def test_429_exhaustion_records_exhausted_not_recovered(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        mock_settings.nvidia_rpm_limit = 0  # NIM-1a: pacing disabled unless a test opts in
        mock_settings.nvidia_max_retries = 0  # exhaust on first 429
        mock_settings.nvidia_retry_backoff_cap_seconds = 30.0
        mock_settings.nvidia_retry_jitter_fraction = 0.25

        get_llm_rate_telemetry().reset_all()

        rate_limited = self._FakeResponse(429, [], body_text="too many requests per minute")

        provider = NvidiaLLMProvider()
        with patch("httpx.stream", return_value=self._FakeStreamCM(rate_limited)), \
             patch("time.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                provider.call("sys", "usr")

        snap = get_llm_rate_telemetry().snapshot()["openai/gpt-oss-120b"]
        assert snap["rate_limited_seen"] == 1
        assert snap["rate_limited_exhausted"] == 1
        assert snap["rate_limited_recovered"] == 0
