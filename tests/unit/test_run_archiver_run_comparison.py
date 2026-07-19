"""Tests for RunArchiver's run-comparison summary and per-output timestamps.

Every output file a run produces (run_summary.json, agent_stats.json, and
every CSV/JSONL export) should (1) carry a visible, distinct date/time for
the run that produced it, and (2) let an analyst compare this run to a
future one without cross-referencing a second file: failures, average time
per agent, total time, and related throughput/quality signals.

`_build_run_comparison_summary()` builds that block once per finalize()
call from the live ExtractionMonitor snapshot + the run_extraction()
summary dict; `_run_header_line()` is the CSV/JSONL-side echo of the same
timestamp. Both are exercised here against the real ExtractionMonitor
singleton (populated via its public record_agent_result API, mirroring how
run_extraction() actually drives it) rather than a mock, so the test proves
the real integration point works, not just a stub.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.core.extraction_monitor import get_monitor
from src.core.llm_rate_telemetry import get_llm_rate_telemetry
from src.core.run_archiver import RunArchiver


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """Duck-typed session stub: every export method only calls
    db.execute(query).all(); the query itself is never inspected."""

    def __init__(self, rows=None):
        self._rows = rows if rows is not None else []

    def execute(self, _query):
        return _FakeResult(self._rows)


@pytest.fixture(autouse=True)
def _reset_monitor():
    # The monitor is a process-wide singleton; each test gets a clean slate
    # and other suites running in the same process aren't left polluted.
    get_monitor().start_run(total_passages=0)
    yield
    get_monitor().start_run(total_passages=0)


def _populate_monitor():
    """Drive the real monitor through a small, known-shape fake run so the
    comparison summary's numbers are independently checkable by hand."""
    monitor = get_monitor()
    monitor.start_run(total_passages=3)
    # obligation: 2 successes, 100ms/200ms => avg 150ms, 0 errors
    monitor.record_agent_result("obligation", 1, success=True, duration_ms=100)
    monitor.record_agent_result("obligation", 2, success=True, duration_ms=200)
    # definition_actor: 1 success (50ms), 1 error (150ms) => avg 100ms, 1 error
    monitor.record_agent_result("definition_actor", 1, success=True, duration_ms=50)
    monitor.record_agent_result(
        "definition_actor", 2, success=False, error="boom", duration_ms=150
    )
    monitor.record_agent_result(
        "obligation", 3, success=True, duration_ms=300,
        confidence_tier="A", extraction_count=1,
    )
    monitor.stop_run()


class TestRunHeaderLine:
    def test_contains_started_at_and_run_type(self, tmp_path):
        started = datetime(2026, 7, 19, 14, 30, 22)
        archiver = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=started)
        line = archiver._run_header_line()
        assert "2026-07-19 14:30:22" in line
        assert "type=extract" in line
        assert line.startswith("# RUN:")
        assert "run_summary.json" in line

    def test_two_runs_at_different_times_are_distinguishable(self, tmp_path):
        a = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=datetime(2026, 7, 19, 9, 0, 0))
        b = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=datetime(2026, 7, 20, 9, 0, 0))
        assert a._run_header_line() != b._run_header_line()

    def test_different_run_types_are_distinguishable(self, tmp_path):
        started = datetime(2026, 7, 19, 9, 0, 0)
        a = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=started)
        b = RunArchiver(run_dir=tmp_path, run_type="retry", started_at=started)
        assert a._run_header_line() != b._run_header_line()


