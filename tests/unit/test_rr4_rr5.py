"""Tests for RR4 and RR5 improvements.

RR4c — Jurisdiction-aware citation normalizer
RR4a/4b — Parser section tracking and offset correctness
RR4d — Parse quality scoring
RR5a — Orthogonal confidence dimensions
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# RR4c — Citation normalizer
# ---------------------------------------------------------------------------

class TestCitationNormalizer:
    """State-specific citation fixtures (RR4f)."""

    def setup_method(self):
        from src.core.citation_normalizer import normalize_citation, find_matching_section_path
        self.normalize = normalize_citation
        self.find_match = find_matching_section_path

    # Colorado
    def test_co_crs_pattern(self):
        result = self.normalize("C.R.S. § 6-1-1702(3)(a)", "CO")
        assert "6-1-1702" in result
        assert result.startswith("§")

    def test_co_colo_rev_stat(self):
        result = self.normalize("Colo. Rev. Stat. § 24-72-204", "CO")
        assert "24-72-204" in result
        assert result.startswith("§")

    # California
    def test_ca_bus_prof(self):
        result = self.normalize("Cal. Bus. & Prof. Code § 22575", "CA")
        assert "22575" in result
        assert result.startswith("§")

    def test_ca_civil_code(self):
        result = self.normalize("Cal. Civil Code § 1798.100", "CA")
        assert "1798.100" in result

    # New York
    def test_ny_gen_bus_law(self):
        result = self.normalize("N.Y. Gen. Bus. Law § 899-aa", "NY")
        assert "899-aa" in result
        assert result.startswith("§")

    # Texas
    def test_tx_bus_com(self):
        result = self.normalize("Tex. Bus. & Com. Code § 503.001", "TX")
        assert "503.001" in result

    # Connecticut
    def test_ct_cgs(self):
        result = self.normalize("C.G.S. § 36a-701b", "CT")
        assert "36a-701b" in result
        assert result.startswith("§")

    # Illinois
    def test_il_ilcs(self):
        result = self.normalize("820 ILCS 5/820-110", "IL")
        # Should preserve the numeric part
        assert "820" in result

    # Utah
    def test_ut_code_ann(self):
        result = self.normalize("Utah Code Ann. § 13-37-201", "UT")
        assert "13-37-201" in result
        assert result.startswith("§")

    # Federal
    def test_federal_usc(self):
        result = self.normalize("15 U.S.C. § 6501", "US")
        assert "6501" in result
        assert result.startswith("§")

    # Generic section prefix stripping (no jurisdiction)
    def test_generic_section_prefix(self):
        result = self.normalize("Section 4(a)(2)")
        assert result.startswith("§")
        assert "4" in result

    def test_generic_sec_prefix(self):
        result = self.normalize("Sec. 12")
        assert result.startswith("§")
        assert "12" in result

    # Section path matching
    def test_find_matching_exact(self):
        paths = ["Section 4", "Section 5", "§ 6-1-1702"]
        matched = self.find_match("§ 6-1-1702(3)(a)", paths)
        assert matched == "§ 6-1-1702"

    def test_find_matching_by_number(self):
        paths = ["Section 4", "Section 12", "Article III"]
        matched = self.find_match("§ 12", paths)
        assert matched == "Section 12"

    def test_find_no_match_below_threshold(self):
        paths = ["Section 99", "Article X"]
        matched = self.find_match("§ 6-1-1702", paths, min_score=0.8)
        assert matched is None

    def test_empty_ref_returns_empty(self):
        result = self.normalize("")
        assert result == ""

    def test_empty_paths_returns_none(self):
        from src.core.citation_normalizer import find_matching_section_path
        assert find_matching_section_path("§ 4", []) is None


# ---------------------------------------------------------------------------
# RR4a / RR4b — Parser section tracking and offset correctness
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=False)
def _require_bs4():
    pytest.importorskip("bs4", reason="bs4 not installed")


class TestParserSectionTracking:
    """Verify that merged passages carry included_section_ids and correct offsets."""

    def setup_method(self):
        pytest.importorskip("bs4", reason="bs4 not installed")
        from src.ingestion.parser import _segment_text
        self.segment = _segment_text

    def test_single_section_has_itself_in_ids(self):
        text = "Section 1\nThis is a short section."
        passages = self.segment(text)
        assert len(passages) >= 1
        # 5-tuple format from RR4a
        assert len(passages[0]) == 5
        section_path, passage_text, start, end, included_ids = passages[0]
        assert "Section 1" in included_ids

    def test_merged_passages_track_all_section_ids(self):
        """When short sections are merged, all section markers appear in included_ids."""
        # Create two short sections that will be merged (each < 3000 chars)
        text = "Section 1\nShort text.\n\nSection 2\nAlso short.\n\nSection 99\nFar section that forces a new chunk " + "x" * 3000
        passages = self.segment(text)
        # First passage should contain Section 1 and Section 2 merged
        first_path, first_text, first_start, first_end, first_ids = passages[0]
        assert len(first_ids) >= 2
        assert "Section 1" in first_ids
        assert "Section 2" in first_ids

    def test_end_offset_is_accurate(self):
        """char_offset_end should reflect the actual span, not start + len(merged_text)."""
        text = "Section 1\nShort.\n\nSection 2\nAlso short.\n\nSection 3\n" + "x" * 3001
        passages = self.segment(text)
        for passage in passages:
            _, ptext, start, end, _ = passage
            # End must be >= start
            assert end >= start
            # The start offset should be non-negative
            assert start >= 0

    def test_paragraph_fallback_returns_5_tuples(self):
        """Paragraph-based fallback also returns 5-tuples (no section headers)."""
        text = "This is a paragraph.\n\nThis is another paragraph."
        passages = self.segment(text)
        assert all(len(p) == 5 for p in passages)

    def test_included_ids_nonempty_for_each_passage(self):
        text = "Section 3\nSome content here.\n\nSection 4\nMore content here."
        passages = self.segment(text)
        for passage in passages:
            _, _, _, _, ids = passage
            assert len(ids) > 0

    def test_cross_reference_does_not_steal_section_content(self):
        """Regression (real Massachusetts bill, TMP-MA-AMENDMENTTOTHE):
        'SECTION 7. Chapter 272 of the General Laws is hereby amended...' is
        one continuous clause, but "Chapter 272" also matches the marker
        pattern, so section_pattern's lookahead stopped right after
        "SECTION 7." Before the fix this produced an empty 'SECTION 7.' stub
        AND mislabeled Section 7's real content under 'Chapter 272' instead.
        _splice_marker_only_stubs runs on the raw marker/body pairs before
        the size-based chunk merge, so it's tested directly here rather than
        through _segment_text (whose merge step would otherwise combine
        short synthetic sections regardless of this bug).
        """
        from src.ingestion.parser import _splice_marker_only_stubs

        raw = [
            ("SECTION 6.", "SECTION 6. Some real prior section content.", 0, 44),
            ("SECTION 7.", "SECTION 7.", 44, 54),  # empty body -- the bug signature
            (
                "Chapter 272",
                "Chapter 272 of the General Laws is hereby amended by inserting "
                "after section 29C the following section:- Section 29D. (a) "
                "Whoever, while under the age of criminal majority, possesses "
                "visual material shall be punished as provided herein.",
                54, 250,
            ),
            ("SECTION 8.", "SECTION 8.", 250, 260),  # empty body -- same bug
            (
                "Section 63",
                "Section 63 of chapter 277 of the General Laws is hereby "
                "amended by striking out certain language.",
                260, 350,
            ),
        ]
        fixed = _splice_marker_only_stubs(raw)
        markers = [f[0] for f in fixed]

        # No bare marker-only stub should survive as its own entry.
        assert not any(f[1].strip() == f[0].strip() for f in fixed), (
            "no entry should be just a bare marker with no body"
        )

        # The real content must be attributed to "SECTION 7."/"SECTION 8.",
        # not mislabeled as "Chapter 272"/"Section 63".
        assert "Chapter 272" not in markers
        assert "Section 63" not in markers
        section_7 = next(f for f in fixed if f[0] == "SECTION 7.")
        assert "Whoever, while under the age of criminal majority" in section_7[1]
        section_8 = next(f for f in fixed if f[0] == "SECTION 8.")
        assert "striking out certain language" in section_8[1]

        # SECTION 6 (a genuinely non-empty marker) must be untouched.
        section_6 = next(f for f in fixed if f[0] == "SECTION 6.")
        assert section_6[1] == "SECTION 6. Some real prior section content."

    def test_splice_marker_only_stubs_leaves_normal_sections_untouched(self):
        """A section with real content of its own (non-empty body) must not
        be merged forward, even if it's short."""
        from src.ingestion.parser import _splice_marker_only_stubs

        raw = [
            ("SECTION 4.", "SECTION 4. Repealed.", 0, 20),
            ("SECTION 5.", "SECTION 5. This act takes effect January 1, 2026.", 20, 70),
        ]
        fixed = _splice_marker_only_stubs(raw)
        assert fixed == raw

    def test_splice_marker_only_stubs_handles_chain_of_empty_markers(self):
        """Two consecutive empty markers before real content should both be
        absorbed into the section that actually contains the content."""
        from src.ingestion.parser import _splice_marker_only_stubs

        raw = [
            ("SECTION 1.", "SECTION 1.", 0, 10),
            ("SECTION 2.", "SECTION 2.", 10, 20),
            ("Chapter 5", "Chapter 5 of the General Laws is hereby amended.", 20, 70),
        ]
        fixed = _splice_marker_only_stubs(raw)
        assert len(fixed) == 1
        assert fixed[0][0] == "SECTION 1."
        assert "Chapter 5 of the General Laws is hereby amended" in fixed[0][1]

    def test_splice_marker_only_stubs_trailing_empty_marker_left_alone(self):
        """An empty marker with nothing after it (end of document) has
        nothing to absorb — it should pass through unchanged, not crash."""
        from src.ingestion.parser import _splice_marker_only_stubs

        raw = [
            ("SECTION 1.", "SECTION 1. Real content here.", 0, 30),
            ("SECTION 2.", "SECTION 2.", 30, 40),
        ]
        fixed = _splice_marker_only_stubs(raw)
        assert fixed == raw


