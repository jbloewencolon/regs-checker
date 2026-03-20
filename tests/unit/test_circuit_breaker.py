"""Unit tests for the shared circuit breaker / FailureTracker."""

from __future__ import annotations

import pytest

from src.core.circuit_breaker import CircuitBreakerTripped, FailureTracker


# ---------------------------------------------------------------------------
# CircuitBreakerTripped exception
# ---------------------------------------------------------------------------


class TestCircuitBreakerTripped:
    def test_is_runtime_error(self):
        assert issubclass(CircuitBreakerTripped, RuntimeError)

    def test_message(self):
        exc = CircuitBreakerTripped("boom")
        assert str(exc) == "boom"

    def test_attributes(self):
        exc = CircuitBreakerTripped(
            "msg",
            context="test",
            consecutive_failures=3,
            total_failures=5,
            total_processed=10,
            last_error="timeout",
            reason="consecutive",
        )
        assert exc.context == "test"
        assert exc.consecutive_failures == 3
        assert exc.total_failures == 5
        assert exc.total_processed == 10
        assert exc.last_error == "timeout"
        assert exc.reason == "consecutive"


# ---------------------------------------------------------------------------
# FailureTracker — initial state
# ---------------------------------------------------------------------------


class TestFailureTrackerInit:
    def test_defaults(self):
        t = FailureTracker()
        assert t.context == "processing"
        assert t.max_consecutive == 3
        assert t.max_failure_rate == 0.8
        assert t.min_items_for_rate == 10

    def test_custom_params(self):
        t = FailureTracker(
            context="my loop",
            max_consecutive=5,
            max_failure_rate=0.5,
            min_items_for_rate=20,
        )
        assert t.context == "my loop"
        assert t.max_consecutive == 5
        assert t.max_failure_rate == 0.5
        assert t.min_items_for_rate == 20

    def test_initial_counters(self):
        t = FailureTracker()
        assert t.consecutive_failures == 0
        assert t.total_failures == 0
        assert t.total_processed == 0
        assert t.failure_rate == 0.0
        assert t.last_error == ""
        assert t.is_healthy is True


# ---------------------------------------------------------------------------
# FailureTracker — recording outcomes
# ---------------------------------------------------------------------------


class TestRecordSuccess:
    def test_increments_processed(self):
        t = FailureTracker()
        t.record_success()
        assert t.total_processed == 1
        assert t.total_failures == 0

    def test_resets_consecutive(self):
        t = FailureTracker(max_consecutive=10)
        t.record_failure("err1", raise_on_trip=False)
        t.record_failure("err2", raise_on_trip=False)
        assert t.consecutive_failures == 2
        t.record_success()
        assert t.consecutive_failures == 0


class TestRecordFailure:
    def test_increments_counters(self):
        t = FailureTracker(max_consecutive=10)
        t.record_failure("oops", raise_on_trip=False)
        assert t.total_processed == 1
        assert t.total_failures == 1
        assert t.consecutive_failures == 1
        assert t.last_error == "oops"

    def test_consecutive_failures_accumulate(self):
        t = FailureTracker(max_consecutive=10)
        for i in range(5):
            t.record_failure(f"err{i}", raise_on_trip=False)
        assert t.consecutive_failures == 5
        assert t.total_failures == 5

    def test_no_raise_when_below_threshold(self):
        t = FailureTracker(max_consecutive=5)
        # 4 failures should NOT trip
        for i in range(4):
            t.record_failure(f"err{i}")
        assert t.consecutive_failures == 4
        assert t.is_healthy is True

    def test_raise_on_trip_false(self):
        t = FailureTracker(max_consecutive=2)
        t.record_failure("err1", raise_on_trip=False)
        t.record_failure("err2", raise_on_trip=False)
        t.record_failure("err3", raise_on_trip=False)
        # Would have tripped at 2, but raise_on_trip=False
        assert t.consecutive_failures == 3
        assert t.is_healthy is False


# ---------------------------------------------------------------------------
# FailureTracker — consecutive threshold trips
# ---------------------------------------------------------------------------


