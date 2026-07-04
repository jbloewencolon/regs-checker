"""Unit tests for EA2-4 — amendment/engrossed-bill markup handling.

Bug: BeautifulSoup's get_text() has no concept of strikethrough/underline
semantics, so a legislatively-struck (no-longer-law) clause reads into the
extracted passage stream identically to currently-binding text. Confirmed
live in this corpus: 2025 Wisconsin Act 69 (TMP-WI-ESTATEADVERTIS.html)
flattens to "...client , principal firm, or firm , without..." where
", principal firm," is a struck (deleted) fragment with zero distinguishing
signal once stripped of styling.

Fix: `_strip_struck_content` removes unambiguously-marked deleted text
(tag name strike/del/s, or inline `text-decoration: line-through`) from the
BeautifulSoup tree before get_text() runs. Retained (inserted/underlined)
text is left alone — get_text() already includes it correctly, it just
wasn't flagged. `parse_and_normalize` also flags `amendment_markup_detected`
on affected passages, plus a separate bracket-convention heuristic (some
states print deletions as literal "[bracketed]" text that survives PDF/
plaintext extraction) — informational only, never auto-stripped, since
ordinary statutory citations are also bracketed.
"""

from __future__ import annotations

from src.ingestion.parser import (
    _BRACKET_AMENDMENT_MIN_COUNT,
    _BRACKET_DELETION_PATTERN,
    _is_line_through_style,
    _is_underline_style,
    _make_soup,
    _parse_html,
    _strip_struck_content,
)


def _html(body: str) -> bytes:
    return f"<html><body>{body}</body></html>".encode()


# ---------------------------------------------------------------------------
# Style-string helpers
# ---------------------------------------------------------------------------


class TestStyleHelpers:
    def test_line_through_detected(self):
        assert _is_line_through_style("text-decoration: line-through;")

    def test_line_through_detected_no_space(self):
        assert _is_line_through_style("text-decoration:line-through")

    def test_none_is_not_line_through(self):
        assert not _is_line_through_style("text-decoration: none;")

    def test_underline_detected(self):
        assert _is_underline_style("text-decoration: underline;")

    def test_none_is_not_underline(self):
        assert not _is_underline_style("text-decoration: none;")

    def test_unrelated_style_is_neither(self):
        assert not _is_line_through_style("color: red;")
        assert not _is_underline_style("color: red;")


# ---------------------------------------------------------------------------
# _strip_struck_content
# ---------------------------------------------------------------------------


class TestStripStruckContent:
    def test_strike_tag_removed(self):
        soup = _make_soup(_html("<p>A developer shall <strike>not</strike> comply.</p>"))
        info = _strip_struck_content(soup)
        assert info["struck_found"] is True
        assert info["struck_chars_removed"] == 3
        assert "not" not in soup.get_text()

    def test_del_tag_removed(self):
        soup = _make_soup(_html("<p>A developer shall <del>never</del> comply.</p>"))
        info = _strip_struck_content(soup)
        assert info["struck_found"] is True
        assert "never" not in soup.get_text()

    def test_s_tag_removed(self):
        soup = _make_soup(_html("<p>A developer shall <s>maybe</s> comply.</p>"))
        info = _strip_struck_content(soup)
        assert info["struck_found"] is True
        assert "maybe" not in soup.get_text()

    def test_inline_line_through_style_removed(self):
        soup = _make_soup(
            _html('<p>A developer shall <span style="text-decoration: line-through;">'
                  "possibly</span> comply.</p>")
        )
        info = _strip_struck_content(soup)
        assert info["struck_found"] is True
        assert "possibly" not in soup.get_text()

    def test_inline_none_style_not_removed(self):
        soup = _make_soup(
            _html('<p>A developer shall <span style="text-decoration: none;">'
                  "certainly</span> comply.</p>")
        )
        info = _strip_struck_content(soup)
        assert info["struck_found"] is False
        assert "certainly" in soup.get_text()

    def test_ins_tag_detected_but_not_removed(self):
        soup = _make_soup(_html("<p>A developer shall <ins>always</ins> comply.</p>"))
        info = _strip_struck_content(soup)
        assert info["inserted_found"] is True
        assert info["struck_found"] is False
        assert "always" in soup.get_text()

    def test_inline_underline_style_detected_but_not_removed(self):
        soup = _make_soup(
            _html('<p>A developer shall <span style="text-decoration: underline;">'
                  "always</span> comply.</p>")
        )
        info = _strip_struck_content(soup)
        assert info["inserted_found"] is True
        assert "always" in soup.get_text()

    def test_clean_html_has_no_markup(self):
        soup = _make_soup(_html("<p>A developer shall comply with this section.</p>"))
        info = _strip_struck_content(soup)
        assert info == {
            "struck_found": False,
            "inserted_found": False,
            "struck_chars_removed": 0,
        }

    def test_mixed_strike_and_underline_both_detected(self):
        # Mirrors the real Wisconsin Act 69 pattern: a struck fragment and an
        # inserted sentence in the same passage.
        soup = _make_soup(
            _html(
                '<p>other than the licensee\'s client<span style="text-decoration: '
                'line-through;">, principal firm,</span> or firm, without consent.'
                '<span style="text-decoration: underline;"> This paragraph does not '
                "prohibit an out-of-state broker.</span></p>"
            )
        )
        info = _strip_struck_content(soup)
        assert info["struck_found"] is True
        assert info["inserted_found"] is True
        text = soup.get_text()
        assert "principal firm" not in text
        assert "out-of-state broker" in text

    def test_unrelated_inline_style_not_flagged(self):
        # e.g. a "Listen Live" audio-stream link elsewhere on a legislature
        # page — real false-positive case found during the EA1-4 corpus
        # audit (TMP-RI-DECEPTIVEANDFR.html); underline there is UI chrome,
        # not amendment markup, but since it's inline style-based there is
        # no way to distinguish it — documented as an accepted precision
        # tradeoff, not asserted away here (this test just pins that a
        # single unrelated underlined span alone still surfaces as
        # inserted_found, i.e. no crash / no special-casing needed).
        soup = _make_soup(
            _html('<a style="text-decoration:underline;">Listen Live</a>')
        )
        info = _strip_struck_content(soup)
        assert info["inserted_found"] is True


