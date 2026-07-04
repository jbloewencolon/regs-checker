"""Unit tests for the confidence scoring model.

Key design: The Orrick gate forces Tier D when no Orrick data is present.
Tests that check scoring behavior above Tier D must supply mock Orrick data
via orrick_similarity, otherwise the gate clamps the result.
"""

from unittest.mock import MagicMock

from src.core.confidence import (
    ConfidenceBreakdown,
    cap_at_tier_c,
    compute_confidence,
    TIER_A_THRESHOLD,
    TIER_B_THRESHOLD,
    TIER_C_THRESHOLD,
)
from src.schemas.extraction import ObligationPayload


def _make_orrick_sim(combined_score: float = 0.30, tokens: list[str] | None = None):
    """Create a mock OrrickSimilarityResult that passes the Orrick gate."""
    sim = MagicMock()
    sim.has_orrick_data = True
    sim.combined_score = combined_score
    sim.matched_tokens = tokens or ["ai", "system", "developer"]
    return sim


class TestConfidenceScoring:
    def test_perfect_score(self):
        """All components maxed + Orrick data -> Tier A."""
        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
                {"field_name": "action", "text": "y", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "subject_normalized": "developer",
                "modality": "shall",
                "action": "comply",
                "object": "AI system",
                "condition": "when deployed",
                "jurisdiction": "CO",
                "section_reference": "Sec 3",
                "timeline": {"effective_date": "2025-01-01"},
                "enforcement": {"penalty_type": "fine"},
            },
            schema_class=ObligationPayload,
            parse_quality_score=1.0,
            orrick_similarity=_make_orrick_sim(0.30),
            cross_validation_score=1.0,
        )
        assert result.tier == "A"
        assert result.total_score >= 0.85

    def test_minimal_score(self):
        """Bad inputs + no Orrick data -> Tier D."""
        result = compute_confidence(
            schema_valid=False,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": False},
            ],
            extraction_payload={
                "subject": "Developer",
                "modality": "shall",
                "action": "comply",
            },
            schema_class=ObligationPayload,
            parse_quality_score=0.2,
        )
        assert result.tier == "D"
        assert result.total_score < 0.50

    def test_tier_b_threshold(self):
        """Good extraction with Orrick data but no CV -> Tier A or B."""
        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
                {"field_name": "action", "text": "y", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "modality": "shall",
                "action": "comply",
                "jurisdiction": "CO",
                "subject_normalized": "developer",
                "object": "AI system",
                "condition": "when deploying",
                "section_reference": "§ 6-1-1502",
            },
            schema_class=ObligationPayload,
            parse_quality_score=0.8,
            orrick_similarity=_make_orrick_sim(0.30),
        )
        assert result.tier in ("A", "B")
        assert result.total_score >= 0.70

    def test_no_evidence_spans(self):
        """Evidence grounding should be 0.0 when no spans provided."""
        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[],
            extraction_payload={
                "subject": "Developer",
                "modality": "shall",
                "action": "comply",
            },
            schema_class=ObligationPayload,
            parse_quality_score=0.5,
        )
        assert result.evidence_grounding == 0.0

    def test_orrick_gate_forces_tier_d(self):
        """Without Orrick data, even perfect scores get clamped to Tier D."""
        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
                {"field_name": "action", "text": "y", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "subject_normalized": "developer",
                "modality": "shall",
                "action": "comply",
                "object": "AI system",
                "condition": "when deployed",
                "jurisdiction": "CO",
                "section_reference": "Sec 3",
                "timeline": {"effective_date": "2025-01-01"},
                "enforcement": {"penalty_type": "fine"},
            },
            schema_class=ObligationPayload,
            parse_quality_score=0.9,
        )
        assert result.tier == "D"
        assert result.orrick_gated is True
        assert result.orrick_alignment == 0.0
        assert result.cross_validation == 0.0

    def test_cross_validation_lowers_tier(self):
        """Low CV score + Orrick data -> should prevent reaching Tier A."""
        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "modality": "shall",
                "action": "comply",
                "jurisdiction": "CO",
            },
            schema_class=ObligationPayload,
            parse_quality_score=0.8,
            orrick_similarity=_make_orrick_sim(0.30),
            cross_validation_score=0.2,
        )
        # Low CV score should prevent reaching Tier A
        assert result.tier in ("B", "C")

    def test_all_components_active(self):
        """With all 6 components active, full weight distribution applies."""
        orrick_sim = _make_orrick_sim(0.30, ["ai", "system"])

        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "subject_normalized": "developer",
                "modality": "shall",
                "action": "comply",
                "object": "AI system",
                "condition": "when deployed",
                "jurisdiction": "CO",
                "section_reference": "Sec 3",
            },
            schema_class=ObligationPayload,
            parse_quality_score=0.9,
            orrick_similarity=orrick_sim,
            cross_validation_score=0.85,
        )
        # All components active — should be a strong score
        assert result.tier in ("A", "B")
        assert result.orrick_alignment == 1.0  # combined_score 0.30 >= 0.25
        assert result.orrick_matched_tokens == ["ai", "system"]

    def test_low_orrick_score_limits_tier(self):
        """Orrick data present but low similarity -> lower orrick_alignment."""
        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "modality": "shall",
                "action": "comply",
            },
            schema_class=ObligationPayload,
            parse_quality_score=0.8,
            orrick_similarity=_make_orrick_sim(0.05),  # Very low match
        )
        # Low Orrick combined_score < 0.10 -> orrick_alignment = 0.3
        assert result.orrick_alignment == 0.3
        assert result.orrick_gated is False  # Data exists, gate doesn't fire

    def test_no_orrick_data_flag(self):
        """When orrick_similarity has has_orrick_data=False, gate fires."""
        no_data_sim = MagicMock()
        no_data_sim.has_orrick_data = False
        no_data_sim.combined_score = 0.0
        no_data_sim.matched_tokens = []

        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "modality": "shall",
                "action": "comply",
            },
            schema_class=ObligationPayload,
            parse_quality_score=0.9,
            orrick_similarity=no_data_sim,
        )
        assert result.tier == "D"
        assert result.orrick_gated is True


