"""Unit tests for shared dashboard helpers (src/api/routes/_dashboard_helpers.py)."""

from datetime import datetime, timedelta

from src.api.routes._dashboard_helpers import _format_last_updated


class TestFormatLastUpdated:
    def test_none_returns_never(self):
        assert _format_last_updated(None) == "never"

    def test_just_now(self):
        result = _format_last_updated(datetime.utcnow())
        assert "ago" in result or "just now" in result
        assert "UTC" in result

    def test_seconds_ago(self):
        result = _format_last_updated(datetime.utcnow() - timedelta(seconds=45))
        assert "45s ago" in result

    def test_minutes_ago(self):
        result = _format_last_updated(datetime.utcnow() - timedelta(minutes=12))
        assert "12m ago" in result

    def test_hours_ago(self):
        result = _format_last_updated(datetime.utcnow() - timedelta(hours=5))
        assert "5h ago" in result

    def test_days_ago(self):
        result = _format_last_updated(datetime.utcnow() - timedelta(days=3))
        assert "3d ago" in result

    def test_includes_absolute_timestamp(self):
        dt = datetime(2026, 7, 12, 14, 30, 0)
        result = _format_last_updated(dt)
        assert "2026-07-12 14:30" in result
