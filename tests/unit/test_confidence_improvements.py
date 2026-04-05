"""Tests for Phase 3 confidence scoring improvements.

Covers:
  - IMPROVEMENT-3: Span length penalty (broad span detection)
  - IMPROVEMENT-4: Section reference quality sub-signal
  - ANALYSIS-4 verification: Orrick tokenizer Unicode safety (no-code check)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.confidence import (
    _score_section_reference,
    compute_confidence,
)
from src.schemas.extraction import ObligationPayload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orrick(score: float = 0.30):
    sim = MagicMock()
    sim.has_orrick_data = True
    sim.combined_score = score
    sim.matched_tokens = ["deployer", "system"]
    return sim


def _base_confidence(**kwargs):
    """Minimal compute_confidence call that passes the Orrick gate."""
    defaults = dict(
        schema_valid=True,
        evidence_spans=[{"field_name": "subject", "text": "x", "verified": True}],
        extraction_payload={"subject": "developer", "modality": "shall", "action": "comply"},
        schema_class=ObligationPayload,
        orrick_similarity=_make_orrick(),
    )
    defaults.update(kwargs)
    return compute_confidence(**defaults)


# ---------------------------------------------------------------------------
# IMPROVEMENT-3: Span length penalty
# ---------------------------------------------------------------------------

class TestSpanLengthPenalty:
    PASSAGE = "A" * 100  # 100-char passage for predictable ratios

    def test_no_penalty_when_no_passage_text(self):
        """Without passage_text, evidence score is unpenalised."""
        result = _base_confidence(
            evidence_spans=[{"field_name": "subject", "text": "A" * 80, "verified": True}],
        )
        assert not result.broad_spans
        assert result.evidence_grounding == pytest.approx(1.0, abs=0.01)

    def test_no_penalty_short_span(self):
        """A span covering 30% of the passage is fine — no penalty."""
        result = _base_confidence(
            evidence_spans=[{"field_name": "s", "text": "A" * 30, "verified": True}],
            passage_text=self.PASSAGE,
        )
        assert not result.broad_spans
        assert result.evidence_grounding == pytest.approx(1.0, abs=0.01)

    def test_moderate_penalty_span_over_50_pct(self):
        """A span covering 60% of the passage triggers a 20% penalty."""
        result = _base_confidence(
            evidence_spans=[{"field_name": "s", "text": "A" * 60, "verified": True}],
            passage_text=self.PASSAGE,
        )
        assert result.broad_spans
        # evidence_score was 1.0, penalised to 0.80
        assert result.evidence_grounding == pytest.approx(0.80, abs=0.01)

    def test_heavy_penalty_span_over_75_pct(self):
        """A span covering 80% of the passage triggers a 40% penalty."""
        result = _base_confidence(
            evidence_spans=[{"field_name": "s", "text": "A" * 80, "verified": True}],
            passage_text=self.PASSAGE,
        )
        assert result.broad_spans
        # evidence_score was 1.0, penalised to 0.60
        assert result.evidence_grounding == pytest.approx(0.60, abs=0.01)

    def test_exact_50_pct_boundary_no_penalty(self):
        """A span at exactly 50% is NOT penalised (threshold is strictly >0.50)."""
        result = _base_confidence(
            evidence_spans=[{"field_name": "s", "text": "A" * 50, "verified": True}],
            passage_text=self.PASSAGE,
        )
        assert not result.broad_spans

    def test_exact_75_pct_boundary_moderate_penalty(self):
        """A span at exactly 75% gets moderate (not heavy) penalty."""
        result = _base_confidence(
            evidence_spans=[{"field_name": "s", "text": "A" * 75, "verified": True}],
            passage_text=self.PASSAGE,
        )
        assert result.broad_spans
        # 75% is not > 0.75, so moderate penalty applies (0.80x)
        assert result.evidence_grounding == pytest.approx(0.80, abs=0.01)

    def test_unverified_span_not_measured(self):
        """An unverified span does not trigger the penalty — only verified spans count."""
        result = _base_confidence(
            evidence_spans=[{"field_name": "s", "text": "A" * 90, "verified": False}],
            passage_text=self.PASSAGE,
        )
        assert not result.broad_spans

    def test_broad_spans_flag_in_breakdown(self):
        """broad_spans flag is True in breakdown when penalty applied."""
        result = _base_confidence(
            evidence_spans=[{"field_name": "s", "text": "A" * 70, "verified": True}],
            passage_text=self.PASSAGE,
        )
        assert result.broad_spans is True

    def test_penalty_applied_only_to_max_span(self):
        """Penalty is based on the longest verified span, not average."""
        result = _base_confidence(
            evidence_spans=[
                {"field_name": "a", "text": "A" * 10, "verified": True},
                {"field_name": "b", "text": "A" * 80, "verified": True},
            ],
            passage_text=self.PASSAGE,
        )
        assert result.broad_spans  # Longest span (80%) triggers penalty

    def test_empty_passage_no_penalty(self):
        """Empty passage string skips the penalty check gracefully."""
        result = _base_confidence(
            evidence_spans=[{"field_name": "s", "text": "anything", "verified": True}],
            passage_text="",
        )
        assert not result.broad_spans

    def test_orrick_gated_breakdown_includes_broad_spans(self):
        """broad_spans is also set on Tier D (Orrick-gated) breakdowns."""
        result = compute_confidence(
            schema_valid=True,
            evidence_spans=[{"field_name": "s", "text": "A" * 80, "verified": True}],
            extraction_payload={"subject": "dev", "modality": "shall", "action": "comply"},
            schema_class=ObligationPayload,
            orrick_similarity=None,  # No Orrick → Tier D gate
            passage_text="A" * 100,
        )
        assert result.tier == "D"
        assert result.orrick_gated
        assert result.broad_spans


# ---------------------------------------------------------------------------
# IMPROVEMENT-4: Section reference quality
# ---------------------------------------------------------------------------

class TestScoreSectionReference:
    def test_none_returns_zero(self):
        assert _score_section_reference(None) == 0.0

    def test_empty_string_returns_zero(self):
        assert _score_section_reference("") == 0.0
        assert _score_section_reference("   ") == 0.0

    def test_highly_specific_section_symbol_with_subsection(self):
        assert _score_section_reference("§ 6-1-1702(3)(a)") == pytest.approx(1.0)

    def test_highly_specific_nested_parens(self):
        assert _score_section_reference("(3)(a)") == pytest.approx(1.0)

    def test_medium_section_symbol_no_subsection(self):
        assert _score_section_reference("§ 14") == pytest.approx(0.6)

    def test_medium_numeric_citation(self):
        assert _score_section_reference("6.1.2") == pytest.approx(0.6)

    def test_low_generic_section_label(self):
        assert _score_section_reference("Section 5") == pytest.approx(0.3)

    def test_low_article_label(self):
        assert _score_section_reference("Article III") == pytest.approx(0.3)

    def test_low_part_label(self):
        assert _score_section_reference("Part IV") == pytest.approx(0.3)

    def test_unrecognised_pattern_minimal_credit(self):
        assert _score_section_reference("Appendix A") == pytest.approx(0.2)

    def test_completeness_blends_section_ref_quality(self):
        """Section ref quality is blended into completeness at 20% weight."""
        # Obligation with NO section_reference: section_ref_quality = 0.0
        no_ref = _base_confidence(
            extraction_payload={
                "subject": "developer",
                "modality": "shall",
                "action": "comply",
            },
        )

        # Same obligation with a highly specific section ref
        with_ref = _base_confidence(
            extraction_payload={
                "subject": "developer",
                "modality": "shall",
                "action": "comply",
                "section_reference": "§ 6-1-1702(3)(a)",
            },
        )

        # Completeness with a specific section ref should be higher
        assert with_ref.completeness > no_ref.completeness
        assert with_ref.section_ref_quality == pytest.approx(1.0)
        assert no_ref.section_ref_quality == pytest.approx(0.0)

    def test_section_ref_quality_in_breakdown(self):
        """section_ref_quality is reported in the breakdown."""
        result = _base_confidence(
            extraction_payload={
                "subject": "developer",
                "modality": "shall",
                "action": "comply",
                "section_reference": "§ 14",
            },
        )
        assert result.section_ref_quality == pytest.approx(0.6)
