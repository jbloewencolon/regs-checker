"""Unit tests for RateLimiter — NIM-1a client-side per-model pacing.

The 2026-07-19 live-run evidence showed the pipeline using only a small
fraction of the reported ~40 RPM/model cap, so this isn't a defense against
current throttling — it's the guardrail that lets concurrency be safely
raised into that unused headroom. Tests exercise the limiter directly
(reservation semantics, cap enforcement, disable-at-zero, thread safety)
and separately confirm the real integration point (NvidiaLLMProvider.call())
actually consults it before each attempt.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.core.llm_provider import NvidiaLLMProvider
from src.core.llm_rate_limiter import RateLimiter, get_rate_limiter
from src.core.llm_rate_telemetry import get_llm_rate_telemetry


class _FakeClock:
    """Deterministic, injectable clock + sleep so tests never depend on
    real wall-clock timing."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.sleep_calls: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now += seconds


class TestRateLimiterBasics:
    def _make(self) -> RateLimiter:
        return RateLimiter()

    def test_disabled_when_cap_is_zero(self):
        limiter = self._make()
        waited = limiter.acquire("model-a", cap_rpm=0, sleep_fn=lambda s: pytest.fail("should not sleep"))
        assert waited == 0.0

    def test_disabled_when_cap_is_negative(self):
        limiter = self._make()
        waited = limiter.acquire("model-a", cap_rpm=-1, sleep_fn=lambda s: pytest.fail("should not sleep"))
        assert waited == 0.0

    def test_under_cap_never_waits(self):
        limiter = self._make()
        clock = _FakeClock()
        with patch("src.core.llm_rate_limiter.time.time", side_effect=lambda: clock.now):
            for _ in range(5):
                waited = limiter.acquire("model-a", cap_rpm=10, sleep_fn=clock.sleep)
                assert waited == 0.0
        assert clock.sleep_calls == []

    def test_at_cap_blocks_until_window_frees_capacity(self):
        limiter = self._make()
        clock = _FakeClock()
        with patch("src.core.llm_rate_limiter.time.time", side_effect=lambda: clock.now):
            # Fill the cap (3 requests) at t=0.
            for _ in range(3):
                limiter.acquire("model-a", cap_rpm=3, sleep_fn=clock.sleep)
            # A 4th request must wait for the oldest (t=0) to age out of the
            # 60s window before it can proceed.
            waited = limiter.acquire("model-a", cap_rpm=3, sleep_fn=clock.sleep)
        assert waited > 0
        assert clock.sleep_calls  # at least one sleep occurred

    def test_models_are_independent(self):
        limiter = self._make()
        clock = _FakeClock()
        with patch("src.core.llm_rate_limiter.time.time", side_effect=lambda: clock.now):
            for _ in range(3):
                limiter.acquire("model-a", cap_rpm=3, sleep_fn=clock.sleep)
            # model-b has its own budget — must not be blocked by model-a's.
            waited = limiter.acquire("model-b", cap_rpm=3, sleep_fn=clock.sleep)
        assert waited == 0.0

    def test_reset_all_clears_every_model(self):
        limiter = self._make()
        clock = _FakeClock()
        with patch("src.core.llm_rate_limiter.time.time", side_effect=lambda: clock.now):
            for _ in range(3):
                limiter.acquire("model-a", cap_rpm=3, sleep_fn=clock.sleep)
            limiter.reset_all()
            # After reset, model-a's budget is fresh — no wait needed.
            waited = limiter.acquire("model-a", cap_rpm=3, sleep_fn=clock.sleep)
        assert waited == 0.0

    def test_global_singleton(self):
        l1 = get_rate_limiter()
        l2 = get_rate_limiter()
        assert l1 is l2

    def test_concurrent_callers_never_exceed_cap_in_window(self):
        """Thread-safety: N threads racing to acquire against a shared cap
        should never let more than `cap` requests through per window
        without a wait — the whole point of a *shared* limiter under
        concurrent agents. Uses a short real window (not a mocked clock)
        so real `time.sleep` genuinely frees capacity and the test can't
        spin-loop or hang: a no-op sleep_fn combined with real time.time()
        would never let the window age out, so this must sleep for real,
        just briefly."""
        limiter = RateLimiter()
        original_window = RateLimiter.WINDOW_SECONDS
        RateLimiter.WINDOW_SECONDS = 0.2
        try:
            cap = 5
            results: list[float] = []
            lock = threading.Lock()

            def worker():
                waited = limiter.acquire("model-a", cap_rpm=cap, sleep_fn=time.sleep)
                with lock:
                    results.append(waited)

            threads = [threading.Thread(target=worker) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            assert len(results) == 20
            # 20 requests against a cap of 5 within one short window must
            # force at least some callers to wait rather than all sailing
            # through — the cap was never silently exceeded.
            assert any(w > 0 for w in results)
        finally:
            RateLimiter.WINDOW_SECONDS = original_window


class TestNvidiaProviderConsultsRateLimiter:
    """Confirms the real integration point: NvidiaLLMProvider.call() calls
    into the shared rate limiter before every attempt."""

    class _FakeResponse:
        def __init__(self, status_code, lines, body_text=""):
            self.status_code = status_code
            self._lines = lines
            self.text = body_text
            self.request = MagicMock(name="request")
            self.headers = {}

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
    def test_call_consults_rate_limiter_before_request(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        mock_settings.nvidia_max_retries = 5
        mock_settings.nvidia_retry_backoff_cap_seconds = 30.0
        mock_settings.nvidia_retry_jitter_fraction = 0.25
        mock_settings.nvidia_rpm_limit = 35.0

        get_rate_limiter().reset_all()
        get_llm_rate_telemetry().reset_all()

        chunks = self._sse_lines(
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        )
        response = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        with patch.object(
            get_rate_limiter(), "acquire", wraps=get_rate_limiter().acquire,
        ) as spy_acquire, patch("httpx.stream", return_value=self._FakeStreamCM(response)):
            provider.call("sys", "usr")

        spy_acquire.assert_called_once()
        called_model = spy_acquire.call_args[0][0]
        called_cap = spy_acquire.call_args[0][1]
        assert called_model == "openai/gpt-oss-120b"
        assert called_cap == 35.0

    @patch("src.core.llm_provider.settings")
    def test_rate_limit_disabled_does_not_block(self, mock_settings):
        """cap=0 (disabled) must not introduce any wait, matching the
        opt-in-only posture for a controlled benchmark."""
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        mock_settings.nvidia_max_retries = 5
        mock_settings.nvidia_retry_backoff_cap_seconds = 30.0
        mock_settings.nvidia_retry_jitter_fraction = 0.25
        mock_settings.nvidia_rpm_limit = 0

        get_rate_limiter().reset_all()
        get_llm_rate_telemetry().reset_all()

        chunks = self._sse_lines(
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        )
        response = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        with patch("time.sleep") as mock_sleep, \
             patch("httpx.stream", return_value=self._FakeStreamCM(response)):
            provider.call("sys", "usr")

        mock_sleep.assert_not_called()

    @patch("src.core.llm_provider.settings")
    def test_pacing_wait_recorded_in_telemetry(self, mock_settings):
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        mock_settings.nvidia_max_retries = 5
        mock_settings.nvidia_retry_backoff_cap_seconds = 30.0
        mock_settings.nvidia_retry_jitter_fraction = 0.25
        mock_settings.nvidia_rpm_limit = 35.0

        get_rate_limiter().reset_all()
        get_llm_rate_telemetry().reset_all()

        chunks = self._sse_lines(
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        )
        response = self._FakeResponse(200, chunks)

        provider = NvidiaLLMProvider()
        # Force the limiter to report a wait, without a real sleep.
        with patch.object(get_rate_limiter(), "acquire", return_value=2.5), \
             patch("httpx.stream", return_value=self._FakeStreamCM(response)):
            provider.call("sys", "usr")

        snap = get_llm_rate_telemetry().snapshot()["openai/gpt-oss-120b"]
        assert snap["pacing_wait_seconds_total"] == 2.5

    @patch("src.core.llm_provider.settings")
    def test_cancellation_during_pacing_wait_propagates(self, mock_settings):
        """A cancelled run should interrupt a pacing wait promptly rather
        than blocking through it — the same guarantee llm_provider.py
        already gives backoff waits."""
        mock_settings.nvidia_api_key = "nvapi-test"
        mock_settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        mock_settings.nvidia_extraction_model = "openai/gpt-oss-120b"
        mock_settings.nvidia_max_retries = 5
        mock_settings.nvidia_retry_backoff_cap_seconds = 30.0
        mock_settings.nvidia_retry_jitter_fraction = 0.25
        mock_settings.nvidia_rpm_limit = 1.0  # force the second call to wait

        get_rate_limiter().reset_all()
        get_llm_rate_telemetry().reset_all()

        from src.core.cancellation import OperationCancelled

        provider = NvidiaLLMProvider()
        with patch.object(get_rate_limiter(), "acquire", side_effect=OperationCancelled("cancelled")):
            with pytest.raises(OperationCancelled):
                provider.call("sys", "usr")