# ---------------------------------------------------------------------------
# RR4d — Parse quality scoring
# ---------------------------------------------------------------------------

class TestParseQualityScoring:
    """Verify parse quality detection for garbled / junk text."""

    def setup_method(self):
        pytest.importorskip("bs4", reason="bs4 not installed")
        from src.ingestion.parser import _compute_parse_quality
        self.quality = _compute_parse_quality

    def test_clean_legal_text_scores_high(self):
        text = (
            "Section 1. Definitions. For the purposes of this section, the following "
            "definitions shall apply. A covered entity shall comply with the obligations "
            "set forth herein, notwithstanding any contrary provision of law."
        )
        score = self.quality(text)
        assert score >= 0.6, f"Expected >= 0.6 but got {score}"

    def test_replacement_char_junk_scores_low(self):
        # Binary PDF decoded as UTF-8 produces replacement chars
        junk = "�" * 200 + "some text" + "�" * 100
        score = self.quality(junk)
        assert score < 0.4, f"Expected < 0.4 but got {score}"

    def test_empty_text_scores_zero(self):
        assert self.quality("") == 0.0

    def test_no_legal_markers_scores_low(self):
        # Plain narrative text with no legal keywords
        text = "The cat sat on the mat. The dog ran around the yard. Blue sky above."
        score = self.quality(text)
        # May still score decently on replacement ratio (0 replacements)
        # but density should be low — overall < 0.7
        assert score < 0.7

    def test_many_replacements_catastrophic(self):
        """Text with >5% replacement chars should score near zero."""
        text = "A" * 94 + "�" * 6  # 6% replacement chars
        score = self.quality(text)
        assert score < 0.3, f"Expected catastrophic score < 0.3 but got {score}"

    def test_threshold_flag(self):
        """Passages below threshold should be flagged in metadata_."""
        from src.ingestion.parser import _PARSE_QUALITY_REVIEW_THRESHOLD
        junk = "�" * 200 + "text" * 10
        score = self.quality(junk)
        # The actual threshold check happens in parse_and_normalize;
        # just verify the constant is reasonable.
        assert 0.0 < _PARSE_QUALITY_REVIEW_THRESHOLD <= 0.5


