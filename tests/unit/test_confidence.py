"""Unit tests for the confidence scoring model."""

from src.core.confidence import compute_confidence
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
