"""Tests for QA-8: parallel-version restatement collapse.

California re-enacts an entire code section on every amendment (Cal. Const.
art. IV § 9). When several pending bills touch the same section, the bill
text carries one full restatement per enactment-order contingency — SB 926
restates Penal Code § 647 eight times (2^3 contingencies of AB 1874 / AB 1962
/ SB 1414). `_group_parallel_versions` detects these groups deterministically
from the amending-header text so only the last (most-merged) version feeds
the agent battery; every version contains this bill's own changes regardless
of which is kept, so the choice is lossless. Fixtures are drawn from the real
committed sources (SB 926, AB 2355, SB 11) plus AR HB1877 as a negative
control — its amendment headers use a distinct Arkansas Code shape that must
not match at all.
"""

from __future__ import annotations

from src.ingestion.parser import (
    _detect_amendment_target,
    _group_parallel_versions,
    _segment_text,
)


class TestDetectAmendmentTarget:
    def test_plain_penal_code_header(self):
        text = "Section 647 of the Penal Code is amended to read: 647. Except..."
        assert _detect_amendment_target(text) == ("Penal Code", "647")

    def test_header_with_amended_by_qualifier(self):
        text = (
            "Section 84504.2 of the Government Code, as amended by Section 5 "
            "of Chapter 777 of the Statutes of 2018, is amended to read: "
            "84504.2. (a) An advertisement..."
        )
        assert _detect_amendment_target(text) == ("Government Code", "84504.2")

    def test_different_qualifier_same_target(self):
        # AB 2355's two versions differ only in which chapter/year they cite —
        # both must resolve to the same (code, section) group key.
        text = (
            "Section 84504.2 of the Government Code, as amended by Section 12 "
            "of Chapter 887 of the Statutes of 2022, is amended to read: "
            "84504.2. (a) An advertisement..."
        )
        assert _detect_amendment_target(text) == ("Government Code", "84504.2")

    def test_multi_word_code_name(self):
        text = "Section 22650 of the Welfare and Institutions Code is amended to read: ..."
        assert _detect_amendment_target(text) == ("Welfare and Institutions Code", "22650")

    def test_section_added_not_amended_does_not_match(self):
        # A newly-added section is not a restatement of an existing one.
        text = (
            "Section 84514 is added to the Government Code, immediately "
            "following Section 84513, to read: 84514. ..."
        )
        assert _detect_amendment_target(text) is None

    def test_arkansas_code_section_symbol_does_not_match(self):
        text = "Arkansas Code § 5-27-603 is amended to read as follows: ..."
        assert _detect_amendment_target(text) is None

    def test_plain_prose_does_not_match(self):
        text = "This act shall take effect on January 1, 2026."
        assert _detect_amendment_target(text) is None

    def test_leading_whitespace_tolerated(self):
        text = "  \n Section 3344 of the Civil Code is amended to read: 3344. (a)..."
        assert _detect_amendment_target(text) == ("Civil Code", "3344")


class TestGroupParallelVersions:
    def _passage(self, marker: str, text: str) -> tuple:
        return (marker, text, 0, len(text), [marker])

    def test_two_versions_grouped_last_is_representative(self):
        passages = [
            self._passage(
                "Section 647",
                "Section 647 of the Penal Code is amended to read: 647. First version.",
            ),
            self._passage(
                "Section 647",
                "Section 647 of the Penal Code is amended to read: 647. Second version.",
            ),
        ]
        meta = _group_parallel_versions(passages)
        assert meta[0]["parallel_version_representative"] is False
        assert meta[1]["parallel_version_representative"] is True
        assert meta[0]["parallel_version_group"] == meta[1]["parallel_version_group"]
        assert meta[0]["parallel_version_count"] == 2

    def test_singleton_amendment_header_not_grouped(self):
        passages = [
            self._passage(
                "Section 84504",
                "Section 84504 of the Government Code is amended to read: 84504. (a)...",
            ),
        ]
        meta = _group_parallel_versions(passages)
        assert meta == {}

    def test_different_sections_not_grouped_together(self):
        passages = [
            self._passage(
                "Section 84504.1",
                "Section 84504.1 of the Government Code is amended to read: ...",
            ),
            self._passage(
                "Section 84504.2",
                "Section 84504.2 of the Government Code is amended to read: ...",
            ),
        ]
        meta = _group_parallel_versions(passages)
        assert meta == {}

    def test_non_amendment_passages_untouched(self):
        passages = [
            self._passage("Paragraph 1", "This act shall take effect January 1, 2026."),
            self._passage("SEC. 2.", "The Legislature finds and declares..."),
        ]
        assert _group_parallel_versions(passages) == {}


class TestRealCorpusFixtures:
    """End-to-end against the real committed sources these fixes target."""

    def test_sb926_eight_versions_of_penal_code_647(self):
        data = open("output/law_texts/TMP-CA-AMENDMENTOFCAL.txt").read()
        passages = _segment_text(data)
        meta = _group_parallel_versions(passages)
        group_indices = [i for i, m in meta.items()
                         if m["parallel_version_group"] == "penal code:647"]
        assert len(group_indices) == 8
        representatives = [i for i in group_indices
                            if meta[i]["parallel_version_representative"]]
        assert representatives == [max(group_indices)]

    def test_ab2355_two_versions_of_govt_code_84504_2(self):
        data = open("output/law_texts/TMP-CA-AMENDMENTTOTHE.txt").read()
        passages = _segment_text(data)
        meta = _group_parallel_versions(passages)
        group_indices = [i for i, m in meta.items()
                         if m["parallel_version_group"] == "government code:84504.2"]
        assert len(group_indices) == 2
        # AB 2355's other five § 84504.x restatements are singletons and
        # must NOT be swept into this (or any) group.
        assert len(meta) == 2

    def test_sb11_two_versions_of_civil_code_3344(self):
        data = open("output/law_texts/US-CA-SB11.txt").read()
        passages = _segment_text(data)
        meta = _group_parallel_versions(passages)
        group_indices = [i for i, m in meta.items()
                         if m["parallel_version_group"] == "civil code:3344"]
        assert len(group_indices) == 2

    def test_ar_hb1877_negative_case_no_groups(self):
        # AR HB1877 amends several distinct sections (§§ 5-27-302, -304,
        # -601, -602, -603, -609), each exactly once — Arkansas's amendment
        # header shape ("Arkansas Code § N is amended to read as follows")
        # also doesn't match the CA-specific pattern at all.
        data = open("output/law_texts/TMP-AR-OFARKANSASCSAM.txt").read()
        passages = _segment_text(data)
        meta = _group_parallel_versions(passages)
        assert meta == {}