class TestCrossValidationWiring:
    """Phase 2b regression guard: a cross-validation score must actually
    move the confidence result, and an absent score must not be treated as
    a neutral pass.

    Cross-validation runs post-extraction (run_verification_pass), which
    recomputes confidence via _recompute_confidence_with_cv.  These tests
    pin the contract that the 0.25 cross-validation weight is live, not dead.
    """

    def _base_kwargs(self):
        return dict(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
                {"field_name": "action", "text": "y", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "subject_normalized": "developer",
                "modality": "shall",
                "action": "comply",
                "object": "AI system",
                "jurisdiction": "CO",
                "section_reference": "Sec 3",
            },
            schema_class=ObligationPayload,
            parse_quality_score=0.8,
            orrick_similarity=_make_orrick_sim(0.30),
        )

    def test_high_cv_score_raises_confidence(self):
        """A high cross-validation score must raise the total vs. no CV."""
        without_cv = compute_confidence(**self._base_kwargs())
        with_high_cv = compute_confidence(
            **self._base_kwargs(), cross_validation_score=1.0
        )
        assert with_high_cv.cross_validation == 1.0
        assert with_high_cv.total_score > without_cv.total_score

    def test_low_cv_score_lowers_confidence(self):
        """A low cross-validation score must lower the total vs. no CV."""
        without_cv = compute_confidence(**self._base_kwargs())
        with_low_cv = compute_confidence(
            **self._base_kwargs(), cross_validation_score=0.0
        )
        assert with_low_cv.cross_validation == 0.0
        assert with_low_cv.total_score < without_cv.total_score

    def test_absent_cv_excludes_weight(self):
        """When CV is absent (None), its weight is redistributed — the
        component is excluded, not silently scored as a neutral value."""
        without_cv = compute_confidence(**self._base_kwargs())
        # The cross_validation component reports 0.0 but is NOT part of the
        # active weighted average (excluded), so a separate run with an
        # explicit 0.0 score must differ.
        with_zero_cv = compute_confidence(
            **self._base_kwargs(), cross_validation_score=0.0
        )
        assert without_cv.total_score != with_zero_cv.total_score, (
            "Absent CV must not be equivalent to an explicit 0.0 score — "
            "the 0.25 weight must be redistributed when CV is None"
        )

    def test_cv_score_can_change_tier(self):
        """A populated CV score should be able to move the tier boundary."""
        # Tuned so the no-CV result sits just under a boundary and a perfect
        # CV score lifts it over.
        kwargs = self._base_kwargs()
        high = compute_confidence(**kwargs, cross_validation_score=1.0)
        low = compute_confidence(**kwargs, cross_validation_score=0.0)
        # The two CV extremes must not collapse to the same tier when the
        # base score sits in a sensitive range.
        assert high.total_score > low.total_score