# ---------------------------------------------------------------------------
# _parse_html end-to-end
# ---------------------------------------------------------------------------


class TestParseHtmlEndToEnd:
    def test_returns_tuple_of_passages_and_markup_info(self):
        content = _html("<p>Section 1. A developer shall comply.</p>")
        result = _parse_html(content)
        assert isinstance(result, tuple)
        assert len(result) == 2
        passages, markup_info = result
        assert isinstance(passages, list)
        assert markup_info == {
            "struck_found": False,
            "inserted_found": False,
            "struck_chars_removed": 0,
        }

    def test_struck_text_absent_from_final_passages(self):
        content = _html(
            "<p>Section 1. A developer shall "
            '<span style="text-decoration: line-through;">not</span> '
            "comply with this section.</p>"
        )
        passages, markup_info = _parse_html(content)
        full_text = " ".join(p[1] for p in passages)
        assert "not comply" not in full_text
        assert "shall" in full_text and "comply with this section" in full_text
        assert markup_info["struck_found"] is True

    def test_inserted_text_retained_in_final_passages(self):
        content = _html(
            "<p>Section 1. A developer shall comply. "
            '<span style="text-decoration: underline;">'
            "This paragraph adds a new exception.</span></p>"
        )
        passages, markup_info = _parse_html(content)
        full_text = " ".join(p[1] for p in passages)
        assert "This paragraph adds a new exception." in full_text
        assert markup_info["inserted_found"] is True

    def test_pdf_guard_branch_returns_none_markup_info(self):
        # Some .html files are actually mislabeled PDFs.
        pdf_like = b"%PDF-1.4\n%mock"
        passages, markup_info = _parse_html(pdf_like)
        assert markup_info is None


# ---------------------------------------------------------------------------
# Bracket-convention deletion markers (KY/NJ-style)
# ---------------------------------------------------------------------------


class TestBracketDeletionPattern:
    def test_matches_alpha_bracket_spans(self):
        text = "loans for [SINGLE FAMILY] SINGLE-FAMILY home [IMPROVEMENT] improvement"
        matches = _BRACKET_DELETION_PATTERN.findall(text)
        assert len(matches) == 2

    def test_does_not_match_numeric_leading_citation(self):
        text = "as provided in [42 U.S.C. § 2000e-8] of federal law"
        matches = _BRACKET_DELETION_PATTERN.findall(text)
        assert matches == []

    def test_single_bracket_below_min_count_threshold(self):
        text = "the term [defined] elsewhere in this chapter"
        matches = _BRACKET_DELETION_PATTERN.findall(text)
        assert len(matches) < _BRACKET_AMENDMENT_MIN_COUNT

    def test_clustered_brackets_meet_min_count_threshold(self):
        text = "[deviant] sexual conduct, [beastiality], and [such material] are prohibited"
        matches = _BRACKET_DELETION_PATTERN.findall(text)
        assert len(matches) >= _BRACKET_AMENDMENT_MIN_COUNT
