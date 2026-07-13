"""Regression tests for the Tier-4 span-verification ordering bug (QA-1).

The 2026-07-12 extraction run produced 0 verified spans on both documents
whose source text embeds bill margin/line numbers (AR HB1877, AZ SB 1359),
while the one clean-text document (AZ SB1462) verified 12/12 — a perfect
correlation with formatting, not content. Root cause: verify_evidence_spans
computed its Tier-4 input as strip_revisor_artifacts(norm_passage), but
norm_passage is already whitespace-collapsed — every newline replaced by a
space — so the line-anchored _MARGIN_NUM/_HYPHEN_BREAK patterns could never
match and Tier 4 was silently a no-op. Stripping must run on the raw,
line-structured text BEFORE whitespace collapse.

Passage fixtures below mirror the real failing documents' structure.
"""

from __future__ import annotations

from src.core.text_grounding import strip_revisor_artifacts, verify_evidence_spans

# Mirrors output/law_texts/TMP-AR-OFARKANSASCSAM.txt: leading indentation,
# bill line numbers at line starts, statutory numbering inline.
AR_PASSAGE = (
    '                (6)     "Computer generated" means produced, adapted, or modified,\n'
    "33   in whole or in part, through the use of artificial intelligence;\n"
    '34                 (7)(A)     "Indistinguishable" means a visual or print medium that\n'
    "35   is such that an ordinary person viewing the visual or print medium would\n"
    "36   conclude that the visual or print medium depicts an actual child engaged in\n"
    "37   the conduct depicted."
)

# Mirrors TMP-AZ-ARIZONAPOLITIC.txt: ALL-CAPS enacted text with margin numbers.
AZ_PASSAGE = (
    " 1   WITHIN NINETY DAYS BEFORE AN ELECTION AT WHICH A CANDIDATE FOR\n"
    " 2   ELECTED OFFICE WILL APPEAR ON THE BALLOT, A PERSON WHO ACTS AS A\n"
    " 3   CREATOR SHALL NOT CREATE AND DISTRIBUTE A SYNTHETIC MEDIA MESSAGE\n"
    " 4   THAT THE PERSON KNOWS IS A DECEPTIVE AND FRAUDULENT DEEPFAKE."
)


class TestTier4MarginNumberStripping:
    def test_quote_spanning_margin_numbered_lines_verifies(self):
        """The exact quote shape that failed on every AR HB1877 row."""
        span = {
            "text": (
                "means produced, adapted, or modified, in whole or in part, "
                "through the use of artificial intelligence;"
            ),
            "field_name": "definition_text",
        }
        result = verify_evidence_spans([span], AR_PASSAGE)
        assert result[0]["verified"] is True
        assert result[0]["match_tier"] == 4
        assert result[0]["loose_match"] is True
        # Tier 4 coordinates are not translated back to the raw passage.
        assert result[0]["char_start"] is None

    def test_all_caps_quote_with_margin_numbers_verifies(self):
        """The AZ SB 1359 shape: caps match, margin numbers break Tiers 1-3."""
        span = {
            "text": (
                "WITHIN NINETY DAYS BEFORE AN ELECTION AT WHICH A CANDIDATE FOR "
                "ELECTED OFFICE WILL APPEAR ON THE BALLOT"
            ),
            "field_name": "temporal_condition",
        }
        result = verify_evidence_spans([span], AZ_PASSAGE)
        assert result[0]["verified"] is True
        assert result[0]["match_tier"] == 4

    def test_multi_line_quote_across_several_margin_numbers(self):
        span = {
            "text": (
                "a visual or print medium that is such that an ordinary person "
                "viewing the visual or print medium would conclude that the "
                "visual or print medium depicts an actual child engaged in "
                "the conduct depicted."
            ),
            "field_name": "definition_text",
        }
        result = verify_evidence_spans([span], AR_PASSAGE)
        assert result[0]["verified"] is True
        assert result[0]["match_tier"] == 4

    def test_span_carrying_margin_number_inside_quote_verifies(self):
        """A model quoting verbatim can carry the newline + margin number."""
        span = {
            "text": (
                '"Computer generated" means produced, adapted, or modified,\n'
                "33   in whole or in part, through the use of artificial intelligence;"
            ),
            "field_name": "definition_text",
        }
        result = verify_evidence_spans([span], AR_PASSAGE)
        assert result[0]["verified"] is True

    def test_clean_text_still_verifies_tier1_with_offsets(self):
        """No regression: exact matches on clean passages keep raw offsets."""
        passage = 'For the purposes of this section: "Harm" means physical injury.'
        span = {"text": '"Harm" means physical injury.', "field_name": "definition"}
        result = verify_evidence_spans([span], passage)
        assert result[0]["verified"] is True
        assert result[0]["match_tier"] == 1
        start, end = result[0]["char_start"], result[0]["char_end"]
        assert passage[start:end] == '"Harm" means physical injury.'

    def test_fabricated_span_still_fails(self):
        span = {
            "text": "the attorney general shall promulgate implementing regulations",
            "field_name": "enforcement",
        }
        result = verify_evidence_spans([span], AR_PASSAGE)
        assert result[0]["verified"] is False

    def test_short_span_below_tier4_floor_not_verified_via_tier4(self):
        """< 25 loose chars must not sneak through the artifact-stripped tier.

        "modified, in whole" only matches once the margin number between
        "modified," and "in whole" is stripped (Tiers 1-3 all fail), but its
        loose form is 17 chars — under Tier 4's 25-char floor.
        """
        span = {"text": "modified, in whole", "field_name": "fragment"}
        result = verify_evidence_spans([span], AR_PASSAGE)
        assert result[0]["verified"] is False


class TestStripRevisorArtifactsOnRawText:
    def test_margin_numbers_removed_from_line_starts(self):
        assert "33" not in strip_revisor_artifacts(
            "some text ends here,\n33   and continues here"
        )

    def test_inline_numbers_preserved(self):
        out = strip_revisor_artifacts("within ninety (90) days of section 16-1023")
        assert "(90)" in out and "16-1023" in out

    def test_hyphen_break_repaired(self):
        assert "compliance" in strip_revisor_artifacts("compli-\nance program")
