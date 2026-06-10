"""Unit tests for the ExtractionMonitor — live issue visualization."""

from __future__ import annotations

import threading
import time

from src.core.extraction_monitor import (
    EventCategory,
    EventSeverity,
    ExtractionEvent,
    ExtractionMonitor,
    HealthSnapshot,
    get_monitor,
)


class TestExtractionEvent:
    def test_event_creation(self):
        evt = ExtractionEvent(
            timestamp=time.time(),
            category=EventCategory.agent_error,
            severity=EventSeverity.error,
            message="Test error",
            details={"agent": "obligation"},
        )
        assert evt.category == EventCategory.agent_error
        assert evt.severity == EventSeverity.error
        assert evt.age_seconds >= 0

    def test_event_to_dict(self):
        evt = ExtractionEvent(
            timestamp=time.time(),
            category=EventCategory.passage_complete,
            severity=EventSeverity.success,
            message="Done",
        )
        d = evt.to_dict()
        assert d["category"] == "passage_complete"
        assert d["severity"] == "success"
        assert "age_seconds" in d


class TestExtractionMonitor:
    def _make_monitor(self) -> ExtractionMonitor:
        return ExtractionMonitor()

    def test_initial_state_not_running(self):
        m = self._make_monitor()
        snap = m.snapshot()
        assert not snap.is_running
        assert snap.passages_processed == 0

    def test_start_and_stop_run(self):
        m = self._make_monitor()
        m.start_run(total_passages=50)
        snap = m.snapshot()
        assert snap.is_running
        assert snap.passages_total == 50
        assert snap.passages_processed == 0

        m.stop_run()
        snap = m.snapshot()
        assert not snap.is_running

    def test_is_running_property_tracks_run_state(self):
        # The is_running property backs seconds_since_last_passage()'s gate,
        # which prevents a finished run's stale heartbeat from reporting "stuck".
        m = self._make_monitor()
        assert m.is_running is False
        m.start_run(total_passages=5)
        assert m.is_running is True
        m.stop_run()
        assert m.is_running is False

    def test_start_run_clears_previous(self):
        m = self._make_monitor()
        m.start_run(total_passages=10)
        m.emit(EventCategory.agent_error, EventSeverity.error, "old error")
        m.start_run(total_passages=20)
        snap = m.snapshot()
        assert snap.passages_total == 20
        # Events from previous run should be cleared (only run_start remains)
        assert snap.total_errors == 0

    def test_record_passage_complete(self):
        m = self._make_monitor()
        m.start_run(total_passages=5)
        m.record_passage_complete(record_id=1, section_path="Sec 1", extraction_count=3)
        m.record_passage_complete(record_id=2, section_path="Sec 2", extraction_count=0)
        snap = m.snapshot()
        assert snap.passages_processed == 2
        assert snap.extractions_created == 3

    def test_record_agent_result_success(self):
        m = self._make_monitor()
        m.start_run(total_passages=5)
        m.record_agent_result(
            agent_name="obligation",
            record_id=1,
            success=True,
            extraction_count=2,
            input_tokens=100,
            output_tokens=50,
            confidence_tier="A",
        )
        snap = m.snapshot()
        assert "obligation" in snap.agent_stats
        assert snap.agent_stats["obligation"]["successes"] == 1
        assert snap.agent_stats["obligation"]["errors"] == 0
        assert snap.confidence_tiers["A"] == 2
        assert snap.total_tokens == 150

    def test_record_agent_result_error(self):
        m = self._make_monitor()
        m.start_run(total_passages=5)
        m.record_agent_result(
            agent_name="obligation",
            record_id=1,
            error="Connection refused",
        )
        snap = m.snapshot()
        assert snap.agent_stats["obligation"]["errors"] == 1
        assert snap.total_errors == 1
        assert snap.consecutive_errors == 1
        assert snap.errors_count == 1  # severity error count

    def test_consecutive_errors_reset_on_success(self):
        m = self._make_monitor()
        m.start_run(total_passages=10)
        m.record_agent_result("obligation", 1, error="fail1")
        m.record_agent_result("obligation", 2, error="fail2")
        assert m.snapshot().consecutive_errors == 2

        m.record_agent_result("obligation", 3, success=True, extraction_count=1,
                              confidence_tier="B")
        assert m.snapshot().consecutive_errors == 0

    def test_low_confidence_emits_warning(self):
        m = self._make_monitor()
        m.start_run(total_passages=5)
        m.record_agent_result(
            agent_name="ambiguity",
            record_id=1,
            success=True,
            extraction_count=1,
            confidence_tier="D",
        )
        snap = m.snapshot()
        assert snap.warnings >= 1
        assert snap.confidence_tiers["D"] == 1

    def test_truncation_emits_warning(self):
        m = self._make_monitor()
        m.start_run(total_passages=5)
        m.record_agent_result(
            agent_name="obligation",
            record_id=1,
            success=True,
            extraction_count=1,
            confidence_tier="B",
            truncated=True,
        )
        snap = m.snapshot()
        assert snap.warnings >= 1

    def test_document_start_complete(self):
        m = self._make_monitor()
        m.start_run(total_passages=20)
        m.record_document_start("CO - SB205", passage_count=10)
        assert m.snapshot().current_document == "CO - SB205"

        m.record_document_complete("CO - SB205", extractions=15, failures=0)
        snap = m.snapshot()
        # Check events were emitted
        events = snap.recent_events
        assert any("CO - SB205" in e["message"] for e in events)

    def test_circuit_breaker_emits_critical(self):
        m = self._make_monitor()
        m.start_run(total_passages=100)
        m.record_circuit_breaker("3 consecutive failures in extraction pipeline")
        snap = m.snapshot()
        assert snap.criticals >= 1
        assert any("CIRCUIT BREAKER" in e["message"] for e in snap.recent_events)

    def test_snapshot_recent_count(self):
        m = self._make_monitor()
        m.start_run(total_passages=100)
        for i in range(30):
            m.emit(EventCategory.passage_complete, EventSeverity.success, f"passage {i}")
        snap = m.snapshot(recent_count=5)
        assert len(snap.recent_events) == 5

    def test_ring_buffer_bounded(self):
        m = self._make_monitor()
        m.start_run(total_passages=1000)
        # Emit more than MAX_EVENTS
        for i in range(ExtractionMonitor.MAX_EVENTS + 100):
            m.emit(EventCategory.passage_complete, EventSeverity.success, f"msg {i}")
        # Should be bounded
        snap = m.snapshot(recent_count=ExtractionMonitor.MAX_EVENTS + 50)
        assert len(snap.recent_events) <= ExtractionMonitor.MAX_EVENTS

    def test_failure_rate_calculation(self):
        m = self._make_monitor()
        m.start_run(total_passages=20)
        for i in range(8):
            m.record_agent_result("obligation", i, success=True, extraction_count=1,
                                  confidence_tier="A")
        for i in range(2):
            m.record_agent_result("obligation", 10 + i, error="fail")
        snap = m.snapshot()
        assert abs(snap.failure_rate - 0.2) < 0.01  # 2/10 = 0.2

    def test_tokens_per_minute(self):
        m = self._make_monitor()
        m.start_run(total_passages=10)
        m.record_agent_result(
            "obligation", 1, success=True, extraction_count=1,
            input_tokens=1000, output_tokens=500, confidence_tier="A",
        )
        snap = m.snapshot()
        assert snap.total_tokens == 1500
        assert snap.tokens_per_minute > 0

    def test_thread_safety(self):
        """Monitor should handle concurrent writes without crashing."""
        m = self._make_monitor()
        m.start_run(total_passages=1000)

        errors = []

        def writer(thread_id: int):
            try:
                for i in range(50):
                    m.record_agent_result(
                        f"agent_{thread_id}",
                        i,
                        success=True,
                        extraction_count=1,
                        input_tokens=10,
                        output_tokens=5,
                        confidence_tier="B",
                    )
                    m.record_passage_complete(
                        record_id=thread_id * 1000 + i,
                        section_path=f"Sec {i}",
                        extraction_count=1,
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread safety violation: {errors}"
        snap = m.snapshot()
        assert snap.passages_processed == 250  # 5 threads × 50 passages

    def test_health_snapshot_to_dict(self):
        m = self._make_monitor()
        m.start_run(total_passages=5)
        m.record_agent_result("obligation", 1, success=True, extraction_count=1,
                              input_tokens=100, output_tokens=50, confidence_tier="A")
        snap = m.snapshot()
        d = snap.to_dict()
        assert isinstance(d, dict)
        assert "is_running" in d
        assert "confidence_tiers" in d
        assert "agent_stats" in d
        assert "recent_events" in d

    def test_global_singleton(self):
        m1 = get_monitor()
        m2 = get_monitor()
        assert m1 is m2

    def test_cancelled_run(self):
        m = self._make_monitor()
        m.start_run(total_passages=100)
        m.record_passage_complete(1, "Sec 1", 2)
        m.stop_run(cancelled=True)
        snap = m.snapshot()
        assert not snap.is_running
        assert snap.warnings >= 1  # Cancellation emits a warning
