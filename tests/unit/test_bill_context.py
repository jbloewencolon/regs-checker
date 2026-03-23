"""Tests for src.core.bill_context — bill-level context builder."""

import pytest

from src.core.bill_context import (
    MAX_DEFINITIONS_CHARS,
    MAX_SCOPE_CHARS,
    _extract_defined_terms,
    _is_definition_passage,
    _is_scope_passage,
    build_bill_context,
)


# ── Classification tests ────────────────────────────────────────────────────


class TestIsDefinitionPassage:
    def test_section_path_with_definition(self):
        assert _is_definition_passage("any text", "Section 2 — Definitions")

    def test_section_path_with_terms(self):
        assert _is_definition_passage("any text", "Part 1 > Terms and Meanings")

    def test_text_with_definitions_header(self):
        assert _is_definition_passage(
            "Definitions. As used in this chapter, the following terms have the following meaning:",
            "",
        )

    def test_text_with_means_pattern(self):
        assert _is_definition_passage(
            'The term "artificial intelligence" means a machine-based system.',
            "",
        )

    def test_text_with_as_used_in(self):
        assert _is_definition_passage(
            "As used in this act, the following definitions apply:",
            "",
        )

    def test_text_with_for_purposes_of(self):
        assert _is_definition_passage(
            "For the purposes of this section, the following terms have the meaning:",
            "",
        )

    def test_irrelevant_passage(self):
        assert not _is_definition_passage(
            "The deployer shall complete an impact assessment within 90 days.",
            "Section 5 — Requirements",
        )


class TestIsScopePassage:
    def test_section_path_with_scope(self):
        assert _is_scope_passage("any text", "Section 1 — Scope")

    def test_section_path_with_applicability(self):
        assert _is_scope_passage("any text", "Part 2 > Applicability")

    def test_text_with_applies_to(self):
        assert _is_scope_passage(
            "This act shall apply to any person who develops or deploys AI.",
            "",
        )

    def test_text_with_does_not_apply(self):
        assert _is_scope_passage(
            "This chapter does not apply to federal agencies.",
            "",
        )

    def test_text_with_exempt(self):
        assert _is_scope_passage(
            "Small businesses are exempt from the requirements of this section.",
            "",
        )

    def test_text_with_legislative_findings(self):
        assert _is_scope_passage(
            "Legislative findings and purpose. The Legislature finds that AI...",
            "",
        )

    def test_text_with_short_title(self):
        assert _is_scope_passage(
            "Short title. This act may be cited as the AI Accountability Act.",
            "",
        )

    def test_irrelevant_passage(self):
        assert not _is_scope_passage(
            "The deployer shall maintain records for 3 years.",
            "Section 8 — Record-Keeping",
        )


# ── Term extraction tests ───────────────────────────────────────────────────


class TestExtractDefinedTerms:
    def test_basic_means_pattern(self):
        text = '"artificial intelligence" means a machine-based system that can generate outputs.'
        terms = _extract_defined_terms(text)
        assert "artificial intelligence" in terms

    def test_multiple_terms(self):
        text = (
            '"deployer" means a person who deploys AI. '
            '"developer" means a person who develops AI. '
            '"high-risk AI system" refers to an AI system that...'
        )
        terms = _extract_defined_terms(text)
        assert len(terms) == 3
        assert "deployer" in terms
        assert "developer" in terms
        assert "high-risk AI system" in terms

    def test_curly_quotes(self):
        text = '\u201cautomated decision system\u201d means a computational process.'
        terms = _extract_defined_terms(text)
        assert "automated decision system" in terms

    def test_no_terms(self):
        text = "The deployer shall complete an impact assessment."
        terms = _extract_defined_terms(text)
        assert terms == []


# ── Full build tests ────────────────────────────────────────────────────────