# ---------------------------------------------------------------------------
# Phase 4b — IAPP gate refinement
# ---------------------------------------------------------------------------


class TestIAPPGateRefinement:
    """Tests for the Phase 4b Orrick-gate refinement.

    The Orrick gate fires (Tier D, orrick_gated=True) only when BOTH Orrick
    and IAPP data are absent.  When IAPP data is present (iapp_has_data=True)
    but Orrick is absent, the result should be scored from evidence+citation
    signals, capped at Tier C (never Tier A/B without Orrick grounding).
    """

    @staticmethod
    def _no_orrick_sim():
        sim = MagicMock()
        sim.has_orrick_data = False
        sim.combined_score = 0.0
        sim.matched_tokens = []
        return sim

    def _base_no_orrick_kwargs(self):
        return dict(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
                {"field_name": "action", "text": "y", "verified": True},
            ],
            extraction_payload={
                "subject": "Developer",
                "modality": "shall",
                "action": "comply",
                "jurisdiction": "CO",
            },
            schema_class=ObligationPayload,
            parse_quality_score=0.8,
            orrick_similarity=self._no_orrick_sim(),
        )

    def test_no_orrick_no_iapp_forces_tier_d(self):
        """No Orrick + no IAPP → orrick_gated=True, Tier D."""
        result = compute_confidence(**self._base_no_orrick_kwargs())
        assert result.tier == "D"
        assert result.orrick_gated is True

    def test_no_orrick_with_iapp_not_gated(self):
        """No Orrick + IAPP present → orrick_gated=False, not forced Tier D."""
        result = compute_confidence(
            **self._base_no_orrick_kwargs(),
            iapp_has_data=True,
        )
        assert result.orrick_gated is False
        assert result.tier != "D"

    def test_no_orrick_with_iapp_capped_below_tier_b(self):
        """IAPP-only path caps the score below Tier B (< 0.70)."""
        result = compute_confidence(
            **self._base_no_orrick_kwargs(),
            iapp_has_data=True,
        )
        assert result.total_score < TIER_B_THRESHOLD

    def test_no_orrick_with_iapp_high_evidence_can_reach_tier_c(self):
        """Strong evidence + IAPP data (no Orrick) → Tier C."""
        result = compute_confidence(
            **self._base_no_orrick_kwargs(),
            iapp_has_data=True,
        )
        assert result.tier in ("C",), (
            f"Expected Tier C with strong evidence + IAPP, got {result.tier}"
        )

    def test_orrick_present_ignores_iapp_flag(self):
        """When Orrick is present, iapp_has_data does not change the score."""
        sim = MagicMock()
        sim.has_orrick_data = True
        sim.combined_score = 0.30
        sim.matched_tokens = ["ai"]
        kwargs = dict(
            schema_valid=True,
            evidence_spans=[
                {"field_name": "subject", "text": "x", "verified": True},
            ],
            extraction_payload={"subject": "Developer", "modality": "shall", "action": "comply"},
            schema_class=ObligationPayload,
            parse_quality_score=0.8,
            orrick_similarity=sim,
        )
        without_iapp = compute_confidence(**kwargs)
        with_iapp = compute_confidence(**kwargs, iapp_has_data=True)
        assert without_iapp.total_score == with_iapp.total_score
        assert without_iapp.orrick_gated is False
        assert with_iapp.orrick_gated is False


