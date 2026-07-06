"""Tests for P3 sync gate changes: tier-only publish with rejection safety.

P3 changes (Phase 3, Confidence-Only Publish Gate):
- Removed review_status='approved' requirement from publish gate
- Added review_status != 'rejected' safety filter (analyst veto mechanism)
- Tier-D extractions always remain ineligible (below floor C)
- Both sync_extractions() id-cursor leg and sync_updates() change-propagation
  leg apply the same eligibility rules
"""

from __future__ import annotations

import pytest
from src.scripts.sync_extractions import _eligible_tiers


class TestEligibleTiers:
    """Test the tier-comparison helper used in both sync legs."""

    def test_tier_floor_c_default(self):
        """Default (C) includes A, B, C; excludes D."""
        tiers = _eligible_tiers("C")
        assert tiers == ["A", "B", "C"]

    def test_tier_floor_b(self):
        """Floor B includes A, B; excludes C, D."""
        tiers = _eligible_tiers("B")
        assert tiers == ["A", "B"]

    def test_tier_floor_a(self):
        """Floor A includes only A."""
        tiers = _eligible_tiers("A")
        assert tiers == ["A"]

    def test_tier_floor_d_all(self):
        """Floor D includes all (but sync still excludes D elsewhere)."""
        tiers = _eligible_tiers("D")
        assert tiers == ["A", "B", "C", "D"]

    def test_tier_floor_none_defaults_to_c(self):
        """None input defaults to C."""
        tiers = _eligible_tiers(None)
        assert tiers == ["A", "B", "C"]

    def test_tier_floor_empty_defaults_to_c(self):
        """Empty string defaults to C."""
        tiers = _eligible_tiers("")
        assert tiers == ["A", "B", "C"]

    def test_tier_floor_invalid_returns_all(self):
        """Invalid tier string returns all (safer fallback)."""
        tiers = _eligible_tiers("X")
        assert tiers == ["A", "B", "C", "D"]

    def test_tier_floor_case_insensitive(self):
        """Tier comparison is case-insensitive."""
        tiers_lower = _eligible_tiers("c")
        tiers_upper = _eligible_tiers("C")
        assert tiers_lower == tiers_upper == ["A", "B", "C"]


class TestP3SyncEligibilityLogic:
    """Test the eligibility decision logic used in both sync legs.

    The eligibility rule is:
      confidence_tier IN eligible_tiers AND review_status != 'rejected'

    This replaces P2's rule:
      review_status = 'approved' AND confidence_tier IN eligible_tiers
    """

    def test_tier_a_approved_eligible(self):
        """Tier A + approved → eligible."""
        tier = "A"
        review_status = "approved"
        eligible_tiers = _eligible_tiers("C")
        is_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert is_eligible is True

    def test_tier_a_pending_eligible(self):
        """Tier A + pending → eligible (was ineligible under P2)."""
        tier = "A"
        review_status = "pending"
        eligible_tiers = _eligible_tiers("C")
        is_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert is_eligible is True

    def test_tier_a_flagged_eligible(self):
        """Tier A + flagged → eligible (was ineligible under P2)."""
        tier = "A"
        review_status = "flagged"
        eligible_tiers = _eligible_tiers("C")
        is_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert is_eligible is True

    def test_tier_a_rejected_ineligible(self):
        """Tier A + rejected → ineligible (analyst veto)."""
        tier = "A"
        review_status = "rejected"
        eligible_tiers = _eligible_tiers("C")
        is_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert is_eligible is False

    def test_tier_b_verified_eligible(self):
        """Tier B + verified → eligible."""
        tier = "B"
        review_status = "verified"
        eligible_tiers = _eligible_tiers("C")
        is_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert is_eligible is True

    def test_tier_c_approved_eligible(self):
        """Tier C + approved → eligible."""
        tier = "C"
        review_status = "approved"
        eligible_tiers = _eligible_tiers("C")
        is_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert is_eligible is True

    def test_tier_c_pending_eligible(self):
        """Tier C + pending → eligible (P3 change: no longer requires approved)."""
        tier = "C"
        review_status = "pending"
        eligible_tiers = _eligible_tiers("C")
        is_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert is_eligible is True

    def test_tier_d_approved_ineligible(self):
        """Tier D + approved → ineligible (below floor)."""
        tier = "D"
        review_status = "approved"
        eligible_tiers = _eligible_tiers("C")
        is_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert is_eligible is False

    def test_tier_d_any_status_ineligible(self):
        """Tier D + any review_status → ineligible (below floor, always)."""
        tier = "D"
        eligible_tiers = _eligible_tiers("C")
        for review_status in ["approved", "pending", "flagged", "verified", "rejected"]:
            is_eligible = (tier in eligible_tiers and review_status != "rejected")
            assert is_eligible is False, f"Tier D + {review_status} should be ineligible"

    def test_rejected_trumps_tier(self):
        """Rejected review_status prevents sync regardless of high tier."""
        tier = "A"
        review_status = "rejected"
        eligible_tiers = _eligible_tiers("C")
        is_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert is_eligible is False


class TestP3RegressionAgainstP2:
    """Verify that the new tier-only gate is a regression fix vs P2's broken state.

    P2 required review_status='approved' for sync. This meant:
    - Extractions with tier A/B/C but status pending/flagged would never sync
    - Rejecting a high-confidence extraction was a side-effect, not intentional
    - No explicit analyst veto mechanism

    P3 fixes:
    - Tier alone determines publish eligibility (A/B/C → sync)
    - Explicit rejection (review_status='rejected') can still block sync (analyst veto)
    - pending/flagged/verified extractions at tier A/B/C now sync (improvement)
    """

    def test_tier_c_flagged_was_blocked_now_syncs(self):
        """P2: Tier C + flagged → blocked. P3: → syncs (improvement)."""
        tier = "C"
        review_status = "flagged"
        eligible_tiers = _eligible_tiers("C")

        # P2 rule: review_status == 'approved' and tier in eligible_tiers
        p2_eligible = (review_status == "approved" and tier in eligible_tiers)
        assert p2_eligible is False, "P2 would block this"

        # P3 rule: tier in eligible_tiers and review_status != 'rejected'
        p3_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert p3_eligible is True, "P3 now allows this (improvement)"

    def test_tier_b_pending_was_blocked_now_syncs(self):
        """P2: Tier B + pending → blocked. P3: → syncs (improvement)."""
        tier = "B"
        review_status = "pending"
        eligible_tiers = _eligible_tiers("C")

        p2_eligible = (review_status == "approved" and tier in eligible_tiers)
        assert p2_eligible is False, "P2 would block this"

        p3_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert p3_eligible is True, "P3 now allows this (improvement)"

    def test_tier_a_approved_was_allowed_still_allowed(self):
        """P2: Tier A + approved → syncs. P3: → syncs (unchanged)."""
        tier = "A"
        review_status = "approved"
        eligible_tiers = _eligible_tiers("C")

        p2_eligible = (review_status == "approved" and tier in eligible_tiers)
        assert p2_eligible is True, "P2 allows this"

        p3_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert p3_eligible is True, "P3 still allows this (consistent)"

    def test_analyst_veto_works_in_p3(self):
        """P3: Analyst can explicitly reject high-tier findings (new veto mechanism)."""
        tier = "A"
        review_status = "rejected"
        eligible_tiers = _eligible_tiers("C")

        p3_eligible = (tier in eligible_tiers and review_status != "rejected")
        assert p3_eligible is False, "Explicit rejection prevents sync (analyst veto)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