class TestBuildBillContext:
    def _make_passage(self, text, section_path="", ordinal=0):
        return {
            "text_content": text,
            "section_path": section_path,
            "ordinal": ordinal,
        }

    def test_empty_passages(self):
        ctx = build_bill_context([])
        assert ctx["definitions"] == ""
        assert ctx["scope"] == ""
        assert ctx["defined_terms"] == []
        assert ctx["stats"]["total_passages"] == 0

    def test_definition_extraction(self):
        passages = [
            self._make_passage(
                'Definitions. "AI system" means a machine-based system. '
                '"deployer" means a person who uses an AI system.',
                "Section 2 — Definitions",
                ordinal=1,
            ),
            self._make_passage(
                "The deployer shall complete an impact assessment.",
                "Section 5 — Requirements",
                ordinal=4,
            ),
        ]
        ctx = build_bill_context(passages)
        assert "AI system" in ctx["definitions"]
        assert "deployer" in ctx["definitions"]
        assert ctx["stats"]["definition_passages"] == 1
        assert "AI system" in ctx["defined_terms"]
        assert "deployer" in ctx["defined_terms"]

    def test_scope_extraction(self):
        passages = [
            self._make_passage(
                "This act shall apply to any person who develops or deploys "
                "an AI system in this state.",
                "Section 1 — Scope",
                ordinal=0,
            ),
            self._make_passage(
                "The developer shall provide documentation.",
                "Section 4 — Developer Obligations",
                ordinal=3,
            ),
        ]
        ctx = build_bill_context(passages)
        assert "shall apply" in ctx["scope"]
        assert ctx["stats"]["scope_passages"] == 1

    def test_structure_outline(self):
        passages = [
            self._make_passage("text", "Section 1 — Short Title", ordinal=0),
            self._make_passage("text", "Section 2 — Definitions", ordinal=1),
            self._make_passage("text", "Section 3 — Scope", ordinal=2),
            self._make_passage("text", "Section 4 — Requirements", ordinal=3),
        ]
        ctx = build_bill_context(passages)
        assert "Section 1" in ctx["structure"]
        assert "Section 4" in ctx["structure"]

    def test_deduplicates_defined_terms(self):
        passages = [
            self._make_passage(
                '"AI system" means a machine-based system.',
                "Section 2 — Definitions",
                ordinal=1,
            ),
            self._make_passage(
                'For purposes of this section, "AI system" means the same thing.',
                "Section 5",
                ordinal=4,
            ),
        ]
        ctx = build_bill_context(passages)
        ai_terms = [t for t in ctx["defined_terms"] if t.lower() == "ai system"]
        assert len(ai_terms) == 1

    def test_truncation_budget(self):
        # Create a definitions passage that exceeds the budget
        long_def = '"term" means ' + "x" * (MAX_DEFINITIONS_CHARS + 500)
        passages = [
            self._make_passage(long_def, "Definitions", ordinal=0),
            self._make_passage('"other" means y.', "Definitions 2", ordinal=1),
        ]
        ctx = build_bill_context(passages)
        # Should be truncated to within budget
        assert len(ctx["definitions"]) <= MAX_DEFINITIONS_CHARS + 100  # some margin for separator + truncation marker

    def test_passage_dual_classification(self):
        """A passage can be both definition and scope."""
        text = (
            "Scope and Definitions. This act applies to deployers. "
            '"deployer" means a person who uses an AI system.'
        )
        passages = [self._make_passage(text, "Section 1 — Scope and Definitions", ordinal=0)]
        ctx = build_bill_context(passages)
        assert ctx["stats"]["definition_passages"] == 1
        assert ctx["stats"]["scope_passages"] == 1

    def test_preserves_document_order(self):
        passages = [
            self._make_passage('"B" means beta.', "Definitions", ordinal=5),
            self._make_passage('"A" means alpha.', "Definitions", ordinal=2),
        ]
        ctx = build_bill_context(passages)
        # ordinal=2 should come before ordinal=5
        assert ctx["definitions"].index("alpha") < ctx["definitions"].index("beta")