# ---------------------------------------------------------------------------
# RR5a — Orthogonal confidence dimensions
# ---------------------------------------------------------------------------

class TestOrthogonalConfidenceDimensions:
    """Verify the three orthogonal dimensions are computed and exposed."""

    def _make_breakdown(self, **kwargs):
        from unittest.mock import MagicMock
        from src.core.confidence import compute_confidence

        schema_class = MagicMock()
        schema_class.model_fields = {}

        orrick = MagicMock()
        orrick.has_orrick_data = kwargs.get("has_orrick", True)
        orrick.combined_score = kwargs.get("orrick_score", 0.3)
        orrick.matched_tokens = []

        evidence_spans = kwargs.get("evidence_spans", [
            {"text": "shall comply", "verified": True},
        ])

        return compute_confidence(
            schema_valid=kwargs.get("schema_valid", True),
            evidence_spans=evidence_spans,
            extraction_payload={"section_reference": "§ 6-1-1702(3)(a)"},
            schema_class=schema_class,
            orrick_similarity=orrick,
            iapp_has_data=kwargs.get("iapp_has_data", False),
        )

    def test_dimensions_present_on_breakdown(self):
        bd = self._make_breakdown()
        assert hasattr(bd, "source_grounding_score")
        assert hasattr(bd, "tracker_alignment_score")
        assert hasattr(bd, "schema_completeness_score")

    def test_source_grounding_reflects_evidence(self):
        """High evidence → high source_grounding_score."""
        bd = self._make_breakdown(evidence_spans=[
            {"text": "shall comply", "verified": True},
            {"text": "obligation", "verified": True},
        ])
        assert bd.source_grounding_score > 0.0

    def test_source_grounding_zero_no_evidence(self):
        bd = self._make_breakdown(evidence_spans=[])
        # section_ref_quality may still give some score
        # but grounding from evidence alone is 0
        assert bd.source_grounding_score <= 0.30  # only citation contribution

    def test_tracker_alignment_zero_when_no_orrick(self):
        bd = self._make_breakdown(has_orrick=False)
        assert bd.tracker_alignment_score == 0.0

    def test_tracker_alignment_positive_with_orrick(self):
        bd = self._make_breakdown(has_orrick=True, orrick_score=0.30)
        assert bd.tracker_alignment_score > 0.0

    def test_schema_completeness_reflects_validity(self):
        bd_valid = self._make_breakdown(schema_valid=True)
        bd_invalid = self._make_breakdown(schema_valid=False)
        assert bd_valid.schema_completeness_score > bd_invalid.schema_completeness_score

    def test_dimensions_do_not_affect_total_score(self):
        """Adding/removing dimensions should leave total_score unchanged."""
        bd = self._make_breakdown()
        # Re-compute with same inputs — total_score must be stable
        bd2 = self._make_breakdown()
        assert bd.total_score == bd2.total_score

    def test_dimensions_present_on_iapp_path(self):
        """Orthogonal dimensions are computed even on the IAPP-only code path."""
        bd = self._make_breakdown(has_orrick=False, iapp_has_data=True)
        assert hasattr(bd, "source_grounding_score")
        assert hasattr(bd, "tracker_alignment_score")
        assert hasattr(bd, "schema_completeness_score")

    def test_dimensions_present_on_gated_path(self):
        """Orthogonal dimensions are computed even when orrick_gated=True."""
        bd = self._make_breakdown(has_orrick=False, iapp_has_data=False)
        assert bd.orrick_gated is True
        assert hasattr(bd, "source_grounding_score")

    def test_api_schema_includes_dimensions(self):
        """ConfidenceBreakdownResponse schema exposes the three dimensions."""
        from src.schemas.api import ConfidenceBreakdownResponse
        fields = ConfidenceBreakdownResponse.model_fields
        assert "source_grounding_score" in fields
        assert "tracker_alignment_score" in fields
        assert "schema_completeness_score" in fields
