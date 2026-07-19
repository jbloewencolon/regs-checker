"""Per-model client-side rate limiter — NIM-1a.

A shared, process-wide sliding-window limiter so concurrent agent calls to
the same NVIDIA model can't collectively exceed a configured requests-per-
minute cap. The 2026-07-19 live-run evidence (see tasks.md's NIM plan)
showed the pipeline using only a small fraction of the reported ~40 RPM/
model cap — this isn't a defense against current throttling, it's the
guardrail that lets concurrency be safely RAISED into that unused headroom:
without a shared limiter, raising ThreadPoolExecutor/triage concurrency
would just reproduce the throttling problem faster.

Distinct from `max_concurrent_agents_per_model` (RR6b), which caps
*concurrency* for VRAM reasons on local models — this caps *rate*, and both
controls are independent and compose.

Disabled entirely when the configured cap is <= 0.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """Thread-safe, per-model sliding-window request-rate limiter.

    `acquire()` blocks the calling thread until making one more request for
    the given model would not exceed `cap_rpm` in the trailing 60s window,
    then reserves the slot atomically (so concurrent callers can't all pass
    a check before any of them records their request). Returns the total
    seconds waited so callers can feed it into telemetry (NIM-1b).
    """

    WINDOW_SECONDS = 60.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recent: dict[str, deque[float]] = {}

    def reset_all(self) -> None:
        """Clear all per-model state — call at the start of a run so a new
        run's pacing isn't shaped by a prior run's request history."""
        with self._lock:
            self._recent.clear()

    def _prune_locked(self, dq: deque[float], now: float) -> None:
        """Caller must hold self._lock."""
        cutoff = now - self.WINDOW_SECONDS
        while dq and dq[0] < cutoff:
            dq.popleft()

    def acquire(
        self,
        model: str,
        cap_rpm: float,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> float:
        """Block (if needed) until one more request for `model` fits under
        `cap_rpm`, then reserve the slot. Returns seconds waited (0.0 if no
        wait was needed).

        `cap_rpm <= 0` disables limiting entirely — every call returns 0.0
        immediately without touching internal state.

        `sleep_fn` is injectable so callers can supply a cancellable sleep
        (mirroring NvidiaLLMProvider's own backoff pattern, so a cancelled
        extraction run can interrupt a pacing wait promptly instead of
        blocking it out) and so tests can run without real wall-clock waits.
        """
        if cap_rpm <= 0:
            return 0.0

        total_waited = 0.0
        while True:
            with self._lock:
                now = time.time()
                dq = self._recent.setdefault(model, deque())
                self._prune_locked(dq, now)
                if len(dq) < cap_rpm:
                    dq.append(now)
                    return total_waited
                # At capacity: wait until the oldest entry in the window
                # ages out, freeing room for this request.
                wait_needed = dq[0] + self.WINDOW_SECONDS - now

            # Sleep outside the lock so other threads can make progress
            # (e.g. a different model's requests, or this same model
            # freeing capacity concurrently).
            wait_needed = max(wait_needed, 0.05)
            sleep_fn(wait_needed)
            total_waited += wait_needed


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Return the global per-model rate limiter singleton."""
    return _limiter
