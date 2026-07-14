"""Tests for QA-9a's restatement-scoped relevance engine (plan Phase 2).

NOT WIRED INTO SYNC — this module is the ready-to-wire engine pending
RPR/product ratification of the in-scope rules (see
docs/qa8_qa9_phased_plan.md Phase 2, gate 4, and the module docstring in
src/core/restatement_scope.py). These tests validate the engine itself
against real committed sources: SB 926's Penal Code § 647 (only the (j)(4)
"computer-generated image" clause should read as in-scope; the loitering/
prostitution/window-peeping subdivisions around it should not), and AB
2355's § 84504.2 (whose font/placement formatting rules must stay in scope
despite containing no AI keyword themselves — the over-filtering trap the
plan's fact 0.3 simulation caught before any code was written).
"""

from __future__ import annotations

from src.core.restatement_scope import (
    RESTATEMENT_SIZE_THRESHOLD,
    assess_extraction_scope,
    find_added_section_numbers,
    find_evidence_offset,
    is_restatement_passage,
    parse_second_level_subdivisions,
    parse_top_level_subdivisions,
)


class TestIsRestatementPassage:
    def test_parallel_version_group_always_triggers(self):
        assert is_restatement_passage("short text", parallel_version_group="penal code:647") is True

    def test_oversized_single_version_amendment_triggers(self):
        text = "Section 647 of the Penal Code is amended to read: " + "x" * RESTATEMENT_SIZE_THRESHOLD
        assert is_restatement_passage(text, parallel_version_group=None) is True

    def test_small_single_version_amendment_does_not_trigger(self):
        text = "Section 84504 of the Government Code is amended to read: 84504. (a) short."
        assert is_restatement_passage(text, parallel_version_group=None) is False

    def test_non_amendment_passage_never_triggers(self):
        text = "x" * (RESTATEMENT_SIZE_THRESHOLD + 100)
        assert is_restatement_passage(text, parallel_version_group=None) is False

    def test_whole_ai_act_never_trips_trigger(self):
        # A wholly-AI bill (no California re-enactment header at all) must
        # never be scoped by this engine, regardless of length.
        text = (
            "This act establishes requirements for developers of automated "
            "decision systems. " * 500
        )
        assert is_restatement_passage(text, parallel_version_group=None) is False


class TestFindAddedSectionNumbers:
    def test_finds_added_section(self):
        text = "Section 84514 is added to the Government Code, immediately following Section 84513, to read:"
        assert find_added_section_numbers(text) == {"84514"}

    def test_amended_section_not_counted_as_added(self):
        text = "Section 647 of the Penal Code is amended to read:"
        assert find_added_section_numbers(text) == set()

    def test_multiple_added_sections(self):
        text = (
            "Section 100 is added to the Civil Code to read: ... "
            "Section 200 is added to the Penal Code to read: ..."
        )
        assert find_added_section_numbers(text) == {"100", "200"}


class TestSequentialSubdivisionParsing:
    def test_prose_cross_reference_before_real_marker_ignored(self):
        # "of subdivision (b)" appears before the real (a) — must not be
        # picked up as a structural boundary.
        text = (
            "Except as provided in paragraph (5) of subdivision (b), every "
            "person is guilty. (a) An individual who solicits. (b) An "
            "individual who agrees."
        )
        spans = parse_top_level_subdivisions(text)
        assert [s.label for s in spans] == ["a", "b"]
        assert text[spans[0].start : spans[0].start + 3] == "(a)"

    def test_gap_in_sequence_stops_matching(self):
        # (a) then (c) with no (b) anywhere in the text: (c) is never
        # accepted, since (b) never arrives to satisfy the expected
        # sequence first — this is deliberately conservative (an
        # irregular document just gets coarser granularity, never a wrong
        # grouping).
        text = "(a) First clause. Some reference to (c) elsewhere in this act."
        spans = parse_top_level_subdivisions(text)
        assert [s.label for s in spans] == ["a"]

    def test_second_level_numeric_within_top_level(self):
        text = "(j) (1) First paragraph. (2) Second paragraph. (3) Third paragraph."
        spans = parse_second_level_subdivisions(text)
        assert [s.label for s in spans] == ["1", "2", "3"]

    def test_second_level_alpha_fallback(self):
        text = "(l) (A) First. (B) Second."
        spans = parse_second_level_subdivisions(text)
        assert [s.label for s in spans] == ["A", "B"]

    def test_no_second_level_returns_empty(self):
        text = "(a) A single flat clause with no nested numbering."
        assert parse_second_level_subdivisions(text) == []


class TestFindEvidenceOffset:
    def test_exact_substring_located(self):
        text = "Section 647 of the Penal Code is amended to read: 647. Except as provided."
        offset = find_evidence_offset("Except as provided", text)
        assert text[offset : offset + len("Except as provided")] == "Except as provided"

    def test_punctuation_and_casing_drift_tolerated(self):
        text = "The disclosure area shall have a solid white background, and shall be boxed."
        offset = find_evidence_offset("disclosure area shall have a solid white background", text)
        assert offset is not None

    def test_too_short_evidence_not_located(self):
        text = "Section 647 of the Penal Code is amended to read."
        assert find_evidence_offset("Section 647", text) is None

    def test_absent_evidence_returns_none(self):
        text = "Section 647 of the Penal Code is amended to read: 647. Except as provided."
        assert find_evidence_offset("this text does not appear anywhere in the passage", text) is None


