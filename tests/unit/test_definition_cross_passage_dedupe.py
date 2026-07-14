"""Tests for QA-4: cross-passage definition dedupe at law level.

AR HB1877 (2026-07-12 run) produced 14 definition rows for ~6 unique terms:
overlapping passages re-extracted the same code section's definitions
("Indistinguishable" x4, "Adversarial testing" x3). The copies are not
byte-identical — truncated tails, source-doubled words ("that is that is"),
different scope strings — so the exact payload-hash dedup can't catch them.
The same term defined in DIFFERENT code sections (§ 5-27-302 vs § 5-27-601)
has meaningfully different text and must be kept.

Text fixtures below are the real payloads from that run.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.ingestion.extractor import (
    _find_cross_passage_definition_dup,
    _is_duplicate_definition_text,
)

INDIST_302_TRUNCATED = (
    "a visual or print medium that is such that an ordinary person viewing "
    "the visual or print medium would conclude that the visual or print "
    "medium depicts an actual child engaged in"
)
INDIST_302_FULL = INDIST_302_TRUNCATED + " the conduct depicted."
INDIST_601 = (
    "that a depiction is such that an ordinary person viewing the depiction "
    "would conclude that it is a depiction of an actual child engaged in "
    "the conduct depicted"
)
SEDM_CLEAN = (
    "any photograph, digitized image, or visual depiction of a minor or a "
    "computer generated image that is indistinguishable from a depiction of "
    "a minor: (i) In any condition of nudity; or (ii) Involved in any "
    "prohibited sexual act."
)
SEDM_DOUBLED = SEDM_CLEAN.replace("that is indistinguishable", "that is that is indistinguishable")


class TestDuplicateDefinitionText:
    def test_truncated_tail_is_duplicate(self):
        assert _is_duplicate_definition_text(INDIST_302_TRUNCATED, INDIST_302_FULL)

    def test_source_doubled_words_is_duplicate(self):
        assert _is_duplicate_definition_text(SEDM_CLEAN, SEDM_DOUBLED)

    def test_case_and_punctuation_variants_are_duplicates(self):
        assert _is_duplicate_definition_text(
            "Means produced, adapted, or modified through AI",
            "means produced adapted or modified through AI;",
        )

    def test_different_code_sections_are_not_duplicates(self):
        """§ 5-27-302 vs § 5-27-601(17) — same term, distinct legal facts."""
        assert not _is_duplicate_definition_text(INDIST_302_FULL, INDIST_601)

    def test_empty_text_never_matches(self):
        assert not _is_duplicate_definition_text("", INDIST_302_FULL)
        assert not _is_duplicate_definition_text(INDIST_302_FULL, "")


LOITER_BARE = (
    "to delay or linger without a lawful purpose for being on the property "
    "and for the purpose of committing a crime as opportunity may be "
    "discovered"
)
LOITER_PREAMBLED = (
    "As used in this subdivision, “loiter” means " + LOITER_BARE + "."
)
AI_BARE = (
    "an engineered or machine-based system that varies in its level of "
    "autonomy and that can, for explicit or implicit objectives, infer from "
    "the input it receives how to generate outputs that can influence "
    "physical or virtual environments"
)
AI_PREAMBLED = (
    "For purposes of this subdivision, “artificial intelligence” "
    "means " + AI_BARE + "."
)


class TestPreambleVariantDuplicates:
    """QA-7: same definition, one copy carrying a quoting preamble.

    Observed on the 2026-07-13 run (CA SB 926 'loiter'/'prostitution',
    CA SB 1120 'artificial intelligence'): the preamble drops sequence
    similarity to 0.85-0.88, under the 0.9 threshold, so QA-4 alone
    missed these.
    """

    def test_preambled_copy_is_duplicate(self):
        assert _is_duplicate_definition_text(
            LOITER_BARE, LOITER_PREAMBLED, term="loiter"
        )

    def test_for_purposes_of_variant_is_duplicate(self):
        assert _is_duplicate_definition_text(
            AI_BARE, AI_PREAMBLED, term="artificial intelligence"
        )

    def test_includes_verb_variant_is_duplicate(self):
        assert _is_duplicate_definition_text(
            "any lewd act between persons for money or other consideration",
            "As used in this subdivision, “prostitution” includes "
            "any lewd act between persons for money or other consideration.",
            term="prostitution",
        )

    def test_without_term_preamble_still_missed(self):
        """Without the term, the preamble can't be stripped — documents that
        the term parameter is what closes the QA-7 gap."""
        assert not _is_duplicate_definition_text(LOITER_BARE, LOITER_PREAMBLED)

    def test_genuinely_different_definition_kept_despite_term(self):
        """SB 926: 'loiter' has a second, distinct statutory meaning."""
        assert not _is_duplicate_definition_text(
            LOITER_BARE,
            "Who loiters, prowls, or wanders upon the private property of "
            "another, at any time, without visible or lawful business with "
            "the owner or occupant.",
            term="loiter",
        )

    def test_cross_reference_definitions_kept(self):
        """SB 11 'digital replica': two different cross-references are not
        near-identical texts and must both survive."""
        assert not _is_duplicate_definition_text(
            "has the same meaning as in Section 3344.1 of the Civil Code.",
            "includes a digital replica, as defined in Section 3344.1.",
            term="digital replica",
        )


def _mock_db(rows):
    db = MagicMock()
    db.execute.return_value.all.return_value = rows
    return db


def _record(document_version_id=7):
    rec = MagicMock()
    rec.document_version_id = document_version_id
    return rec


class TestFindCrossPassageDup:
    def test_finds_near_duplicate_same_term(self):
        db = _mock_db([(101, {"term": "Indistinguishable",
                              "definition_text": INDIST_302_TRUNCATED})])
        item = {"term": "Indistinguishable", "definition_text": INDIST_302_FULL}
        assert _find_cross_passage_definition_dup(db, _record(), item) == 101

    def test_same_term_different_section_kept(self):
        db = _mock_db([(101, {"term": "Indistinguishable",
                              "definition_text": INDIST_302_FULL})])
        item = {"term": "Indistinguishable", "definition_text": INDIST_601}
        assert _find_cross_passage_definition_dup(db, _record(), item) is None

    def test_different_term_same_text_kept(self):
        """Term gate: identical boilerplate under different terms is not a dupe."""
        db = _mock_db([(101, {"term": "Computer generated",
                              "definition_text": SEDM_CLEAN})])
        item = {"term": "Sexually explicit digital material",
                "definition_text": SEDM_CLEAN}
        assert _find_cross_passage_definition_dup(db, _record(), item) is None

    def test_term_match_is_case_insensitive(self):
        db = _mock_db([(55, {"term": "COMPUTER GENERATED",
                             "definition_text": SEDM_CLEAN})])
        item = {"term": "Computer generated", "definition_text": SEDM_CLEAN}
        assert _find_cross_passage_definition_dup(db, _record(), item) == 55

    def test_missing_term_or_text_skips_check(self):
        db = _mock_db([])
        assert _find_cross_passage_definition_dup(
            db, _record(), {"term": "", "definition_text": "x"}
        ) is None
        assert _find_cross_passage_definition_dup(
            db, _record(), {"term": "x", "definition_text": ""}
        ) is None
        # No query issued when the gate fields are empty.
        db.execute.assert_not_called()

    def test_non_dict_payload_rows_ignored(self):
        db = _mock_db([(9, None), (10, "corrupt")])
        item = {"term": "Indistinguishable", "definition_text": INDIST_302_FULL}
        assert _find_cross_passage_definition_dup(db, _record(), item) is None

    def test_preamble_variant_caught_through_db_path(self):
        """QA-7 end-to-end: the stored bare copy suppresses the incoming
        preambled copy of the same definition."""
        db = _mock_db([(88, {"term": "loiter", "definition_text": LOITER_BARE})])
        item = {"term": "loiter", "definition_text": LOITER_PREAMBLED}
        assert _find_cross_passage_definition_dup(db, _record(), item) == 88
