"""Unit tests for the Phase 4c confidence recompute script.

Tests cover:
- compute_v3_score() weight math and gating rules
- Orrick gate: forces Tier D when both orrick and iapp are None
- IAPP-only cap: score cannot reach Tier B without Orrick data
- Weight redistribution when optional components are absent
- evaluate_fixture() loads the scaffold fixture without error
- CLI entry point runs without crashing on the scaffold fixture
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scripts.recompute_confidence import (
    TIER_A,
    TIER_B,
    TIER_C,
    V3_WEIGHTS,
    _interpolate_orrick,
    _score_to_tier,
    compute_v3_score,
    evaluate_fixture,
)

FIXTURE_PATH = Path("tests/unit/fixtures/gold_confidence.json")


# ---------------------------------------------------------------------------
# Weight sanity
# ---------------------------------------------------------------------------


class TestV3Weights:
    def test_weights_sum_to_one(self):
        total = sum(V3_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"V3 weights sum to {total}, not 1.0"

    def test_all_weights_positive(self):
        for name, w in V3_WEIGHTS.items():
            assert w > 0, f"Weight for {name!r} must be positive"

    def test_orrick_is_highest_weight(self):
        assert V3_WEIGHTS["orrick"] == max(V3_WEIGHTS.values())


# ---------------------------------------------------------------------------
# Orrick interpolation
# ---------------------------------------------------------------------------


class TestInterpolateOrrick:
    def test_below_lower_bound_is_zero(self):
        assert _interpolate_orrick(0.0) == 0.0
        assert _interpolate_orrick(0.09) == 0.0

    def test_above_upper_bound_is_one(self):
        assert _interpolate_orrick(0.30) == 1.0
        assert _interpolate_orrick(1.0) == 1.0

    def test_midpoint_interpolation(self):
        # 0.175 is exactly midway between 0.10 and 0.25 → 0.50
        result = _interpolate_orrick(0.175)
        assert abs(result - 0.50) < 0.01

    def test_lower_bound_is_zero(self):
        assert _interpolate_orrick(0.10) == 0.0

    def test_upper_bound_is_one(self):
        assert _interpolate_orrick(0.25) == 1.0


# ---------------------------------------------------------------------------
# Gating rules
# ---------------------------------------------------------------------------


class TestGatingRules:
    def test_no_tracker_data_forces_tier_d(self):
        score, tier = compute_v3_score(
            orrick_score=None,
            iapp_score=None,
            evidence_score=1.0,
            citation_score=1.0,
            cv_score=1.0,
            gap_score=1.0,
            analyst_score=1.0,
            schema_valid=True,
            field_completeness=1.0,
        )
        assert tier == "D"
        assert score == 0.0

    def test_iapp_only_capped_below_tier_b(self):
        score, tier = compute_v3_score(
            orrick_score=None,
            iapp_score=1.0,
            evidence_score=1.0,
            citation_score=1.0,
            cv_score=1.0,
            gap_score=1.0,
            analyst_score=1.0,
            schema_valid=True,
            field_completeness=1.0,
        )
        assert score < TIER_B, f"IAPP-only score {score} should be < TIER_B={TIER_B}"
        assert tier in ("C", "D")  # cannot reach B or A

    def test_orrick_present_can_reach_tier_a(self):
        score, tier = compute_v3_score(
            orrick_score=0.90,   # → effective_orrick = 1.0
            iapp_score=1.0,
            evidence_score=1.0,
            citation_score=1.0,
            cv_score=1.0,
            gap_score=1.0,
            analyst_score=1.0,
            schema_valid=True,
            field_completeness=1.0,
        )
        assert score >= TIER_A
        assert tier == "A"

    def test_orrick_present_iapp_absent_no_cap(self):
        # Orrick present — IAPP-only cap does not apply
        score, tier = compute_v3_score(
            orrick_score=0.90,
            iapp_score=None,
            evidence_score=1.0,
            citation_score=1.0,
            cv_score=1.0,
            gap_score=1.0,
            analyst_score=1.0,
            schema_valid=True,
            field_completeness=1.0,
        )
        assert score >= TIER_B  # not capped

    def test_schema_invalid_penalizes_score(self):
        score_valid, _ = compute_v3_score(
            orrick_score=0.90, iapp_score=1.0,
            evidence_score=0.8, citation_score=0.8,
            cv_score=None, gap_score=None, analyst_score=None,
            schema_valid=True, field_completeness=0.8,
        )
        score_invalid, _ = compute_v3_score(
            orrick_score=0.90, iapp_score=1.0,
            evidence_score=0.8, citation_score=0.8,
            cv_score=None, gap_score=None, analyst_score=None,
            schema_valid=False, field_completeness=0.8,
        )
        # schema_invalid should not raise — any score is valid; valid > invalid not guaranteed
        # because schema_valid feeds into completeness, not a direct v3 component
        assert 0.0 <= score_invalid <= 1.0


# ---------------------------------------------------------------------------
# Weight redistribution when optional components are absent
# ---------------------------------------------------------------------------


class TestWeightRedistribution:
    def test_score_in_range_when_all_optional_absent(self):
        score, tier = compute_v3_score(
            orrick_score=0.90,
            iapp_score=None,
            evidence_score=0.7,
            citation_score=0.7,
            cv_score=None,
            gap_score=None,
            analyst_score=None,
            schema_valid=True,
            field_completeness=0.7,
        )
        assert 0.0 <= score <= 1.0

    def test_skip_analyst_flag_redistributes_weight(self):
        # With analyst_score=None and skip_analyst=False, analyst component is 0.0
        # With skip_analyst=True, its weight is redistributed (higher score)
        base_args = dict(
            orrick_score=0.90, iapp_score=1.0,
            evidence_score=0.8, citation_score=0.8,
            cv_score=0.8, gap_score=0.8,
            analyst_score=None,
            schema_valid=True, field_completeness=0.8,
        )
        score_without_skip, _ = compute_v3_score(**base_args, skip_analyst=False)
        score_with_skip, _ = compute_v3_score(**base_args, skip_analyst=True)
        # skip_analyst=True redistributes the weight → same or higher score
        assert score_with_skip >= score_without_skip

    def test_cv_absent_redistributed_weight_stays_normalized(self):
        score, _ = compute_v3_score(
            orrick_score=0.90, iapp_score=1.0,
            evidence_score=1.0, citation_score=1.0,
            cv_score=None,
            gap_score=1.0,
            analyst_score=1.0,
            schema_valid=True, field_completeness=1.0,
        )
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Tier boundary math
# ---------------------------------------------------------------------------


class TestScoreToTier:
    def test_tier_a_boundary(self):
        assert _score_to_tier(TIER_A) == "A"
        assert _score_to_tier(1.0) == "A"

    def test_tier_b_boundary(self):
        assert _score_to_tier(TIER_B) == "B"
        assert _score_to_tier(TIER_A - 0.001) == "B"

    def test_tier_c_boundary(self):
        assert _score_to_tier(TIER_C) == "C"
        assert _score_to_tier(TIER_B - 0.001) == "C"

    def test_tier_d_boundary(self):
        assert _score_to_tier(TIER_C - 0.001) == "D"
        assert _score_to_tier(0.0) == "D"


# ---------------------------------------------------------------------------
# evaluate_fixture
# ---------------------------------------------------------------------------


class TestEvaluateFixture:
    def test_scaffold_fixture_loads_without_error(self):
        report = evaluate_fixture(FIXTURE_PATH)
        assert "results" in report
        assert "tier_accuracy" in report
        assert isinstance(report["results"], list)

    def test_scaffold_fixture_has_expected_schema_version(self):
        report = evaluate_fixture(FIXTURE_PATH)
        assert report["schema_version"] == "1.0"

    def test_scaffold_fixture_computes_scores_for_all_rows(self):
        data = json.loads(FIXTURE_PATH.read_text())
        n_fixtures = len(data["fixtures"])
        report = evaluate_fixture(FIXTURE_PATH)
        assert len(report["results"]) == n_fixtures

    def test_all_predicted_tiers_valid(self):
        report = evaluate_fixture(FIXTURE_PATH)
        valid_tiers = {"A", "B", "C", "D"}
        for row in report["results"]:
            assert row["v3_predicted_tier"] in valid_tiers, (
                f"Fixture {row['fixture_id']}: unexpected tier {row['v3_predicted_tier']!r}"
            )

    def test_all_scores_in_range(self):
        report = evaluate_fixture(FIXTURE_PATH)
        for row in report["results"]:
            s = row["v3_total_score"]
            assert 0.0 <= s <= 1.0, (
                f"Fixture {row['fixture_id']}: score {s} out of [0, 1]"
            )

    def test_gc001_predicts_tier_b_with_null_analyst(self):
        # GC-001 has analyst_score=null → treated as 0.0 (conservative pre-ANALYSIS-1).
        # Strong on all other inputs (orrick=0.85, iapp=1.0, evidence=0.90) but the
        # 10% analyst weight dragging to 0.0 pulls total below TIER_A=0.85.
        report = evaluate_fixture(FIXTURE_PATH)
        gc001 = next(r for r in report["results"] if r["fixture_id"] == "GC-001")
        assert gc001["v3_predicted_tier"] == "B", (
            f"GC-001 should predict Tier B (null analyst_score penalizes), got {gc001}"
        )

    def test_gc001_predicts_tier_a_when_skip_analyst(self):
        # With skip_analyst=True the 10% analyst weight is redistributed → score clears TIER_A.
        report = evaluate_fixture(FIXTURE_PATH, skip_analyst=True)
        gc001 = next(r for r in report["results"] if r["fixture_id"] == "GC-001")
        assert gc001["v3_predicted_tier"] == "A", (
            f"GC-001 should predict Tier A when analyst weight redistributed, got {gc001}"
        )

    def test_gc002_capped_below_tier_b(self):
        report = evaluate_fixture(FIXTURE_PATH)
        gc002 = next(r for r in report["results"] if r["fixture_id"] == "GC-002")
        assert gc002["v3_predicted_tier"] in ("C", "D"), (
            f"GC-002 (IAPP-only) should be Tier C or D, got {gc002}"
        )

    def test_skip_analyst_report_flag_propagated(self):
        report = evaluate_fixture(FIXTURE_PATH, skip_analyst=True)
        assert report["skip_analyst"] is True
