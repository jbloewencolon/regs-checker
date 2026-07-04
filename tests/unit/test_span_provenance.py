"""Unit tests for EA2-2 — span provenance (match_tier, loose_match, raw offsets).

Bug: verify_evidence_spans() reported char_start/char_end relative to a
normalized intermediate string (Unicode-substituted, whitespace-collapsed),
never translated back to the raw passage text. Any passage containing smart
quotes, en/em dashes, multiple spaces, or newlines inside a matched span
would produce offsets that don't correspond to real positions in the raw
text stored in the database — audit-UI highlighting built on those offsets
would be silently wrong. Additionally, no field distinguished an exact
match from a loose (punctuation/artifact-tolerant) one.

Fix: Tier 1/2 offsets are now translated through an index map back to the
raw passage. Tier 3/4 (loose) offsets are honestly reported as None rather
than continuing to report wrong numbers, since precisely inverting those
transforms isn't implemented. Every verified span now carries match_tier
(1-4) and loose_match (bool).
"""

from __future__ import annotations

import unicodedata

from src.core.text_grounding import (
    _normalize_text,
    _normalize_text_with_map,
    _normalize_unicode_with_map,
    _normalize_whitespace,
    _normalize_whitespace_with_map,
    verify_evidence_spans,
)


def _span(text: str, field_name: str = "action") -> dict:
    return {"field_name": field_name, "text": text}


# ---------------------------------------------------------------------------
# _normalize_unicode_with_map
# ---------------------------------------------------------------------------


class TestNormalizeUnicodeWithMap:
    def test_plain_ascii_maps_identity(self):
        text = "hello world"
        out, spans = _normalize_unicode_with_map(text)
        assert out == text
        assert spans == [(i, i + 1) for i in range(len(text))]

    def test_smart_quote_substitution_maps_to_original_position(self):
        text = "the ‘developer’ shall comply"
        out, spans = _normalize_unicode_with_map(text)
        assert out == "the 'developer' shall comply"
        # The output char at the smart-quote's position still points back
        # to the ORIGINAL smart-quote's raw index, not the ASCII replacement.
        smart_quote_raw_idx = text.index("‘")
        out_idx = out.index("'")
        assert spans[out_idx] == (smart_quote_raw_idx, smart_quote_raw_idx + 1)

    def test_soft_hyphen_is_deleted_and_shifts_subsequent_spans(self):
        text = "compli­ ance"  # soft hyphen between compli and ance
        out, spans = _normalize_unicode_with_map(text)
        assert "­" not in out
        assert len(spans) == len(out)
        # Every remaining char's span must point to ITS OWN correct raw index
        # (not the deleted char's index) — reconstruct and verify each maps
        # to the character it actually represents in the raw text.
        for out_idx, (raw_start, raw_end) in enumerate(spans):
            assert text[raw_start:raw_end] == out[out_idx] or (
                out[out_idx] in ("'", '"', "-", " ")
            )


# ---------------------------------------------------------------------------
# _normalize_whitespace_with_map
# ---------------------------------------------------------------------------


class TestNormalizeWhitespaceWithMap:
    def _identity_spans(self, text: str) -> list[tuple[int, int]]:
        return [(i, i + 1) for i in range(len(text))]

    def test_matches_plain_normalize_whitespace_output(self):
        samples = [
            "  hello   world  ",
            "\n\nhello\nworld\n",
            "hello world",
            "",
            "   ",
            "a",
            "a b",
            "  a  b  c  ",
        ]
        for text in samples:
            expected = _normalize_whitespace(text)
            collapsed, _ = _normalize_whitespace_with_map(text, self._identity_spans(text))
            assert collapsed == expected, f"mismatch for {text!r}"

    def test_internal_run_span_covers_entire_raw_whitespace_run(self):
        text = "hello   world"  # 3 spaces between words
        collapsed, spans = _normalize_whitespace_with_map(text, self._identity_spans(text))
        assert collapsed == "hello world"
        space_out_idx = collapsed.index(" ")
        raw_start, raw_end = spans[space_out_idx]
        # The collapsed space's raw span must cover all 3 original spaces.
        assert text[raw_start:raw_end] == "   "

    def test_leading_and_trailing_whitespace_dropped(self):
        text = "   hello world   "
        collapsed, spans = _normalize_whitespace_with_map(text, self._identity_spans(text))
        assert collapsed == "hello world"
        assert len(spans) == len(collapsed)


# ---------------------------------------------------------------------------
# _normalize_text_with_map
# ---------------------------------------------------------------------------


class TestNormalizeTextWithMap:
    def test_output_string_matches_plain_normalize_text(self):
        samples = [
            "A developer shall comply.",
            "the ‘developer’ shall “comply”",
            "  multiple   spaces \n\n and newlines  ",
            "en–dash and em—dash",
        ]
        for text in samples:
            result = _normalize_text_with_map(text)
            assert result is not None
            collapsed, _ = result
            assert collapsed == _normalize_text(text), f"mismatch for {text!r}"

    def test_span_length_matches_output_length(self):
        text = "the ‘developer’ shall   comply"
        result = _normalize_text_with_map(text)
        assert result is not None
        collapsed, spans = result
        assert len(spans) == len(collapsed)

    def test_returns_none_when_nfc_changes_length(self):
        # "e" + combining acute accent (U+0301) composes under NFC into a
        # single precomposed "é" — length changes from 2 to 1. This is the
        # rare case where raw-offset translation is deliberately unavailable
        # rather than silently wrong.
        text = "café"  # "cafe" + combining acute on the 'e'
        assert unicodedata.normalize("NFC", text) != text
        assert len(unicodedata.normalize("NFC", text)) < len(text)
        result = _normalize_text_with_map(text)
        assert result is None


