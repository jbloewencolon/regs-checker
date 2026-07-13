"""Tests for QA-2: deterministic grounding guards on definition_actor output.

The 2026-07-12 run showed meta/llama-3.1-8b-instruct filling the schema's
optional arrays with invented content on AR HB1877:
  - a "Developer" actor (responsibility: "use of artificial intelligence")
    attached to the definition of "Computer generated", which names no actor;
  - a NIST framework_ref attached to the definitions of "Indistinguishable"
    and "Computer generated", when NIST appears only in the separate
    "Adversarial testing" definition in the same passage.

The guard drops actors/framework_refs whose names are not grounded in the
definition context (term + definition_text + scope). Test fixtures below are
the real payloads from that run, lightly trimmed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.definition_actor import DefinitionActorAgent


@pytest.fixture()
def agent():
    with patch.object(DefinitionActorAgent, "__init__", lambda self: None):
        a = DefinitionActorAgent()
    return a


ADVERSARIAL_TESTING_DEF = (
    "red teaming or another activity or exercise conducted in a controlled "
    "environment and in collaboration with an artificial intelligence developer "
    "to identify a potential adverse behavior or outcome of a model or system, "
    "and to conduct other structured evaluation methods as set forth by the "
    "National Institute of Standards and Technology."
)


class TestActorGrounding:
    def test_hallucinated_developer_actor_dropped(self, agent):
        """Real row 20: 'Developer' invented on the 'Computer generated' definition."""
        result = {
            "term": "Computer generated",
            "definition_text": (
                "produced, adapted, or modified, in whole or in part, through "
                "the use of artificial intelligence"
            ),
            "scope": "Arkansas Code § 5-27-302",
            "actors": [{
                "actor_name": "Developer",
                "actor_type": "Developer",
                "responsibilities": ["use of artificial intelligence"],
            }],
            "framework_refs": [],
        }
        out = agent._postprocess_extraction(result, passage="unused")
        assert out["actors"] == []

    def test_grounded_actor_kept(self, agent):
        """Real row 25: 'artificial intelligence developer' IS in the definition."""
        result = {
            "term": "Adversarial testing",
            "definition_text": ADVERSARIAL_TESTING_DEF,
            "scope": "Arkansas Code § 5-27-601(18)",
            "actors": [{
                "actor_name": "artificial intelligence developer",
                "actor_type": "Developer",
                "responsibilities": ["collaboration"],
            }],
            "framework_refs": [],
        }
        out = agent._postprocess_extraction(result, passage="unused")
        assert len(out["actors"]) == 1

    def test_ordinary_person_grounded_via_definition_text(self, agent):
        result = {
            "term": "Indistinguishable",
            "definition_text": (
                "a visual or print medium that is such that an ordinary person "
                "viewing the visual or print medium would conclude that the "
                "visual or print medium depicts an actual child"
            ),
            "scope": None,
            "actors": [{"actor_name": "Ordinary person", "actor_type": "Observer",
                        "responsibilities": []}],
            "framework_refs": [],
        }
        out = agent._postprocess_extraction(result, passage="unused")
        assert len(out["actors"]) == 1

    def test_actor_grounded_via_term_only(self, agent):
        """AZ SB1359: the 'CREATOR' definition's actor is the term itself."""
        result = {
            "term": "CREATOR",
            "definition_text": (
                "ANY PERSON THAT USES ARTIFICIAL INTELLIGENCE OR OTHER DIGITAL "
                "TECHNOLOGY TO GENERATE SYNTHETIC MEDIA."
            ),
            "scope": "FOR THE PURPOSES OF THIS SECTION",
            "actors": [{"actor_name": "Creator", "actor_type": "PERSON",
                        "responsibilities": []}],
            "framework_refs": [],
        }
        out = agent._postprocess_extraction(result, passage="unused")
        assert len(out["actors"]) == 1

    def test_plural_singular_tolerance(self, agent):
        result = {
            "term": "High-risk system",
            "definition_text": "a system marketed to deployers for consequential decisions",
            "scope": None,
            "actors": [{"actor_name": "deployer", "actor_type": None,
                        "responsibilities": []}],
            "framework_refs": [],
        }
        out = agent._postprocess_extraction(result, passage="unused")
        assert len(out["actors"]) == 1


class TestFrameworkRefGrounding:
    def test_cross_contaminated_nist_ref_dropped(self, agent):
        """Real row 30: NIST attached to 'Indistinguishable', which never
        mentions it — contamination from a sibling definition."""
        result = {
            "term": "Indistinguishable",
            "definition_text": (
                "a visual or print medium that is such that an ordinary person "
                "viewing the visual or print medium would conclude that the "
                "visual or print medium depicts an actual child engaged in the "
                "conduct depicted."
            ),
            "scope": "visual or print medium",
            "actors": [],
            "framework_refs": [{
                "framework_name": "National Institute of Standards and Technology",
                "section_or_standard": "structured evaluation methods",
                "relationship": "referenced",
            }],
        }
        out = agent._postprocess_extraction(result, passage="unused")
        assert out["framework_refs"] == []

    def test_genuine_nist_ref_kept(self, agent):
        """Real row 32: the 'Adversarial testing' definition names NIST."""
        result = {
            "term": "Adversarial testing",
            "definition_text": ADVERSARIAL_TESTING_DEF,
            "scope": "artificial intelligence developer",
            "actors": [],
            "framework_refs": [{
                "framework_name": "National Institute of Standards and Technology",
                "section_or_standard": "structured evaluation methods",
                "relationship": "referenced",
            }],
        }
        out = agent._postprocess_extraction(result, passage="unused")
        assert len(out["framework_refs"]) == 1

    def test_acronym_only_ref_needs_acronym_in_text(self, agent):
        result = {
            "term": "Risk framework",
            "definition_text": "the framework published by NIST for AI risk management",
            "scope": None,
            "actors": [],
            "framework_refs": [{"framework_name": "NIST",
                                "section_or_standard": None, "relationship": None}],
        }
        out = agent._postprocess_extraction(result, passage="unused")
        assert len(out["framework_refs"]) == 1


class TestGuardEdgeCases:
    def test_empty_arrays_pass_through(self, agent):
        result = {"term": "X", "definition_text": "means Y", "scope": None,
                  "actors": [], "framework_refs": []}
        out = agent._postprocess_extraction(result, passage="unused")
        assert out["actors"] == [] and out["framework_refs"] == []

    def test_empty_grounding_context_keeps_everything(self, agent):
        """No term/definition/scope to ground against — don't drop blindly."""
        result = {"term": "", "definition_text": "", "scope": None,
                  "actors": [{"actor_name": "Developer", "actor_type": None,
                              "responsibilities": []}],
                  "framework_refs": []}
        out = agent._postprocess_extraction(result, passage="unused")
        assert len(out["actors"]) == 1

    def test_base_agent_hook_is_noop(self):
        from src.agents.base import BaseExtractionAgent
        result = {"anything": 1}
        assert BaseExtractionAgent._postprocess_extraction(
            None, result, "passage"
        ) is result
