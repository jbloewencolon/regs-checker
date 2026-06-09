"""Unit tests for Phase 4b IAPP alignment module."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.core.iapp_alignment import (
    IAPPEntry,
    IAPP_SCOPE_TO_ACTORS,
    check_iapp_alignment,
    get_iapp_entry,
    get_iapp_entry_for_context,
    reload_iapp_index,
    _parse_scope_codes,
)


@pytest.fixture(autouse=True)
def reset_index():
    """Reload the IAPP index before each test."""
    reload_iapp_index()
    yield
    reload_iapp_index()


# ---------------------------------------------------------------------------
# Scope code parsing
# ---------------------------------------------------------------------------


class TestParseScopeCodes:
    def test_single_code(self):
        assert _parse_scope_codes("G") == ["G"]

    def test_compound_code(self):
        codes = _parse_scope_codes("F,D,G")
        assert codes == ["F", "D", "G"]

    def test_spaces_stripped(self):
        codes = _parse_scope_codes("A*, G")
        assert codes == ["A*", "G"]

    def test_empty_returns_empty(self):
        assert _parse_scope_codes("") == []


# ---------------------------------------------------------------------------
# IAPPEntry
# ---------------------------------------------------------------------------


class TestIAPPEntry:
    def _make_entry(self, section="LAWS SIGNED", scope_raw="D", **kwargs) -> IAPPEntry:
        defaults = dict(
            section=section,
            jurisdiction="Colorado",
            bill_number="SB 205",
            scope_raw=scope_raw,
            scope_codes=_parse_scope_codes(scope_raw),
            obligations={},
        )
        defaults.update(kwargs)
        return IAPPEntry(**defaults)

    def test_is_enacted_true(self):
        assert self._make_entry(section="LAWS SIGNED").is_enacted is True

    def test_is_enacted_false_for_pending(self):
        assert self._make_entry(section="ACTIVE BILLS").is_enacted is False

    def test_has_data_true_with_scope(self):
        assert self._make_entry(scope_raw="D").has_data is True

    def test_has_data_false_empty(self):
        assert self._make_entry(scope_raw="").has_data is False

    def test_actor_set_deployer_scope(self):
        entry = self._make_entry(scope_raw="D")
        assert "deployer" in entry.actor_set
        assert "operator" in entry.actor_set
        assert "developer" not in entry.actor_set

    def test_actor_set_general_scope(self):
        entry = self._make_entry(scope_raw="G")
        # G means all actors
        assert "developer" in entry.actor_set
        assert "deployer" in entry.actor_set
        assert "regulated_entity" in entry.actor_set

    def test_actor_set_frontier_scope(self):
        entry = self._make_entry(scope_raw="F")
        assert "developer" in entry.actor_set
        assert "provider" in entry.actor_set
        assert "deployer" not in entry.actor_set

    def test_actor_set_compound_scope(self):
        entry = self._make_entry(scope_raw="F,D")
        assert "developer" in entry.actor_set  # from F
        assert "deployer" in entry.actor_set   # from D

    def test_obligation_types_non_empty(self):
        entry = self._make_entry(
            obligations={"Assessments": "1,2", "Training": "", "General notice": "2"}
        )
        types = entry.obligation_types
        assert "Assessments" in types
        assert "General notice" in types
        assert "Training" not in types


# ---------------------------------------------------------------------------
# check_iapp_alignment
# ---------------------------------------------------------------------------


class TestCheckIAPPAlignment:
    def _entry(self, scope_raw: str) -> IAPPEntry:
        return IAPPEntry(
            section="LAWS SIGNED",
            jurisdiction="Colorado",
            bill_number="SB 205",
            scope_raw=scope_raw,
            scope_codes=_parse_scope_codes(scope_raw),
            obligations={},
        )

    def test_aligned_deployer_in_d_scope(self):
        assert check_iapp_alignment("deployer", self._entry("D")) == "aligned"

    def test_aligned_developer_in_f_scope(self):
        assert check_iapp_alignment("developer", self._entry("F")) == "aligned"

    def test_aligned_all_actors_in_g_scope(self):
        entry = self._entry("G")
        assert check_iapp_alignment("deployer", entry) == "aligned"
        assert check_iapp_alignment("developer", entry) == "aligned"
        assert check_iapp_alignment("regulated_entity", entry) == "aligned"

    def test_scope_mismatch_deployer_in_f_scope(self):
        assert check_iapp_alignment("deployer", self._entry("F")) == "scope_mismatch"

    def test_scope_mismatch_developer_in_d_scope(self):
        assert check_iapp_alignment("developer", self._entry("D")) == "scope_mismatch"

    def test_tracker_silent_no_entry(self):
        assert check_iapp_alignment("deployer", None) == "tracker_silent"

    def test_tracker_silent_empty_scope(self):
        entry = self._entry("")
        assert check_iapp_alignment("deployer", entry) == "tracker_silent"

    def test_tracker_silent_none_subject(self):
        entry = self._entry("D")
        assert check_iapp_alignment(None, entry) == "tracker_silent"

    def test_compound_scope_fd_deployer_aligned(self):
        entry = self._entry("F,D")
        assert check_iapp_alignment("deployer", entry) == "aligned"
        assert check_iapp_alignment("developer", entry) == "aligned"


# ---------------------------------------------------------------------------
# Live CSV lookups (uses actual static/iapp_law_tracker.csv)
# ---------------------------------------------------------------------------


class TestLiveLookup:
    def test_colorado_sb205_found(self):
        """Colorado SB 205 should be in the IAPP tracker."""
        entry = get_iapp_entry("Colorado", "SB 205")
        assert entry is not None
        assert entry.has_data
        assert "D" in entry.scope_codes

    def test_abbreviation_lookup_co_sb205(self):
        """Should match via state abbreviation 'CO' as well."""
        entry = get_iapp_entry("CO", "SB 205")
        assert entry is not None

    def test_case_insensitive(self):
        entry = get_iapp_entry("colorado", "sb 205")
        assert entry is not None

    def test_nonexistent_law_returns_none(self):
        assert get_iapp_entry("California", "SB 99999") is None

    def test_unknown_jurisdiction_returns_none(self):
        assert get_iapp_entry("Narnia", "SB 1") is None


# ---------------------------------------------------------------------------
# get_iapp_entry_for_context
# ---------------------------------------------------------------------------


class TestGetIAPPEntryForContext:
    def test_context_with_short_cite_and_jurisdiction_name(self):
        ctx = {
            "jurisdiction": "CO",
            "jurisdiction_name": "Colorado",
            "short_cite": "SB 205",
        }
        entry = get_iapp_entry_for_context(ctx)
        assert entry is not None

    def test_context_with_abbreviation_only(self):
        ctx = {
            "jurisdiction": "CO",
            "jurisdiction_name": None,
            "short_cite": "SB 205",
        }
        entry = get_iapp_entry_for_context(ctx)
        assert entry is not None

    def test_context_missing_bill_returns_none(self):
        ctx = {"jurisdiction": "CO", "jurisdiction_name": "Colorado"}
        assert get_iapp_entry_for_context(ctx) is None

    def test_context_unknown_law_returns_none(self):
        ctx = {
            "jurisdiction": "CO",
            "jurisdiction_name": "Colorado",
            "short_cite": "SB 99999",
        }
        assert get_iapp_entry_for_context(ctx) is None


# ---------------------------------------------------------------------------
# Scope code coverage sanity
# ---------------------------------------------------------------------------


class TestScopeCodeCoverage:
    def test_all_base_codes_defined(self):
        for code in ("G", "G*", "F", "D", "A", "A*"):
            assert code in IAPP_SCOPE_TO_ACTORS, f"Missing scope code: {code}"

    def test_each_scope_has_actors(self):
        for code, actors in IAPP_SCOPE_TO_ACTORS.items():
            assert len(actors) > 0, f"Empty actor set for scope code: {code}"
