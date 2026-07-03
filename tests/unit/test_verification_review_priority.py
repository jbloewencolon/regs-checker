"""Unit tests for EA0-3 — review-queue priority sync after verification.

Bug: ``ReviewQueueItem.priority`` was set once at extraction-insert time from
the extraction-time confidence tier and never revisited. When cross-validation
later demoted an extraction's tier (or flagged a critical/high-severity
issue), the review queue kept showing the stale, lower-urgency priority —
the reviewer's queue ordering silently drifted out of sync with the actual
confidence/verification state.

These tests exercise ``_sync_review_priority`` directly against a mocked
``db`` session (no real database needed) since the function's logic is a
pure decision — given a tier and an issues list, what priority should the
row have — independent of SQLAlchemy wiring.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.ingestion.verification_runner import _sync_review_priority


def _mock_db_with_item(existing_priority: int):
    """Build a mock db whose select().scalars().first() returns a fake queue item."""
    item = MagicMock()
    item.priority = existing_priority
    db = MagicMock()
    db.scalars.return_value.first.return_value = item
    return db, item


class TestPriorityEscalatesWithTier:
    def test_tier_a_sets_low_priority(self):
        db, item = _mock_db_with_item(existing_priority=0)
        _sync_review_priority(db, extraction_id=1, tier="A")
        assert item.priority == 0

    def test_tier_d_escalates_priority(self):
        # Extraction was originally tier A (priority 0); CV demoted it to D.
        db, item = _mock_db_with_item(existing_priority=0)
        _sync_review_priority(db, extraction_id=1, tier="D")
        assert item.priority == 3

    def test_tier_b_escalates_from_a(self):
        db, item = _mock_db_with_item(existing_priority=0)
        _sync_review_priority(db, extraction_id=1, tier="B")
        assert item.priority == 1


class TestPriorityNeverLowered:
    def test_higher_existing_priority_is_not_downgraded(self):
        # Some other signal already pushed this to max urgency (3); a later
        # tier-B recompute (which alone would only warrant priority 1) must
        # not silently undo that escalation.
        db, item = _mock_db_with_item(existing_priority=3)
        _sync_review_priority(db, extraction_id=1, tier="B")
        assert item.priority == 3

    def test_equal_priority_is_a_no_op(self):
        db, item = _mock_db_with_item(existing_priority=1)
        _sync_review_priority(db, extraction_id=1, tier="B")
        assert item.priority == 1


class TestCriticalIssueForcesMaxUrgency:
    def test_critical_issue_escalates_even_on_tier_a(self):
        # Tier alone says "low urgency" but a confirmed critical CV finding
        # (hallucination, wrong subject, etc.) must bump review priority
        # regardless — a ~0.08 accuracy nudge at 0.10 weight often isn't
        # enough to move the tier itself.
        db, item = _mock_db_with_item(existing_priority=0)
        _sync_review_priority(
            db, extraction_id=1, tier="A",
            issues=[{"issue_type": "hallucination", "severity": "critical"}],
        )
        assert item.priority == 3

    def test_high_severity_issue_escalates(self):
        db, item = _mock_db_with_item(existing_priority=0)
        _sync_review_priority(
            db, extraction_id=1, tier="B",
            issues=[{"issue_type": "incorrect_subject", "severity": "high"}],
        )
        assert item.priority == 3

    def test_low_and_medium_severity_do_not_force_max(self):
        db, item = _mock_db_with_item(existing_priority=0)
        _sync_review_priority(
            db, extraction_id=1, tier="A",
            issues=[
                {"issue_type": "missed_nuance", "severity": "medium"},
                {"issue_type": "formatting", "severity": "low"},
            ],
        )
        assert item.priority == 0

    def test_empty_issues_list_is_safe(self):
        db, item = _mock_db_with_item(existing_priority=0)
        _sync_review_priority(db, extraction_id=1, tier="A", issues=[])
        assert item.priority == 0

    def test_issue_missing_severity_key_is_safe(self):
        db, item = _mock_db_with_item(existing_priority=0)
        _sync_review_priority(
            db, extraction_id=1, tier="A", issues=[{"issue_type": "x"}],
        )
        assert item.priority == 0


class TestMissingReviewQueueItem:
    def test_no_matching_queue_item_is_a_no_op(self):
        db = MagicMock()
        db.scalars.return_value.first.return_value = None
        # Must not raise even though there's nothing to update.
        _sync_review_priority(db, extraction_id=999, tier="D")
        db.scalars.return_value.first.assert_called_once()
