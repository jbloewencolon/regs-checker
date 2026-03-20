"""Circuit breaker / failure tracker for batch-processing loops.

Provides a lightweight ``FailureTracker`` that monitors both *consecutive*
and *total* failure rates across a processing loop.  When thresholds are
exceeded it raises ``CircuitBreakerTripped`` with a diagnostic message
explaining what failed, how many times, and what to check.

Usage in any loop::

    tracker = FailureTracker(
        context="batch result processing",
        max_consecutive=5,
        max_failure_rate=0.8,
        min_items_for_rate=10,
    )

    for item in items:
        try:
            process(item)
            tracker.record_success()
        except SomeError as e:
            tracker.record_failure(str(e))  # raises if threshold hit

Design goals:
  - One class reused everywhere (extractor, pipeline, evaluation, seeding)
  - Clear diagnostic output: context, counts, last error, what to check
  - Rate-based threshold only kicks in after ``min_items_for_rate`` items
    to avoid false positives on small batches
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


class CircuitBreakerTripped(RuntimeError):
    """Raised when a failure threshold is exceeded.

    Attributes:
        context: Human-readable label for the loop that tripped.
        consecutive_failures: How many failures in a row.
        total_failures: Total failure count.
        total_processed: Total items attempted.
        last_error: The error message that triggered the trip.
        reason: "consecutive" or "rate" — which threshold was hit.
    """

    def __init__(
        self,
        message: str,
        *,
        context: str = "",
        consecutive_failures: int = 0,
        total_failures: int = 0,
        total_processed: int = 0,
        last_error: str = "",
        reason: str = "",
    ):
        super().__init__(message)
        self.context = context
        self.consecutive_failures = consecutive_failures
        self.total_failures = total_failures
        self.total_processed = total_processed
        self.last_error = last_error
        self.reason = reason


@dataclass
class FailureTracker:
    """Tracks failure rates in a processing loop and trips a circuit breaker.

    Parameters:
        context: Label for the loop (shown in error messages).
        max_consecutive: Abort after this many failures in a row.
        max_failure_rate: Abort if failure_count / total exceeds this (0.0–1.0).
        min_items_for_rate: Don't check failure rate until at least this many
            items have been processed (avoids false positives on tiny batches).
    """

    context: str = "processing"
    max_consecutive: int = 3
    max_failure_rate: float = 0.8
    min_items_for_rate: int = 10

    # --- Internal state ---
    _consecutive: int = field(default=0, init=False, repr=False)
    _total_failures: int = field(default=0, init=False, repr=False)
    _total_processed: int = field(default=0, init=False, repr=False)
    _last_error: str = field(default="", init=False, repr=False)
    _failure_details: list[str] = field(default_factory=list, init=False, repr=False)

    # --- Public counts (read-only access) ---

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive

    @property
    def total_failures(self) -> int:
        return self._total_failures

    @property
    def total_processed(self) -> int:
        return self._total_processed

    @property
    def failure_rate(self) -> float:
        if self._total_processed == 0:
            return 0.0
        return self._total_failures / self._total_processed

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def is_healthy(self) -> bool:
        """True if no thresholds have been exceeded (yet)."""
        return (
            self._consecutive < self.max_consecutive
            and (
                self._total_processed < self.min_items_for_rate
                or self.failure_rate <= self.max_failure_rate
            )
        )

    # --- Recording outcomes ---

    def record_success(self) -> None:
        """Record a successful operation. Resets the consecutive counter."""
        self._total_processed += 1
        self._consecutive = 0

    def record_failure(self, error: str = "", *, raise_on_trip: bool = True) -> None:
        """Record a failed operation. May raise CircuitBreakerTripped.

        Args:
            error: Description of what failed (included in diagnostics).
            raise_on_trip: If False, update counters but don't raise even
                if a threshold is hit.  Useful when the caller wants to
                check ``is_healthy`` manually.
        """
        self._total_processed += 1
        self._total_failures += 1
        self._consecutive += 1
        self._last_error = error
        if error:
            self._failure_details.append(error)
            # Keep only the last 10 for memory
            if len(self._failure_details) > 10:
                self._failure_details = self._failure_details[-10:]

        logger.warning(
            "failure_tracked",
            context=self.context,
            consecutive=self._consecutive,
            total_failures=self._total_failures,
            total_processed=self._total_processed,
            error=error[:200] if error else "",
        )

        if not raise_on_trip:
            return

        # Check consecutive threshold
        if self._consecutive >= self.max_consecutive:
            self._trip(
                reason="consecutive",
                detail=(
                    f"{self._consecutive} consecutive failures in {self.context}. "
                    f"Last error: {self._last_error}"
                ),
            )

        # Check rate threshold (only after enough items)
        if (
            self._total_processed >= self.min_items_for_rate
            and self.failure_rate > self.max_failure_rate
        ):
            pct = f"{self.failure_rate:.0%}"
            self._trip(
                reason="rate",
                detail=(
                    f"{pct} failure rate in {self.context} "
                    f"({self._total_failures}/{self._total_processed} failed). "
                    f"Last error: {self._last_error}"
                ),
            )

    def _trip(self, reason: str, detail: str) -> None:
        """Raise CircuitBreakerTripped with full diagnostics."""
        recent = "\n    ".join(self._failure_details[-5:]) if self._failure_details else "(none)"
        message = (
            f"*** CIRCUIT BREAKER TRIPPED ({self.context}) ***\n"
            f"  Reason: {detail}\n"
            f"  Total processed: {self._total_processed}\n"
            f"  Total failures:  {self._total_failures}\n"
            f"  Failure rate:    {self.failure_rate:.0%}\n"
            f"  Recent errors:\n    {recent}"
        )

        logger.error(
            "circuit_breaker_tripped",
            context=self.context,
            reason=reason,
            consecutive=self._consecutive,
            total_failures=self._total_failures,
            total_processed=self._total_processed,
            failure_rate=round(self.failure_rate, 3),
        )

        raise CircuitBreakerTripped(
            message,
            context=self.context,
            consecutive_failures=self._consecutive,
            total_failures=self._total_failures,
            total_processed=self._total_processed,
            last_error=self._last_error,
            reason=reason,
        )

    def summary(self) -> dict:
        """Return a summary dict suitable for inclusion in function return values."""
        return {
            "total_processed": self._total_processed,
            "total_failures": self._total_failures,
            "failure_rate": round(self.failure_rate, 3),
            "consecutive_failures_at_end": self._consecutive,
            "circuit_breaker_tripped": False,
        }
