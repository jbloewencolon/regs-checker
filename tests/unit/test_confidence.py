"""Unit tests for the confidence scoring model.

Key design: The Orrick gate forces Tier D when no Orrick data is present.
Tests that check scoring behavior above Tier D must supply mock Orrick data
via orrick_similarity, otherwise the gate clamps the result.
"""

from unittest.mock import MagicMock

from src.core.confidence import (
    ConfidenceBreakdown,
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