# ---------------------------------------------------------------------------
# verify_evidence_spans — match_tier / loose_match / raw offsets
# ---------------------------------------------------------------------------


class TestVerifyEvidenceSpansMatchTier:
    def test_exact_match_is_tier_1_not_loose(self):
        passage = "A developer shall conduct an annual audit."
        spans = [_span("shall conduct an annual audit")]
        result = verify_evidence_spans(spans, passage)
        assert result[0]["verified"] is True
        assert result[0]["match_tier"] == 1
        assert result[0]["loose_match"] is False

    def test_case_insensitive_match_is_tier_2(self):
        passage = "A developer SHALL conduct an annual audit."
        spans = [_span("shall conduct an annual audit")]
        result = verify_evidence_spans(spans, passage)
        assert result[0]["verified"] is True
        assert result[0]["match_tier"] == 2
        assert result[0]["loose_match"] is False

    def test_loose_match_is_tier_3_with_no_offsets(self):
        # Re-punctuated span (colon instead of comma) forces past tier 1/2
        # into the punctuation-insensitive tier 3 match.
        passage = "A developer shall conduct an annual audit, and file a report."
        spans = [_span("shall conduct an annual audit: and file a report")]
        result = verify_evidence_spans(spans, passage)
        assert result[0]["verified"] is True
        assert result[0]["match_tier"] == 3
        assert result[0]["loose_match"] is True
        assert result[0]["char_start"] is None
        assert result[0]["char_end"] is None

    def test_not_found_has_no_match_tier_key(self):
        passage = "A developer shall conduct an annual audit."
        spans = [_span("this text does not appear anywhere in the passage at all")]
        result = verify_evidence_spans(spans, passage)
        assert result[0]["verified"] is False
        assert "match_tier" not in result[0]
        assert "loose_match" not in result[0]


class TestVerifyEvidenceSpansRawOffsets:
    """The critical correctness property: char_start/char_end must index
    into the RAW passage exactly as passed to verify_evidence_spans, not a
    normalized intermediate — passage[char_start:char_end] must recover a
    string equivalent (post-normalization) to the matched span.
    """

    def test_offsets_correct_for_plain_ascii(self):
        passage = "A developer shall conduct an annual audit."
        spans = [_span("shall conduct an annual audit")]
        result = verify_evidence_spans(spans, passage)
        r = result[0]
        assert passage[r["char_start"]:r["char_end"]] == "shall conduct an annual audit"

    def test_offsets_correct_when_passage_has_smart_quotes_before_match(self):
        # Smart quotes BEFORE the matched span shift its true raw position;
        # if offsets were still relative to the normalized string, they'd
        # misalign relative to the raw passage whenever a substitution
        # changes length before the match (soft hyphen does; quotes don't,
        # but this guards the general translation-through-substitution path).
        passage = "The ‘Developer’ shall conduct an annual audit."
        spans = [_span("shall conduct an annual audit")]
        result = verify_evidence_spans(spans, passage)
        r = result[0]
        assert passage[r["char_start"]:r["char_end"]] == "shall conduct an annual audit"

    def test_offsets_correct_when_raw_passage_has_extra_internal_whitespace(self):
        # The passage has a newline + indentation between "shall" and
        # "conduct" that norm_passage collapses to one space. The raw
        # offsets must span the FULL raw whitespace run, not just one
        # character of it, so slicing the raw passage recovers the
        # (pre-collapse) original text rather than truncating it.
        passage = "A developer shall\n    conduct an annual audit."
        spans = [_span("shall conduct an annual audit")]
        result = verify_evidence_spans(spans, passage)
        r = result[0]
        raw_slice = passage[r["char_start"]:r["char_end"]]
        assert _normalize_text(raw_slice) == "shall conduct an annual audit"
        # And the raw slice must contain the actual raw whitespace, proving
        # the end boundary wasn't truncated to a single collapsed space.
        assert "\n" in raw_slice

    def test_offsets_correct_for_case_insensitive_tier(self):
        passage = "A developer SHALL conduct an annual audit."
        spans = [_span("shall conduct an annual audit")]
        result = verify_evidence_spans(spans, passage)
        r = result[0]
        assert passage[r["char_start"]:r["char_end"]] == "SHALL conduct an annual audit"

    def test_offsets_correct_when_span_is_at_start_of_passage(self):
        passage = "Shall conduct an annual audit, per this section."
        spans = [_span("Shall conduct an annual audit")]
        result = verify_evidence_spans(spans, passage)
        r = result[0]
        assert r["char_start"] == 0
        assert passage[r["char_start"]:r["char_end"]] == "Shall conduct an annual audit"

    def test_offsets_correct_when_span_is_at_end_of_passage(self):
        passage = "This section requires the developer to conduct an annual audit"
        spans = [_span("conduct an annual audit")]
        result = verify_evidence_spans(spans, passage)
        r = result[0]
        assert r["char_end"] == len(passage)
        assert passage[r["char_start"]:r["char_end"]] == "conduct an annual audit"
