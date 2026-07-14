"""Tests for QA-10: dropping junk "definitions" of conditional-enactment
boilerplate (rides with plan Phase 2, but mechanical — no ratification
needed, unlike QA-9a's relevance rules).

California's contingent-enactment clauses ("Section 1.6 of this bill
incorporates amendments to Section 647 of the Penal Code proposed by this
bill, Assembly Bill 1962, and Assembly Bill 1874...") structurally resemble
a definition to a small model — a code-section citation followed by prose
about it — and get mis-extracted as one. SB 926 ids 234/235 are the real
run's instances of this; the fixture text below is copied verbatim from the
committed source (output/law_texts/TMP-CA-AMENDMENTOFCAL.txt).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.definition_actor import (
    DefinitionActorAgent,
    _is_bare_citation_term,
    _is_conditional_enactment_boilerplate,
)


@pytest.fixture()
def agent():
    with patch.object(DefinitionActorAgent, "__init__", lambda self: None):
        a = DefinitionActorAgent()
    return a


BOILERPLATE_TEXT = (
    "Section 1.6 of this bill incorporates amendments to Section 647 of "
    "the Penal Code proposed by this bill, Assembly Bill 1962, and "
    "Assembly Bill 1874. That section of this bill shall only become "
    "operative if (1) all three bills are enacted and become effective on "
    "or before January 1, 2025, (2) each bill amends Section 647 of the "
    "Penal Code, (3) Senate Bill 1414 is not enacted or as enacted does "
    "not amend that section, and (4) this bill is enacted after Assembly "
    "Bill 1962 and Assembly Bill 1874, in which case Sections 1, 1.1, "
    "1.2, 1.3, 1.4, 1.5, and 1.7 of this bill shall not become operative."
)


class TestBareCitationTermDetection:
    def test_penal_code_section_citation_is_bare(self):
        assert _is_bare_citation_term("Section 647 of the Penal Code") is True

    def test_government_code_section_citation_is_bare(self):
        assert _is_bare_citation_term("Section 84504.2 of the Government Code") is True

    def test_sec_abbreviation_is_bare(self):
        assert _is_bare_citation_term("Sec. 3344 of the Civil Code") is True

    def test_real_defined_term_is_not_bare(self):
        assert _is_bare_citation_term("automated decision system") is False

    def test_term_mentioning_a_section_in_passing_is_not_bare(self):
        # Only a term that IS entirely a citation should match — one that
        # merely references a section as part of a real definition must not.
        assert _is_bare_citation_term("violation of Section 647 of the Penal Code") is False

    def test_empty_term_is_not_bare(self):
        assert _is_bare_citation_term("") is False


class TestConditionalEnactmentBoilerplateDetection:
    def test_real_sb926_boilerplate_detected(self):
        assert _is_conditional_enactment_boilerplate(BOILERPLATE_TEXT) is True

    def test_real_definition_not_flagged(self):
        text = (
            "'Artificial intelligence system' means a machine-based system "
            "that generates outputs such as predictions, content, "
            "recommendations, or decisions."
        )
        assert _is_conditional_enactment_boilerplate(text) is False

    def test_effective_date_clause_alone_not_flagged(self):
        # An ordinary effective-date sentence shouldn't trip this — only
        # the specific contingent-enactment phrasing should.
        text = "This act shall take effect on January 1, 2026."
        assert _is_conditional_enactment_boilerplate(text) is False


class TestPostprocessDropsBoilerplate:
    def test_sb926_id234_style_bare_citation_term_dropped(self, agent):
        result = {
            "term": "Section 647 of the Penal Code",
            "definition_text": BOILERPLATE_TEXT,
            "scope": None,
            "actors": [],
            "framework_refs": [],
        }
        assert agent._postprocess_extraction(result, passage=BOILERPLATE_TEXT) is None

    def test_boilerplate_definition_text_alone_dropped_even_with_ok_term(self, agent):
        # Belt-and-suspenders: even if a model attaches a plausible-looking
        # term to the boilerplate body, the text itself is disqualifying.
        result = {
            "term": "incorporation of amendments",
            "definition_text": BOILERPLATE_TEXT,
            "scope": None,
            "actors": [],
            "framework_refs": [],
        }
        assert agent._postprocess_extraction(result, passage=BOILERPLATE_TEXT) is None

    def test_genuine_definition_survives(self, agent):
        passage = (
            "For purposes of this section, 'computer-generated image' means "
            "any photo realistic image, digital image, or electronic image "
            "created by a computer."
        )
        result = {
            "term": "computer-generated image",
            "definition_text": (
                "any photo realistic image, digital image, or electronic "
                "image created by a computer"
            ),
            "scope": None,
            "actors": [],
            "framework_refs": [],
        }
        out = agent._postprocess_extraction(result, passage=passage)
        assert out is not None
        assert out["term"] == "computer-generated image"
