"""Tests for Unicode normalization in evidence span verification.

BUG-4: Source PDFs/HTML use typographic Unicode characters (smart quotes,
en/em dashes, non-breaking spaces) that LLMs replace with ASCII equivalents
when quoting verbatim.  Without normalization, spans that are substantively
correct fail verification on character-level differences.
"""

from __future__ import annotations

import pytest

from src.agents.base import BaseExtractionAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_agent() -> BaseExtractionAgent:
    """Return a minimal concrete BaseExtractionAgent for testing."""

    class _TestAgent(BaseExtractionAgent):
        agent_name = "test"

        def get_system_prompt(self) -> str:
            return ""

        def get_extraction_prompt(self, passage: str, context=None) -> str:
            return ""

        def get_output_schema(self):
            return None

    return _TestAgent()


AGENT = make_agent()


# ---------------------------------------------------------------------------
# _normalize_unicode
# ---------------------------------------------------------------------------

class TestNormalizeUnicode:
    def test_en_dash_to_hyphen(self):
        assert AGENT._normalize_unicode("privacy\u2013protective") == "privacy-protective"

    def test_em_dash_to_hyphen(self):
        assert AGENT._normalize_unicode("opt\u2014out") == "opt-out"

    def test_non_breaking_hyphen_to_hyphen(self):
        assert AGENT._normalize_unicode("non\u2011breaking") == "non-breaking"

    def test_figure_dash_to_hyphen(self):
        assert AGENT._normalize_unicode("figure\u2012dash") == "figure-dash"

    def test_smart_single_quotes_to_ascii(self):
        assert AGENT._normalize_unicode("\u2018quoted\u2019") == "'quoted'"

    def test_low9_single_quote_to_ascii(self):
        assert AGENT._normalize_unicode("\u201alow9\u2019") == "'low9'"

    def test_smart_double_quotes_to_ascii(self):
        assert AGENT._normalize_unicode("\u201cquoted\u201d") == '"quoted"'

    def test_low9_double_quote_to_ascii(self):
        assert AGENT._normalize_unicode("\u201elow9\u201d") == '"low9"'

    def test_non_breaking_space_to_space(self):
        assert AGENT._normalize_unicode("word\u00a0word") == "word word"

    def test_narrow_no_break_space_to_space(self):
        assert AGENT._normalize_unicode("word\u202fword") == "word word"

    def test_thin_space_to_space(self):
        assert AGENT._normalize_unicode("word\u2009word") == "word word"

    def test_plain_ascii_unchanged(self):
        text = "The deployer shall conduct an impact assessment."
        assert AGENT._normalize_unicode(text) == text

    def test_mixed_variants_in_one_string(self):
        # Combines en-dash, smart quotes, non-breaking space
        result = AGENT._normalize_unicode(
            "\u201cprivacy\u2013protective\u201d\u00a0measures"
        )
        assert result == '"privacy-protective" measures'


# ---------------------------------------------------------------------------
# _normalize_text (Unicode + whitespace pipeline)
# ---------------------------------------------------------------------------

class TestNormalizeText:
    def test_chains_unicode_then_whitespace(self):
        # en-dash + extra spaces should produce a single clean hyphen with no double spaces
        result = AGENT._normalize_text("privacy\u2013 protective  measures")
        assert result == "privacy- protective measures"

    def test_newlines_and_unicode(self):
        # en-dash → "-", newline → " " (collapsed by whitespace norm), NBSP → " "
        result = AGENT._normalize_text("shall\u2013\nmust\u00a0comply")
        assert result == "shall- must comply"

    def test_idempotent_on_plain_ascii(self):
        text = "plain ascii text with spaces"
        assert AGENT._normalize_text(text) == text


# ---------------------------------------------------------------------------
# _verify_evidence_spans — Unicode variants now verify correctly
# ---------------------------------------------------------------------------

class TestVerifyEvidenceSpansUnicode:
    """
    Core regression tests for BUG-4.  Each case pairs a source passage
    containing a typographic Unicode character with a span that uses the
    ASCII equivalent (as an LLM would produce), or vice versa.
    """

    def _verify(self, span_text: str, passage: str) -> bool:
        results = AGENT._verify_evidence_spans(
            [{"field_name": "subject", "text": span_text}], passage
        )
        return results[0]["verified"]

    # En-dash in source, ASCII hyphen from LLM
    def test_en_dash_in_source_ascii_in_span(self):
        passage = "The privacy\u2013protective measures shall apply."
        span = "privacy-protective measures"
        assert self._verify(span, passage) is True

    # ASCII hyphen in source, LLM preserved it (control case)
    def test_ascii_hyphen_in_both(self):
        passage = "The privacy-protective measures shall apply."
        span = "privacy-protective measures"
        assert self._verify(span, passage) is True

    # Em-dash in source, ASCII in span
    def test_em_dash_in_source_ascii_in_span(self):
        passage = "opt\u2014out rights are granted to consumers."
        span = "opt-out rights are granted to consumers."
        assert self._verify(span, passage) is True

    # Non-breaking hyphen in source, ASCII in span
    def test_non_breaking_hyphen_in_source(self):
        passage = "The non\u2011compliance penalty shall not exceed five thousand dollars."
        span = "non-compliance penalty shall not exceed five thousand dollars."
        assert self._verify(span, passage) is True

    # Smart quotes in source, ASCII in span
    def test_smart_quotes_in_source(self):
        passage = 'The term \u201cdeployer\u201d means any entity that deploys the system.'
        span = 'The term "deployer" means any entity that deploys the system.'
        assert self._verify(span, passage) is True

    # Non-breaking space in source, regular space in span
    def test_non_breaking_space_in_source(self):
        passage = "deployers\u00a0shall\u00a0disclose AI use to affected individuals."
        span = "deployers shall disclose AI use to affected individuals."
        assert self._verify(span, passage) is True

    # Span with Unicode, passage with ASCII (reverse direction)
    def test_unicode_in_span_ascii_in_source(self):
        passage = "The privacy-protective measures shall apply."
        span = "privacy\u2013protective measures"
        assert self._verify(span, passage) is True

    # Genuinely wrong span — should still fail
    def test_genuinely_nonmatching_span_still_fails(self):
        passage = "The deployer shall conduct an impact assessment."
        span = "The developer shall submit an annual report."
        assert self._verify(span, passage) is False

    # Empty span — should be skipped (not added to results)
    def test_empty_span_text(self):
        passage = "Some legislative text about AI systems."
        results = AGENT._verify_evidence_spans(
            [{"field_name": "subject", "text": ""}], passage
        )
        # Empty-text spans are filtered out — nothing to verify
        assert results == []

    # Case-insensitive fallback still works after Unicode normalization
    def test_case_insensitive_fallback_with_unicode(self):
        passage = "DEPLOYERS\u2013OF\u2013AI\u2013SYSTEMS shall disclose."
        span = "deployers-of-ai-systems shall disclose."
        assert self._verify(span, passage) is True

    # Multiple spans in one call — mix of passing and failing
    def test_multiple_spans_mixed_results(self):
        passage = "The deployer\u2019s obligation is to conduct a bias audit annually."
        spans = [
            {"field_name": "subject", "text": "deployer's obligation"},
            {"field_name": "action", "text": "fabricated text that is not present"},
        ]
        results = AGENT._verify_evidence_spans(spans, passage)
        assert results[0]["verified"] is True
        assert results[1]["verified"] is False
