"""Extraction Monitor — thread-safe real-time event log for extraction runs.

Provides a shared, in-memory event buffer that the extraction pipeline writes
to as it runs.  The dashboard polls this buffer to render a live issue feed
so users can decide whether to terminate a run in progress.

The monitor tracks:
  - Per-passage outcomes: success, abstention, validation error, API error
  - Per-agent performance: which agents are failing and why
  - Confidence distribution: live histogram of extraction quality
  - Health gauges: failure rate, consecutive failures, token burn rate
  - Flagged issues: hallucinations, low-confidence extractions, truncations

All state is ephemeral (in-memory, not persisted).  A new extraction run
clears the previous run's events.  Thread-safe via threading.Lock.

Design goals:
  - Zero overhead when dashboard is not polling (events just accumulate)
  - Bounded memory: ring buffer with configurable max size
  - Simple API: emit() to write, snapshot() to read
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventSeverity(str, Enum):
    """Severity levels for extraction events."""

    info = "info"
    success = "success"
    warning = "warning"
    error = "error"
    critical = "critical"


class EventCategory(str, Enum):
    """Categories of extraction events."""

    passage_start = "passage_start"
    passage_complete = "passage_complete"
    agent_success = "agent_success"
    agent_abstention = "agent_abstention"
    agent_error = "agent_error"
    validation_error = "validation_error"
    low_confidence = "low_confidence"
    truncation = "truncation"
    circuit_breaker = "circuit_breaker"
    deduplication = "deduplication"
    run_start = "run_start"
    run_complete = "run_complete"
    run_cancelled = "run_cancelled"
    jurisdiction_mismatch = "jurisdiction_mismatch"
    document_start = "document_start"
    document_complete = "document_complete"


@dataclass
class ExtractionEvent:
    """A single event from the extraction pipeline."""

    timestamp: float
    category: EventCategory
    severity: EventSeverity
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "category": self.category.value,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
            "age_seconds": round(self.age_seconds, 1),
        }


@dataclass
class AgentStats:
    """Per-agent performance counters."""

    calls: int = 0
    successes: int = 0
    abstentions: int = 0
    errors: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_duration_ms: int = 0  # cumulative wall-clock time across all calls

    @property
    def failure_rate(self) -> float:
        if self.calls == 0:
            return 0.0
        return self.errors / self.calls

    @property
    def avg_duration_ms(self) -> float:
        if self.calls == 0:
            return 0.0
        return self.total_duration_ms / self.calls


@dataclass
class HealthSnapshot:
    """Point-in-time health indicators for the running extraction."""

    is_running: bool
    elapsed_seconds: float
    passages_processed: int
    passages_total: int
    extractions_created: int
    current_document: str | None

    # Failure tracking
    total_errors: int
    consecutive_errors: int
    failure_rate: float

    # Confidence distribution (live)
    confidence_tiers: dict[str, int]  # A/B/C/D counts

    # Per-agent stats
    agent_stats: dict[str, dict[str, Any]]

    # Token usage
    total_tokens: int
    tokens_per_minute: float

    # Issue counts by severity
    warnings: int
    errors_count: int
    criticals: int

    # Recent events (last N)
    recent_events: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_running": self.is_running,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "passages_processed": self.passages_processed,
            "passages_total": self.passages_total,
            "extractions_created": self.extractions_created,
            "current_document": self.current_document,
            "total_errors": self.total_errors,
            "consecutive_errors": self.consecutive_errors,
            "failure_rate": round(self.failure_rate, 3),
            "confidence_tiers": self.confidence_tiers,
            "agent_stats": self.agent_stats,
            "total_tokens": self.total_tokens,
            "tokens_per_minute": round(self.tokens_per_minute, 1),
            "warnings": self.warnings,
            "errors_count": self.errors_count,
            "criticals": self.criticals,
            "recent_events": self.recent_events,
        }


class ExtractionMonitor:
    """Thread-safe extraction event monitor.

    Singleton-ish: one global instance shared by the extraction pipeline
    and the dashboard polling endpoint.

    Usage in extraction pipeline::

        monitor.start_run(total_passages=100)
        monitor.emit(EventCategory.passage_complete, EventSeverity.success, "...")
        monitor.stop_run()

    Usage in dashboard endpoint::

        snapshot = monitor.snapshot(recent_count=20)
        # Render snapshot.to_dict() as HTML
    """

    MAX_EVENTS = 500  # Ring buffer size

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: deque[ExtractionEvent] = deque(maxlen=self.MAX_EVENTS)
        self._is_running = False
        self._start_time: float | None = None
        self._passages_processed = 0
        self._passages_total = 0
        self._extractions_created = 0
        self._current_document: str | None = None
        self._total_errors = 0
        self._consecutive_errors = 0
        self._confidence_tiers: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}
        self._agent_stats: dict[str, AgentStats] = {}
        self._total_tokens = 0
        self._severity_counts: dict[str, int] = {
            "warning": 0, "error": 0, "critical": 0,
        }

    @property
    def is_running(self) -> bool:
        """True while an extraction run is actively in progress."""
        return self._is_running

    def start_run(self, total_passages: int) -> None:
        """Reset state and mark a new extraction run as started."""
        with self._lock:
            self._events.clear()
            self._is_running = True
            self._start_time = time.time()
            self._passages_processed = 0
            self._passages_total = total_passages
            self._extractions_created = 0
            self._current_document = None
            self._total_errors = 0
            self._consecutive_errors = 0
            self._confidence_tiers = {"A": 0, "B": 0, "C": 0, "D": 0}
            self._agent_stats = {}
            self._total_tokens = 0
            self._severity_counts = {"warning": 0, "error": 0, "critical": 0}

        self.emit(
            EventCategory.run_start,
            EventSeverity.info,
            f"Extraction started: {total_passages} passages to process",
        )

    def stop_run(self, cancelled: bool = False) -> None:
        """Mark the current extraction run as stopped."""
        cat = EventCategory.run_cancelled if cancelled else EventCategory.run_complete
        sev = EventSeverity.warning if cancelled else EventSeverity.success
        with self._lock:
            msg = (
                f"Extraction {'cancelled' if cancelled else 'complete'}: "
                f"{self._extractions_created} extractions from "
                f"{self._passages_processed}/{self._passages_total} passages"
            )
            if self._total_errors > 0:
                msg += f" ({self._total_errors} errors)"
            self._is_running = False
        self.emit(cat, sev, msg)

    def emit(
        self,
        category: EventCategory,
        severity: EventSeverity,
        message: str,
        **details: Any,
    ) -> None:
        """Emit an extraction event."""
        event = ExtractionEvent(
            timestamp=time.time(),
            category=category,
            severity=severity,
            message=message,
            details=details,
        )
        with self._lock:
            self._events.append(event)
            if severity.value in self._severity_counts:
                self._severity_counts[severity.value] += 1

    def record_passage_complete(
        self,
        record_id: int,
        section_path: str | None,
        extraction_count: int,
    ) -> None:
        """Record a passage completion."""
        with self._lock:
            self._passages_processed += 1
            self._extractions_created += extraction_count
            if extraction_count > 0:
                self._consecutive_errors = 0

        sev = EventSeverity.success if extraction_count > 0 else EventSeverity.info
        self.emit(
            EventCategory.passage_complete,
            sev,
            f"Passage {record_id}: {extraction_count} extractions"
            + (f" [{section_path}]" if section_path else ""),
            record_id=record_id,
            extraction_count=extraction_count,
        )

    def record_agent_result(
        self,
        agent_name: str,
        record_id: int,
        success: bool = False,
        abstained: bool = False,
        error: str | None = None,
        extraction_count: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: int = 0,
        confidence_tier: str | None = None,
        truncated: bool = False,
    ) -> None:
        """Record the outcome of a single agent call."""
        with self._lock:
            stats = self._agent_stats.setdefault(agent_name, AgentStats())
            stats.calls += 1
            stats.total_input_tokens += input_tokens
            stats.total_output_tokens += output_tokens
            stats.total_duration_ms += duration_ms
            self._total_tokens += input_tokens + output_tokens

            if error:
                stats.errors += 1
                self._total_errors += 1
                self._consecutive_errors += 1
            elif abstained:
                stats.abstentions += 1
                self._consecutive_errors = 0
            else:
                stats.successes += 1
                self._consecutive_errors = 0

            if confidence_tier and confidence_tier in self._confidence_tiers:
                self._confidence_tiers[confidence_tier] += extraction_count

        if error:
            self.emit(
                EventCategory.agent_error,
                EventSeverity.error,
                f"[{agent_name}] Failed on record {record_id}: {error[:150]}",
                agent=agent_name,
                record_id=record_id,
                error=error,
            )
        elif truncated:
            self.emit(
                EventCategory.truncation,
                EventSeverity.warning,
                f"[{agent_name}] Output truncated on record {record_id}",
                agent=agent_name,
                record_id=record_id,
            )
        elif confidence_tier == "D":
            self.emit(
                EventCategory.low_confidence,
                EventSeverity.warning,
                f"[{agent_name}] Low confidence (tier D) on record {record_id}",
                agent=agent_name,
                record_id=record_id,
                tier="D",
            )

    def record_document_start(self, label: str, passage_count: int) -> None:
        """Record starting extraction for a new document."""
        with self._lock:
            self._current_document = label
        self.emit(
            EventCategory.document_start,
            EventSeverity.info,
            f"Starting [{label}]: {passage_count} passages",
            document=label,
            passage_count=passage_count,
        )

    def record_document_complete(
        self, label: str, extractions: int, failures: int
    ) -> None:
        """Record finishing extraction for a document."""
        sev = EventSeverity.success if failures == 0 else EventSeverity.warning
        self.emit(
            EventCategory.document_complete,
            sev,
            f"Done [{label}]: {extractions} extractions, {failures} failures",
            document=label,
            extractions=extractions,
            failures=failures,
        )

    def record_circuit_breaker(self, detail: str) -> None:
        """Record a circuit breaker trip."""
        self.emit(
            EventCategory.circuit_breaker,
            EventSeverity.critical,
            f"CIRCUIT BREAKER TRIPPED: {detail[:200]}",
            detail=detail,
        )

    def snapshot(self, recent_count: int = 20) -> HealthSnapshot:
        """Return a point-in-time health snapshot."""
        with self._lock:
            elapsed = (
                time.time() - self._start_time
                if self._start_time
                else 0.0
            )
            tpm = (
                (self._total_tokens / elapsed * 60)
                if elapsed > 0
                else 0.0
            )

            recent = list(self._events)[-recent_count:]
            recent_dicts = [e.to_dict() for e in reversed(recent)]

            agent_dicts = {}
            for name, stats in self._agent_stats.items():
                agent_dicts[name] = {
                    "calls": stats.calls,
                    "successes": stats.successes,
                    "abstentions": stats.abstentions,
                    "errors": stats.errors,
                    "failure_rate": round(stats.failure_rate, 3),
                    "tokens": stats.total_input_tokens + stats.total_output_tokens,
                    "avg_duration_ms": round(stats.avg_duration_ms),
                }

            total_calls = sum(s.calls for s in self._agent_stats.values())
            total_failures = sum(s.errors for s in self._agent_stats.values())
            failure_rate = total_failures / total_calls if total_calls > 0 else 0.0

            return HealthSnapshot(
                is_running=self._is_running,
                elapsed_seconds=elapsed,
                passages_processed=self._passages_processed,
                passages_total=self._passages_total,
                extractions_created=self._extractions_created,
                current_document=self._current_document,
                total_errors=self._total_errors,
                consecutive_errors=self._consecutive_errors,
                failure_rate=failure_rate,
                confidence_tiers=dict(self._confidence_tiers),
                agent_stats=agent_dicts,
                total_tokens=self._total_tokens,
                tokens_per_minute=tpm,
                warnings=self._severity_counts["warning"],
                errors_count=self._severity_counts["error"],
                criticals=self._severity_counts["critical"],
                recent_events=recent_dicts,
            )


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_monitor = ExtractionMonitor()


def get_monitor() -> ExtractionMonitor:
    """Return the global extraction monitor singleton."""
    return _monitor