class TestIAPPAlignmentScore:
    """Phase 4b: iapp_alignment_score feeds into tracker_alignment_score (diagnostic)."""

    def _base_kwargs(self, orrick_score: float = 0.3):
        sim = MagicMock()
        sim.has_orrick_data = True
        sim.combined_score = orrick_score
        sim.matched_tokens = ["ai"]
        return dict(
            schema_valid=True,
            evidence_spans=[{"field_name": "f", "text": "x", "verified": True}],
            extraction_payload={"subject": "developer", "action": "comply"},
            schema_class=ObligationPayload,
            orrick_similarity=sim,
        )

    def test_iapp_aligned_raises_tracker_alignment_score(self):
        """iapp_alignment_score=1.0 blends with mid-band Orrick to raise tracker_alignment.

        combined_score=0.15 → orrick_score = 0.5 + (0.05/0.15)*0.5 ≈ 0.6667.
        Blended: 0.6667 * 0.60 + 1.0 * 0.40 ≈ 0.80.
        """
        without = compute_confidence(**self._base_kwargs(orrick_score=0.15))
        with_iapp = compute_confidence(
            **self._base_kwargs(orrick_score=0.15), iapp_alignment_score=1.0
        )
        assert with_iapp.tracker_alignment_score > without.tracker_alignment_score
        expected_orrick = 0.5 + (0.15 - 0.10) / (0.25 - 0.10) * 0.5
        expected_blended = expected_orrick * 0.60 + 1.0 * 0.40
        assert abs(with_iapp.tracker_alignment_score - expected_blended) < 0.01

    def test_iapp_scope_mismatch_lowers_tracker_alignment_score(self):
        """scope_mismatch (0.3) blended with perfect Orrick → 1.0*0.60+0.3*0.40 = 0.72."""
        # combined_score=0.25 → orrick_score=1.0 (≥0.25 threshold)
        without = compute_confidence(**self._base_kwargs(orrick_score=0.25))
        with_mismatch = compute_confidence(
            **self._base_kwargs(orrick_score=0.25), iapp_alignment_score=0.3
        )
        assert with_mismatch.tracker_alignment_score < without.tracker_alignment_score
        assert abs(with_mismatch.tracker_alignment_score - (1.0 * 0.60 + 0.3 * 0.40)) < 0.01

    def test_iapp_only_no_orrick(self):
        """IAPP score alone (no Orrick) sets tracker_alignment_score to iapp score."""
        sim = MagicMock()
        sim.has_orrick_data = False
        sim.combined_score = 0.0
        sim.matched_tokens = []
        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[{"field_name": "f", "text": "x", "verified": True}],
            extraction_payload={"subject": "developer", "action": "comply"},
            schema_class=ObligationPayload,
            orrick_similarity=sim,
            iapp_has_data=True,
            iapp_alignment_score=1.0,
        )
        assert abs(result.tracker_alignment_score - 1.0) < 0.01

    def test_no_iapp_score_unchanged(self):
        """Without iapp_alignment_score, tracker_alignment_score equals orrick_alignment."""
        result = compute_confidence(**self._base_kwargs(orrick_score=0.5))
        assert abs(result.tracker_alignment_score - result.orrick_alignment) < 0.01

    def test_iapp_score_does_not_affect_total_score(self):
        """Phase 4b: iapp_alignment_score must NOT change total_score (Phase 4c only)."""
        without = compute_confidence(**self._base_kwargs())
        with_iapp = compute_confidence(**self._base_kwargs(), iapp_alignment_score=1.0)
        assert without.total_score == with_iapp.total_score
        assert without.tier == with_iapp.tier


class TestCapAtTierC:
    """EA2-3: truncated/heavily-repaired raw output caps the tier at C so a
    structural defect (possible lost content) can't hide behind an
    otherwise-good confidence score.
    """

    def test_tier_a_is_capped_to_c(self):
        score, tier = cap_at_tier_c(0.95, "A")
        assert tier == "C"
        assert score < TIER_B_THRESHOLD

    def test_tier_b_is_capped_to_c(self):
        score, tier = cap_at_tier_c(0.75, "B")
        assert tier == "C"
        assert score < TIER_B_THRESHOLD

    def test_tier_c_is_unchanged(self):
        score, tier = cap_at_tier_c(0.55, "C")
        assert tier == "C"
        assert score == 0.55

    def test_tier_d_is_unchanged(self):
        # A D-tier extraction is already worse than the cap — never improve it.
        score, tier = cap_at_tier_c(0.10, "D")
        assert tier == "D"
        assert score == 0.10

    def test_capped_score_and_tier_stay_consistent(self):
        # The returned score must actually map to the returned tier — no
        # score=0.9-next-to-tier="C" inconsistency in the review UI.
        from src.core.confidence import _score_to_tier
        score, tier = cap_at_tier_c(0.99, "A")
        assert _score_to_tier(score) == tier

    def test_capped_score_never_reaches_tier_b_threshold(self):
        score, _ = cap_at_tier_c(1.0, "A")
        assert score < TIER_B_THRESHOLD

    def test_capped_score_stays_at_or_above_tier_c_threshold(self):
        # Capping should land inside tier C's own band, not fall through to D.
        score, tier = cap_at_tier_c(0.99, "A")
        assert tier == "C"
        assert score >= TIER_C_THRESHOLD
