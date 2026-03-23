"""Unit tests for the confidence scoring model."""

from src.core.confidence import (
    ConfidenceBreakdown,
    compute_confidence,
    TIER_A_THRESHOLD,
    TIER_B_THRESHOLD,
    TIER_C_THRESHOLD,
)
from src.schemas.extraction import ObligationPayload


class TestConfidenceScoring:
    def test_perfect_score(self):
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
            cross_validation_score=1.0,
        )
        assert result.tier == "A"
        assert result.total_score >= 0.85

    def test_minimal_score(self):
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
        )
        assert result.tier in ("A", "B")
        assert result.total_score >= 0.70

    def test_no_evidence_spans(self):
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

    def test_weight_redistribution_without_optional_components(self):
        """Without Orrick or cross-validation, weights redistribute to core
        components so a well-extracted item can reach Tier A/B."""
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
        # Without optional components, this should reach A on core merits
        assert result.tier == "A", (
            f"Expected Tier A, got {result.tier} (score={result.total_score})"
        )
        # Optional component scores should be 0.0 when excluded
        assert result.orrick_alignment == 0.0
        assert result.cross_validation == 0.0

    def test_cross_validation_lowers_tier(self):
        """A low cross-validation score should pull the tier down."""
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
            cross_validation_score=0.2,
        )
        # Low CV score should prevent reaching Tier A
        assert result.tier in ("B", "C")

    def test_all_components_active(self):
        """With all 6 components active, full weight distribution applies."""
        from unittest.mock import MagicMock

        orrick_sim = MagicMock()
        orrick_sim.has_orrick_data = True
        orrick_sim.combined_score = 0.30
        orrick_sim.matched_tokens = ["ai", "system"]

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
