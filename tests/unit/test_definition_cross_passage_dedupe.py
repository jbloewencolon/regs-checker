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