class TestConsecutiveTrip:
    def test_trips_at_threshold(self):
        t = FailureTracker(max_consecutive=3, context="test_loop")
        t.record_failure("err1")
        t.record_failure("err2")
        with pytest.raises(CircuitBreakerTripped) as exc_info:
            t.record_failure("err3")
        exc = exc_info.value
        assert exc.reason == "consecutive"
        assert exc.consecutive_failures == 3
        assert exc.context == "test_loop"
        assert "err3" in exc.last_error

    def test_success_resets_consecutive(self):
        """Successes interspersed should prevent consecutive trip."""
        t = FailureTracker(max_consecutive=3)
        t.record_failure("err1")
        t.record_failure("err2")
        t.record_success()  # Reset
        t.record_failure("err3")
        t.record_failure("err4")
        # Only 2 consecutive now, shouldn't trip
        assert t.consecutive_failures == 2
        assert t.total_failures == 4

    def test_trips_after_success_gap(self):
        """After a success, 3 more consecutive failures should trip."""
        t = FailureTracker(max_consecutive=3)
        t.record_failure("a")
        t.record_success()
        t.record_failure("b")
        t.record_failure("c")
        with pytest.raises(CircuitBreakerTripped):
            t.record_failure("d")


# ---------------------------------------------------------------------------
# FailureTracker — rate threshold trips
# ---------------------------------------------------------------------------


class TestRateTrip:
    def test_does_not_trip_below_min_items(self):
        """Rate check shouldn't fire until min_items_for_rate is reached."""
        t = FailureTracker(
            max_consecutive=100,  # Disable consecutive
            max_failure_rate=0.5,
            min_items_for_rate=10,
        )
        # 9 failures out of 9 items = 100% rate, but below min_items
        for i in range(9):
            t.record_failure(f"err{i}")
        assert t.total_processed == 9
        assert t.is_healthy is True  # Still healthy because < min_items

    def test_trips_at_min_items(self):
        """Rate check fires once min_items_for_rate is reached."""
        t = FailureTracker(
            max_consecutive=100,
            max_failure_rate=0.5,
            min_items_for_rate=10,
        )
        # 9 failures, then the 10th should trip (100% > 50%)
        for i in range(9):
            t.record_failure(f"err{i}", raise_on_trip=False)
        with pytest.raises(CircuitBreakerTripped) as exc_info:
            t.record_failure("err10")
        assert exc_info.value.reason == "rate"

    def test_healthy_rate_does_not_trip(self):
        """Below the rate threshold = no trip."""
        t = FailureTracker(
            max_consecutive=100,
            max_failure_rate=0.5,
            min_items_for_rate=10,
        )
        # 3 failures and 7 successes = 30% < 50%
        for _ in range(3):
            t.record_failure("err", raise_on_trip=False)
        for _ in range(7):
            t.record_success()
        assert t.failure_rate == 0.3
        assert t.is_healthy is True


# ---------------------------------------------------------------------------
# FailureTracker — summary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary_dict(self):
        t = FailureTracker(context="test")
        t.record_success()
        t.record_failure("err", raise_on_trip=False)
        t.record_success()

        s = t.summary()
        assert s["total_processed"] == 3
        assert s["total_failures"] == 1
        assert s["failure_rate"] == round(1 / 3, 3)
        assert s["consecutive_failures_at_end"] == 0
        assert s["circuit_breaker_tripped"] is False


# ---------------------------------------------------------------------------
# FailureTracker — diagnostic message quality
# ---------------------------------------------------------------------------


class TestDiagnosticMessage:
    def test_trip_message_contains_context(self):
        t = FailureTracker(max_consecutive=1, context="my batch loop")
        with pytest.raises(CircuitBreakerTripped, match="my batch loop"):
            t.record_failure("timeout error")

    def test_trip_message_contains_counts(self):
        t = FailureTracker(max_consecutive=2, context="x")
        t.record_failure("err1")
        with pytest.raises(CircuitBreakerTripped) as exc_info:
            t.record_failure("err2")
        msg = str(exc_info.value)
        assert "Total failures:" in msg
        assert "Total processed:" in msg
        assert "Failure rate:" in msg
        assert "Recent errors:" in msg

    def test_trip_message_contains_recent_errors(self):
        t = FailureTracker(max_consecutive=3, context="x")
        t.record_failure("alpha")
        t.record_failure("beta")
        with pytest.raises(CircuitBreakerTripped) as exc_info:
            t.record_failure("gamma")
        msg = str(exc_info.value)
        assert "alpha" in msg
        assert "beta" in msg
        assert "gamma" in msg


# ---------------------------------------------------------------------------
# Integration: FailureTracker imported into extractor
# ---------------------------------------------------------------------------


class TestExtractorImport:
    def test_extractor_imports_from_shared_module(self):
        """extractor.py should import CircuitBreakerTripped from circuit_breaker."""
        from src.ingestion.extractor import CircuitBreakerTripped as ExtCBT
        assert ExtCBT is CircuitBreakerTripped

    def test_extractor_has_threshold_constant(self):
        from src.ingestion.extractor import CIRCUIT_BREAKER_THRESHOLD
        assert CIRCUIT_BREAKER_THRESHOLD == 3
