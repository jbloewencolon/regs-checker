"""Unit tests for Phase 5b concept-grouping deterministic core.

The DB-driven grouping pass (group_concepts_for_dv / run_concept_grouping) is
exercised in tests/integration against a live Postgres.  These unit tests cover
the pure, deterministic helpers that decide how fragments are keyed and scored.
"""

from __future__ import annotations

import pytest

from src.core.concept_grouping import (
    _actor_family,
    _classify_obligation_family,
    _dedup_join,
    _is_iso_date,
    _tier_for_score,
    reload_alias_cache,
)
from src.core.vocab_loader import reload_cache


@pytest.fixture(autouse=True)
def reset_caches():
    reload_cache()
    reload_alias_cache()
    yield
    reload_cache()
    reload_alias_cache()


# ---------------------------------------------------------------------------
# _classify_obligation_family — deterministic alias-grounded classification
# ---------------------------------------------------------------------------


class TestClassifyObligationFamily:
    def test_disclosure_action(self):
        assert _classify_obligation_family(
            "the deployer shall provide disclosure to the consumer"
        ) == "disclosure_to_user"

    def test_impact_assessment_action(self):
        assert _classify_obligation_family(
            "complete an impact_assessment before deployment"
        ) == "impact_assessment"

    def test_registration_action(self):
        assert _classify_obligation_family(
            "must complete registration with the state registry"
        ) == "registration"

    def test_unmatched_action_is_general(self):
        assert _classify_obligation_family(
            "the entity shall do something otherwise unspecified"
        ) == "obligation_general"

    def test_empty_action_is_general(self):
        assert _classify_obligation_family("") == "obligation_general"

    def test_returns_canonical_code(self):
        # Whatever it returns must be a real obligation_family code or the
        # generic bucket — never a REVIEW_ placeholder or raw alias.
        from src.core.vocab_loader import get_canonical_codes
        codes = set(get_canonical_codes("obligation_family")) | {"obligation_general"}
        result = _classify_obligation_family("must maintain record_keeping logs")
        assert result in codes

    def test_longest_alias_wins(self):
        # "record_keeping" (longer) should win over a hypothetical short token.
        assert _classify_obligation_family(
            "obligations include record_keeping of all decisions"
        ) == "record_keeping"


# ---------------------------------------------------------------------------
# _tier_for_score — local tier thresholds
# ---------------------------------------------------------------------------


class TestTierForScore:
    def test_tier_a(self):
        assert _tier_for_score(0.90) == "A"
        assert _tier_for_score(0.85) == "A"

    def test_tier_b(self):
        assert _tier_for_score(0.84) == "B"
        assert _tier_for_score(0.70) == "B"

    def test_tier_c(self):
        assert _tier_for_score(0.69) == "C"
        assert _tier_for_score(0.50) == "C"

    def test_tier_d(self):
        assert _tier_for_score(0.49) == "D"
        assert _tier_for_score(0.0) == "D"


# ---------------------------------------------------------------------------
# _actor_family — actor normalization with empty handling
# ---------------------------------------------------------------------------


class TestActorFamily:
    def test_known_actor_normalizes(self):
        assert _actor_family("deployer") == "deployer"

    def test_empty_returns_none(self):
        assert _actor_family("") is None
        assert _actor_family(None) is None

    def test_fallback_used_when_primary_empty(self):
        assert _actor_family(None, "developer") == "developer"

    def test_unknown_actor_falls_back_to_regulated_entity(self):
        # vocab_loader's fallback for the actor dimension is regulated_entity
        assert _actor_family("totally_unknown_actor_xyz") == "regulated_entity"


# ---------------------------------------------------------------------------
# _dedup_join — order-preserving dedup with limit
# ---------------------------------------------------------------------------


class TestDedupJoin:
    def test_dedups_preserving_order(self):
        assert _dedup_join(["a", "b", "a", "c"]) == "a | b | c"

    def test_respects_limit(self):
        assert _dedup_join(["a", "b", "c", "d", "e", "f"], limit=3) == "a | b | c"


# ---------------------------------------------------------------------------
# _is_iso_date — EA6-5: deadline computations must exclude unparsed dates
# ---------------------------------------------------------------------------


class TestIsIsoDate:
    def test_genuine_iso_date_is_true(self):
        assert _is_iso_date("2026-01-01") is True

    def test_iso_date_with_surrounding_whitespace_is_true(self):
        assert _is_iso_date("  2026-01-01  ") is True

    def test_free_text_passthrough_is_false(self):
        # This is exactly what TimelineInfo stores when normalize_date()
        # fails — the raw model text, unchanged.
        assert _is_iso_date("the first day of the next legislative session") is False

    def test_year_only_is_false(self):
        # normalize_date() itself would have expanded a bare year to
        # YYYY-01-01; a bare year reaching here means it was never parsed.
        assert _is_iso_date("2026") is False

    def test_none_is_false(self):
        assert _is_iso_date(None) is False

    def test_empty_string_is_false(self):
        assert _is_iso_date("") is False

    def test_non_string_is_false(self):
        assert _is_iso_date(2026) is False
        assert _is_iso_date(["2026-01-01"]) is False

    def test_malformed_iso_like_string_is_false(self):
        assert _is_iso_date("2026-1-1") is False
        assert _is_iso_date("26-01-01") is False

    def test_strips_whitespace(self):
        assert _dedup_join(["  a  ", "a"]) == "a"

    def test_skips_empty(self):
        assert _dedup_join(["", "  ", "a"]) == "a"

    def test_empty_list(self):
        assert _dedup_join([]) == ""