class TestAssessExtractionScopeRealCorpus:
    """SB 926 Penal Code § 647 and AB 2355 Government Code § 84504.2 —
    the representative (last, per QA-8) version of each."""

    @classmethod
    def setup_class(cls):
        from src.ingestion.parser import _segment_text

        sb926 = open("output/law_texts/TMP-CA-AMENDMENTOFCAL.txt").read()
        cls.sb926_text = _segment_text(sb926)[7][1]

        ab2355_raw = open("output/law_texts/TMP-CA-AMENDMENTTOTHE.txt").read()
        ab2355_passages = _segment_text(ab2355_raw)
        cls.ab2355_84504_2_text = ab2355_passages[3][1]
        cls.ab2355_added_sections = find_added_section_numbers(ab2355_raw)

    def test_sb926_j4_computer_generated_image_in_scope(self):
        evidence = (
            "A person who intentionally creates and distributes or causes "
            "to be distributed any photo realistic image, digital image, "
            "electronic image, computer image, computer-generated image"
        )
        result = assess_extraction_scope(evidence, self.sb926_text)
        assert result["in_scope"] is True
        assert result["subdivision"] == "(j)(4)"

    def test_sb926_j1_window_peeping_out_of_scope(self):
        evidence = (
            "A person who looks through a hole or opening, into, or "
            "otherwise views, by means of any instrumentality"
        )
        result = assess_extraction_scope(evidence, self.sb926_text)
        assert result["in_scope"] is False
        assert result["subdivision"] == "(j)(1)"

    def test_sb926_loitering_subdivision_a_out_of_scope(self):
        evidence = (
            "An individual who solicits anyone to engage in or who engages "
            "in lewd or dissolute conduct in a public place"
        )
        result = assess_extraction_scope(evidence, self.sb926_text)
        assert result["in_scope"] is False
        assert result["subdivision"] == "(a)"

    def test_sb926_prostitution_definition_subdivision_i_out_of_scope(self):
        evidence = (
            "Who, while loitering, prowling, or wandering upon the private "
            "property of another, at any time, peeks in the door or"
        )
        result = assess_extraction_scope(evidence, self.sb926_text)
        assert result["in_scope"] is False
        assert result["subdivision"] == "(i)"

    def test_ab2355_formatting_rule_in_scope_via_added_section_reference(self):
        # (a)(1): the white-background rule. No AI keyword anywhere in it —
        # this is the exact over-filtering trap fact 0.3 identified. It
        # reads in-scope only because its parent (a) cites the bill's own
        # added § 84514.
        evidence = (
            "The disclosure area shall have a solid white background and "
            "shall be in a printed or drawn box on the bottom of at least "
            "one page"
        )
        result = assess_extraction_scope(
            evidence, self.ab2355_84504_2_text,
            added_section_numbers=self.ab2355_added_sections,
        )
        assert result["in_scope"] is True
        assert "84514" in result["reason"]

    def test_ab2355_type_size_rule_also_in_scope(self):
        evidence = "The text shall be in standard Arial Regular type with a type size of at least 10-point."
        result = assess_extraction_scope(
            evidence, self.ab2355_84504_2_text,
            added_section_numbers=self.ab2355_added_sections,
        )
        assert result["in_scope"] is True

    def test_ab2355_without_added_section_context_stays_visible_by_default(self):
        # Sanity check on the safe-default: if the caller doesn't pass the
        # added-section set at all, an unrecognized formatting rule with no
        # keyword falls through to the conservative "no signal" outcome —
        # false-negative-safe wiring (an omitted added_section_numbers arg)
        # never accidentally over-hides; it just skips rule (b) entirely.
        evidence = "The text shall be in standard Arial Regular type with a type size of at least 10-point."
        result = assess_extraction_scope(evidence, self.ab2355_84504_2_text)
        assert result["in_scope"] is False
        assert result["reason"] == "no_ai_domain_signal"


class TestAssessExtractionScopeFallbacks:
    def test_shared_preamble_before_first_subdivision_in_scope(self):
        text = "647. Except as provided in subdivision (l), every person is guilty. (a) First clause."
        evidence = "Except as provided in subdivision"
        result = assess_extraction_scope(evidence, text)
        assert result["in_scope"] is True
        assert result["reason"] == "shared_preamble"

    def test_no_subdivision_structure_in_scope(self):
        text = "This act shall take effect on January 1, 2026, and shall apply to all contracts entered thereafter."
        evidence = "shall take effect on January 1, 2026"
        result = assess_extraction_scope(evidence, text)
        assert result["in_scope"] is True
        assert result["reason"] == "no_subdivision_structure"

    def test_unlocatable_evidence_defaults_to_in_scope(self):
        text = "(a) Some clause. (b) Another clause."
        result = assess_extraction_scope("text that never appears in the passage at all", text)
        assert result["in_scope"] is True
        assert result["reason"] == "evidence_not_located"

    def test_adjacency_rule_keeps_lead_in_with_in_scope_sibling(self):
        # A minimal (a)(b) restatement-shaped text — (b) plays the role of
        # SB 926's (j): a lead-in sentence followed by numbered clauses,
        # only one of which is AI-relevant.
        text = (
            "(a) An unrelated clause about something else entirely. "
            "(b) A person violates this subdivision under any of the "
            "following circumstances: (1) Peeks through a window. "
            "(2) Creates a computer-generated image of another person."
        )
        evidence = "A person violates this subdivision under any of the following circumstances"
        result = assess_extraction_scope(evidence, text)
        assert result["in_scope"] is True
        assert result["reason"] == "adjacent_to_in_scope_sibling"

    def test_adjacency_rule_does_not_sweep_in_unrelated_sibling(self):
        text = (
            "(a) An unrelated clause about something else entirely. "
            "(b) A person violates this subdivision under any of the "
            "following circumstances: (1) Peeks through a window. "
            "(2) Creates a computer-generated image of another person."
        )
        evidence = "Peeks through a window"
        result = assess_extraction_scope(evidence, text)
        assert result["in_scope"] is False
        assert result["subdivision"] == "(b)(1)"
