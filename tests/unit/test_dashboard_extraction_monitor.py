"""Tests for the /api/extraction-monitor dashboard fragment.

get_extraction_monitor() has no FastAPI dependencies (no `db: Session =
Depends(...)`), so it's called directly rather than through a test client —
consistent with this file's existing direct-unit-test style. Exercises the
NIM-0a/NIM-1b additions specifically: the per-model LLM rate telemetry table
and the duplicate-warnings-suppressed badge, both of which previously had no
test coverage at all (a prior gap this session found and closed) — without
them, a real run's rate-limit/pacing signals landed in the snapshot dict but
were invisible in the polled live-monitor HTML.
"""

from __future__ import annotations

from unittest.mock import patch

from src.api.routes.dashboard import get_extraction_monitor
from src.core.extraction_monitor import get_monitor
from src.core.llm_rate_limiter import get_rate_limiter
from src.core.llm_rate_telemetry import get_llm_rate_telemetry


def _reset_all():
    get_monitor().start_run(total_passages=0)
    get_llm_rate_telemetry().reset_all()
    get_rate_limiter().reset_all()


class TestIdleState:
    def test_no_run_shows_idle_message(self):
        _reset_all()
        get_monitor().stop_run()
        response = get_extraction_monitor()
        assert "No extraction running" in response.body.decode()


class TestLLMRateTelemetrySection:
    @patch("src.core.config.settings")
    def test_renders_model_row_with_stats(self, mock_settings):
        mock_settings.nvidia_rpm_limit = 35.0
        _reset_all()
        get_monitor().start_run(total_passages=5)
        get_monitor().record_agent_result(
            "obligation", 1, success=True, extraction_count=1, confidence_tier="B",
        )
        get_llm_rate_telemetry().record_request("openai/gpt-oss-120b")
        get_llm_rate_telemetry().record_tokens("openai/gpt-oss-120b", 100, 50)

        html = get_extraction_monitor().body.decode()

        assert "LLM Rate Telemetry" in html
        assert "openai/gpt-oss-120b" in html
        # tokens_total = 100 + 50 = 150; requests_total = 1.
        assert "150" in html
        assert "<td>1</td>" in html

    def test_absent_when_no_llm_calls_made(self):
        _reset_all()
        get_monitor().start_run(total_passages=5)
        get_monitor().record_agent_result(
            "obligation", 1, success=True, extraction_count=1, confidence_tier="B",
        )
        html = get_extraction_monitor().body.decode()
        assert "LLM Rate Telemetry" not in html

    @patch("src.core.config.settings")
    def test_rpm_shown_against_configured_cap(self, mock_settings):
        mock_settings.nvidia_rpm_limit = 35.0
        _reset_all()
        get_monitor().start_run(total_passages=5)
        get_monitor().record_agent_result(
            "obligation", 1, success=True, extraction_count=1, confidence_tier="B",
        )
        get_llm_rate_telemetry().record_request("openai/gpt-oss-120b")

        html = get_extraction_monitor().body.decode()
        assert "/ 35" in html

    @patch("src.core.config.settings")
    def test_pacing_disabled_shows_pacing_off_label(self, mock_settings):
        mock_settings.nvidia_rpm_limit = 0
        _reset_all()
        get_monitor().start_run(total_passages=5)
        get_monitor().record_agent_result(
            "obligation", 1, success=True, extraction_count=1, confidence_tier="B",
        )
        get_llm_rate_telemetry().record_request("openai/gpt-oss-120b")

        html = get_extraction_monitor().body.decode()
        assert "pacing off" in html

    @patch("src.core.config.settings")
    def test_exhausted_429_count_rendered(self, mock_settings):
        mock_settings.nvidia_rpm_limit = 35.0
        _reset_all()
        get_monitor().start_run(total_passages=5)
        get_monitor().record_agent_result(
            "obligation", 1, success=True, extraction_count=1, confidence_tier="B",
        )
        get_llm_rate_telemetry().record_request("openai/gpt-oss-120b")
        get_llm_rate_telemetry().record_rate_limited("openai/gpt-oss-120b")
        get_llm_rate_telemetry().record_rate_limited_exhausted("openai/gpt-oss-120b")

        html = get_extraction_monitor().body.decode()
        assert "var(--danger)" in html  # exhausted count is colored as danger


class TestDuplicateWarningsBadge:
    def test_badge_absent_when_no_duplicates_suppressed(self):
        _reset_all()
        get_monitor().start_run(total_passages=5)
        get_monitor().record_agent_result(
            "obligation", 1, success=True, extraction_count=1, confidence_tier="D",
        )
        html = get_extraction_monitor().body.decode()
        assert "duplicate warnings collapsed" not in html

    def test_badge_present_when_duplicates_suppressed(self):
        _reset_all()
        get_monitor().start_run(total_passages=5)
        for _ in range(5):
            get_monitor().record_agent_result(
                "preemption", 674, success=True, extraction_count=1, confidence_tier="D",
            )
        html = get_extraction_monitor().body.decode()
        assert "duplicate warnings collapsed" in html
        assert "4 duplicate warnings collapsed" in html