class TestBuildRunComparisonSummary:
    def test_failures_avg_duration_and_total_time(self, tmp_path):
        _populate_monitor()
        started = datetime(2026, 7, 19, 14, 30, 22)
        archiver = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=started)
        summary = {
            "total_extractions": 5,
            "records_processed": 3,
            "records_failed": 0,
            "circuit_breaker_tripped": False,
            "token_usage": {"total_tokens": 12345},
            "conservation": {"conserved": True},
        }

        comparison = archiver._build_run_comparison_summary(summary, duration_seconds=60.0)

        assert comparison["run_timestamp"] == started.isoformat() + "Z"
        assert comparison["run_type"] == "extract"
        assert comparison["total_duration_seconds"] == 60.0
        assert comparison["total_extractions"] == 5
        assert comparison["extractions_per_minute"] == 5.0

        failures = comparison["failures"]
        assert failures["total_agent_errors"] == 1
        assert failures["per_agent_errors"] == {"obligation": 0, "definition_actor": 1}
        assert failures["circuit_breaker_tripped"] is False

        # obligation: (100+200+300)/3 = 200ms; definition_actor: (50+150)/2 = 100ms
        assert comparison["avg_duration_ms_per_agent"]["obligation"] == 200
        assert comparison["avg_duration_ms_per_agent"]["definition_actor"] == 100
        # overall: (100+200+300+50+150)/5 = 160ms
        assert comparison["avg_duration_ms_overall"] == 160

        assert comparison["token_usage_total"] == 12345
        assert comparison["conservation_ok"] is True
        assert comparison["confidence_tier_distribution"]["A"] >= 1

    def test_zero_duration_never_divides_by_zero(self, tmp_path):
        archiver = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=datetime.utcnow())
        comparison = archiver._build_run_comparison_summary(
            {"total_extractions": 0}, duration_seconds=0.0
        )
        assert comparison["extractions_per_minute"] == 0.0
        assert comparison["avg_duration_ms_overall"] == 0

    def test_circuit_breaker_detail_surfaced(self, tmp_path):
        archiver = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=datetime.utcnow())
        comparison = archiver._build_run_comparison_summary(
            {
                "circuit_breaker_tripped": True,
                "circuit_breaker_detail": "10 consecutive failures",
            },
            duration_seconds=30.0,
        )
        assert comparison["failures"]["circuit_breaker_tripped"] is True
        assert comparison["failures"]["circuit_breaker_detail"] == "10 consecutive failures"

    def test_llm_throttle_telemetry_block_present(self, tmp_path):
        """NIM-0a: per-model request-rate/429/token telemetry from the
        llm_provider.py chokepoint should ride alongside the rest of the
        comparison summary — populated here via the real telemetry
        singleton's public API, mirroring how NvidiaLLMProvider.call()
        actually drives it."""
        get_llm_rate_telemetry().record_request("openai/gpt-oss-120b")
        get_llm_rate_telemetry().record_tokens("openai/gpt-oss-120b", input_tokens=100, output_tokens=50)
        get_llm_rate_telemetry().record_rate_limited("openai/gpt-oss-120b")
        get_llm_rate_telemetry().record_rate_limited_recovered("openai/gpt-oss-120b")

        archiver = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=datetime.utcnow())
        comparison = archiver._build_run_comparison_summary(
            {"total_extractions": 0}, duration_seconds=10.0
        )

        telemetry = comparison["llm_throttle_telemetry"]
        assert "openai/gpt-oss-120b" in telemetry
        model_stats = telemetry["openai/gpt-oss-120b"]
        assert model_stats["requests_total"] == 1
        assert model_stats["tokens_total"] == 150
        assert model_stats["rate_limited_seen"] == 1
        assert model_stats["rate_limited_recovered"] == 1


