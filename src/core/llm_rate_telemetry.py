"""Per-model LLM request telemetry — NIM-0a.

NVIDIA exposes no balance and no usage API (per the NIM throughput review),
so the only reliable way to know how close a run is to its rate-limit
ceiling is to observe our own traffic. This module tracks request rate,
429 outcomes, and token usage per model at the single `llm_provider.call()`
chokepoint every agent call already passes through — the same chokepoint
the review names as the right measurement point.

Thread-safe, per-model, reset once per extraction run (`reset_all()`,
mirroring `ExtractionMonitor.start_run()`'s reset pattern) so a run's
telemetry reflects only that run, not a stale prior one.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelRateStats:
    """Per-model counters. The recent-request deque is bounded so a very
    long run can't grow it unboundedly — it only needs to hold enough
    timestamps to cover one rolling window at realistic request rates."""

    requests_total: int = 0
    tokens_total: int = 0
    rpm_peak: float = 0.0
    rate_limited_seen: int = 0
    rate_limited_recovered: int = 0
    rate_limited_exhausted: int = 0
    recent_request_times: deque = field(default_factory=lambda: deque(maxlen=5000))


class LLMRateTelemetry:
    """Thread-safe per-model request/429/token tracker."""

    WINDOW_SECONDS = 60.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._models: dict[str, ModelRateStats] = {}

    def reset_all(self) -> None:
        """Clear all per-model state — call at the start of a run."""
        with self._lock:
            self._models.clear()

    def _stats_for(self, model: str) -> ModelRateStats:
        return self._models.setdefault(model, ModelRateStats())

    def record_request(self, model: str) -> None:
        """Record one outbound HTTP attempt — including attempts that will
        turn out to be 429s or retries, since each one is a real request
        against the account's rate-limit budget."""
        now = time.time()
        with self._lock:
            stats = self._stats_for(model)
            stats.requests_total += 1
            stats.recent_request_times.append(now)
            rpm = self._rpm_locked(stats, now)
            if rpm > stats.rpm_peak:
                stats.rpm_peak = rpm

    def record_tokens(self, model: str, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self._stats_for(model).tokens_total += input_tokens + output_tokens

    def record_rate_limited(self, model: str) -> None:
        """A 429 was observed for this model, whether or not the call
        eventually recovers via retry."""
        with self._lock:
            self._stats_for(model).rate_limited_seen += 1

    def record_rate_limited_recovered(self, model: str) -> None:
        """A call succeeded after hitting one or more 429s first."""
        with self._lock:
            self._stats_for(model).rate_limited_recovered += 1

    def record_rate_limited_exhausted(self, model: str) -> None:
        """Retries were exhausted while rate-limited — a real, visible failure."""
        with self._lock:
            self._stats_for(model).rate_limited_exhausted += 1

    def _rpm_locked(self, stats: ModelRateStats, now: float) -> float:
        """Caller must hold self._lock. Prunes timestamps older than the
        rolling window and returns the current trailing-window rate,
        scaled to requests/minute."""
        cutoff = now - self.WINDOW_SECONDS
        while stats.recent_request_times and stats.recent_request_times[0] < cutoff:
            stats.recent_request_times.popleft()
        return len(stats.recent_request_times) * (60.0 / self.WINDOW_SECONDS)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Point-in-time telemetry per model."""
        now = time.time()
        with self._lock:
            out: dict[str, dict[str, Any]] = {}
            for model, stats in self._models.items():
                rpm_current = self._rpm_locked(stats, now)
                out[model] = {
                    "requests_total": stats.requests_total,
                    "tokens_total": stats.tokens_total,
                    "rpm_current": round(rpm_current, 1),
                    "rpm_peak": round(stats.rpm_peak, 1),
                    "rate_limited_seen": stats.rate_limited_seen,
                    "rate_limited_recovered": stats.rate_limited_recovered,
                    "rate_limited_exhausted": stats.rate_limited_exhausted,
                }
            return out


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_telemetry = LLMRateTelemetry()


def get_llm_rate_telemetry() -> LLMRateTelemetry:
    """Return the global per-model LLM rate telemetry singleton."""
    return _telemetry
