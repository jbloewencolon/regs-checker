"""Unit tests for the legislative status checker."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.status_checker import (
    VALID_TRANSITIONS,
    StatusChange,
    StatusCheckResult,
    _apply_status_change,
    _normalize_name,
    _resolve_status,
)


# ---------------------------------------------------------------------------
# _normalize_name
# ---------------------------------------------------------------------------


class TestNormalizeName:
    def test_lowercase_and_strip(self):
        assert _normalize_name("  SB 205  ") == "sb 205"

    def test_removes_punctuation(self):
        assert _normalize_name("H.B. 1234") == "hb 1234"

    def test_collapses_whitespace(self):
        assert _normalize_name("AI   Consumer  Act") == "ai consumer act"

    def test_empty_string(self):
        assert _normalize_name("") == ""


# ---------------------------------------------------------------------------
# VALID_TRANSITIONS
# ---------------------------------------------------------------------------


class TestValidTransitions:
    def test_introduced_can_become_pending(self):
        assert "pending" in VALID_TRANSITIONS["introduced"]

    def test_introduced_can_become_dead(self):
        assert "dead" in VALID_TRANSITIONS["introduced"]

    def test_pending_can_become_enacted(self):
        assert "enacted" in VALID_TRANSITIONS["pending"]

    def test_enacted_can_become_active(self):
        assert "active" in VALID_TRANSITIONS["enacted"]

    def test_active_can_become_repealed(self):
        assert "repealed" in VALID_TRANSITIONS["active"]

    def test_dead_is_mostly_terminal(self):
        # Dead bills can be reintroduced
        assert "introduced" in VALID_TRANSITIONS["dead"]
        assert "enacted" not in VALID_TRANSITIONS["dead"]

    def test_repealed_is_terminal(self):
        assert len(VALID_TRANSITIONS["repealed"]) == 0

    def test_vetoed_is_terminal(self):
        assert len(VALID_TRANSITIONS["vetoed"]) == 0

    def test_passed_one_chamber_can_become_enacted(self):
        assert "enacted" in VALID_TRANSITIONS["passed_one_chamber"]

    def test_passed_one_chamber_can_die(self):
        assert "dead" in VALID_TRANSITIONS["passed_one_chamber"]


# ---------------------------------------------------------------------------
# _resolve_status
# ---------------------------------------------------------------------------


class TestResolveStatus:
    def test_iapp_detects_change(self):
        iapp_index = {
            ("CO", "sb 205"): {"normalized_status": "enacted", "last_action": "Signed"},
        }
        change = _resolve_status("CO", "SB 205", "pending", {}, iapp_index)
        assert change is not None
        assert change.new_status == "enacted"
        assert change.source == "iapp"

    def test_orrick_detects_change(self):
        orrick_index = {
            ("CO", "sb 205"): {"normalized_status": "active", "effective_date": "2/1/2026"},
        }
        change = _resolve_status("CO", "SB 205", "enacted", orrick_index, {})
        assert change is not None
        assert change.new_status == "active"
        assert change.source == "orrick"

    def test_both_sources_agree(self):
        orrick_index = {
            ("CO", "sb 205"): {"normalized_status": "enacted", "effective_date": "2/1/2026"},
        }
        iapp_index = {
            ("CO", "sb 205"): {"normalized_status": "enacted", "last_action": "Signed"},
        }
        change = _resolve_status("CO", "SB 205", "pending", orrick_index, iapp_index)
        assert change is not None
        assert change.new_status == "enacted"
        assert change.source == "both"

    def test_no_change_returns_none(self):
        iapp_index = {
            ("CO", "sb 205"): {"normalized_status": "pending", "last_action": ""},
        }
        change = _resolve_status("CO", "SB 205", "pending", {}, iapp_index)
        assert change is None

    def test_invalid_transition_rejected(self):
        # Can't go from repealed to pending
        iapp_index = {
            ("CO", "sb 205"): {"normalized_status": "pending", "last_action": ""},
        }
        change = _resolve_status("CO", "SB 205", "repealed", {}, iapp_index)
        assert change is None

    def test_no_match_returns_none(self):
        change = _resolve_status("CO", "SB 999", "pending", {}, {})
        assert change is None

    def test_vetoed_is_detected(self):
        iapp_index = {
            ("CA", "sb 1047"): {"normalized_status": "vetoed", "last_action": "Vetoed by Governor"},
        }
        change = _resolve_status("CA", "SB 1047", "passed_one_chamber", {}, iapp_index)
        assert change is not None
        assert change.new_status == "vetoed"

    def test_dead_bill_detected(self):
        iapp_index = {
            ("TX", "hb 2060"): {"normalized_status": "dead", "last_action": "Died in committee"},
        }
        change = _resolve_status("TX", "HB 2060", "pending", {}, iapp_index)
        assert change is not None
        assert change.new_status == "dead"


# ---------------------------------------------------------------------------
# _apply_status_change
# ---------------------------------------------------------------------------


class TestApplyStatusChange:
    def test_updates_version_and_logs_event(self):
        from src.db.models import LegalEventType, TemporalStatus

        # Mock version
        version = MagicMock()
        version.id = 42

        # Mock db
        db = MagicMock()

        change = StatusChange(
            document_version_id=42,
            family_title="Colorado SB 205",
            jurisdiction_code="CO",
            old_status="pending",
            new_status="enacted",
            source="iapp",
            detail="Signed by Governor",
        )

        _apply_status_change(db, version, change)

        # Check version status was updated
        assert version.temporal_status == TemporalStatus.enacted

        # Check a LegalEvent was added
        db.add.assert_called_once()
        event = db.add.call_args[0][0]
        assert event.event_type == LegalEventType.enactment
        assert event.document_version_id == 42
        assert "pending → enacted" in event.description

        db.flush.assert_called_once()