class TestFinalizeWritesConsistentTimestamps:
    """End-to-end: finalize() against a DB stub that returns no rows for
    every query, so every export writes just its header content."""

    def test_run_summary_json_has_comparison_block(self, tmp_path):
        _populate_monitor()
        started = datetime(2026, 7, 19, 14, 30, 22)
        archiver = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=started)
        summary = {"total_extractions": 5, "records_processed": 3, "token_usage": {"total_tokens": 100}}

        archiver.finalize(_FakeDB([]), summary)

        run_summary = json.loads((tmp_path / "run_summary.json").read_text())
        assert run_summary["started_at"] == started.isoformat()
        comparison = run_summary["run_comparison_summary"]
        assert comparison["run_timestamp"] == started.isoformat() + "Z"
        assert comparison["failures"]["per_agent_errors"] == {
            "obligation": 0, "definition_actor": 1,
        }
        assert comparison["avg_duration_ms_per_agent"]["obligation"] == 200

    def test_agent_stats_json_echoes_same_comparison_block(self, tmp_path):
        _populate_monitor()
        started = datetime(2026, 7, 19, 14, 30, 22)
        archiver = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=started)

        archiver.finalize(_FakeDB([]), {"total_extractions": 5, "records_processed": 3})

        run_summary = json.loads((tmp_path / "run_summary.json").read_text())
        agent_stats = json.loads((tmp_path / "agent_stats.json").read_text())

        assert agent_stats["run_summary"] == run_summary["run_comparison_summary"]

    def test_extractions_csv_carries_run_header(self, tmp_path):
        started = datetime(2026, 7, 19, 14, 30, 22)
        archiver = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=started)

        archiver.finalize(_FakeDB([]), {"total_extractions": 0, "records_processed": 0})

        content = (tmp_path / "extractions.csv").read_text()
        first_line = content.splitlines()[0]
        assert "2026-07-19 14:30:22" in first_line
        assert "type=extract" in first_line

    def test_two_finalize_calls_at_different_times_produce_different_headers(self, tmp_path_factory):
        dir_a = tmp_path_factory.mktemp("run_a")
        dir_b = tmp_path_factory.mktemp("run_b")
        archiver_a = RunArchiver(run_dir=dir_a, run_type="extract", started_at=datetime(2026, 7, 19, 9, 0, 0))
        archiver_b = RunArchiver(run_dir=dir_b, run_type="extract", started_at=datetime(2026, 7, 20, 9, 0, 0))

        archiver_a.finalize(_FakeDB([]), {"total_extractions": 0, "records_processed": 0})
        archiver_b.finalize(_FakeDB([]), {"total_extractions": 0, "records_processed": 0})

        csv_a = (dir_a / "extractions.csv").read_text().splitlines()[0]
        csv_b = (dir_b / "extractions.csv").read_text().splitlines()[0]
        assert csv_a != csv_b

        summary_a = json.loads((dir_a / "run_summary.json").read_text())
        summary_b = json.loads((dir_b / "run_summary.json").read_text())
        assert summary_a["run_comparison_summary"]["run_timestamp"] != \
            summary_b["run_comparison_summary"]["run_timestamp"]


class TestByAgentExportCarriesRunHeader:
    def test_by_agent_csv_has_header_line(self, tmp_path):
        started = datetime(2026, 7, 19, 14, 30, 22)
        archiver = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=started)

        # 14-column row matching _export_by_agent's _cols order.
        row = (
            1, "obligation", None, 0.9, None, "test-model",
            {"k": "v"}, [], None, "Section 1", "US", "Short Cite",
            "Title", "canon-key-1",
        )
        archiver._export_by_agent(_FakeDB([row]))

        csv_path = tmp_path / "by_agent" / "obligation.csv"
        assert csv_path.exists()
        first_line = csv_path.read_text().splitlines()[0]
        assert "2026-07-19 14:30:22" in first_line


class TestBillLevelExportCarriesRunHeader:
    def test_bill_level_csv_has_header_line(self, tmp_path):
        started = datetime(2026, 7, 19, 14, 30, 22)
        archiver = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=started)

        row = (
            1, "enforcement_agent", "test-model", 100, 50, False,
            None, {"k": "v"}, None, "US", "Short Cite", "Title",
        )
        archiver._export_bill_level_extractions(_FakeDB([row]))

        csv_path = tmp_path / "bill_level_extractions.csv"
        assert csv_path.exists()
        first_line = csv_path.read_text().splitlines()[0]
        assert "2026-07-19 14:30:22" in first_line


class TestLowConfidenceExportCarriesRunHeader:
    def test_low_confidence_csv_and_jsonl_have_run_timestamp(self, tmp_path):
        started = datetime(2026, 7, 19, 14, 30, 22)
        archiver = RunArchiver(run_dir=tmp_path, run_type="extract", started_at=started)

        row = (
            1, None, 0.4, None, None, {"k": "v"}, [], None,
            "passage text", "US", "Title", {},
        )
        archiver._export_low_confidence(_FakeDB([row]))

        csv_first_line = (tmp_path / "low_confidence_extractions.csv").read_text().splitlines()[0]
        assert "2026-07-19 14:30:22" in csv_first_line

        jsonl_first_line = (tmp_path / "low_confidence_extractions.jsonl").read_text().splitlines()[0]
        disclaimer_record = json.loads(jsonl_first_line)
        assert disclaimer_record["run_timestamp"] == started.isoformat() + "Z"
        assert disclaimer_record["run_type"] == "extract"
