"""Tests for sync exclusion list."""

from src.core.sync_exclusions import (
    EXCLUDED_LAW_IDS,
    filter_excluded_rows,
    get_exclusion_reason,
    is_excluded,
)


class TestIsExcluded:
    def test_excluded_law_ids(self):
        assert is_excluded(21) is True
        assert is_excluded(188) is True
        assert is_excluded(159) is True
        assert is_excluded(60) is True

    def test_non_excluded_law_id(self):
        assert is_excluded(1) is False
        assert is_excluded(100) is False
        assert is_excluded(999) is False


class TestGetExclusionReason:
    def test_known_exclusion(self):
        reason = get_exclusion_reason(21)
        assert reason is not None
        assert "CA CCPA" in reason

    def test_unknown_law_id(self):
        assert get_exclusion_reason(999) is None


class TestFilterExcludedRows:
    def test_filters_excluded(self):
        rows = [
            {"law_id": 1, "data": "ok"},
            {"law_id": 21, "data": "excluded"},
            {"law_id": 50, "data": "ok"},
            {"law_id": 188, "data": "excluded"},
        ]
        included, excluded = filter_excluded_rows(rows)
        assert len(included) == 2
        assert len(excluded) == 2
        assert all(r["law_id"] not in (21, 188) for r in included)

    def test_no_exclusions(self):
        rows = [{"law_id": 1}, {"law_id": 2}]
        included, excluded = filter_excluded_rows(rows)
        assert len(included) == 2
        assert len(excluded) == 0

    def test_all_excluded(self):
        rows = [{"law_id": 21}, {"law_id": 188}]
        included, excluded = filter_excluded_rows(rows)
        assert len(included) == 0
        assert len(excluded) == 2

    def test_custom_key(self):
        rows = [{"my_law": 21, "data": "x"}, {"my_law": 5, "data": "y"}]
        included, excluded = filter_excluded_rows(rows, law_id_key="my_law")
        assert len(included) == 1
        assert len(excluded) == 1

    def test_exclusion_list_completeness(self):
        """Verify all 4 known-bad law_ids from the onboarding doc are present."""
        assert set(EXCLUDED_LAW_IDS.keys()) == {21, 188, 159, 60}
