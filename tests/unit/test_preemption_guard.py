"""Tests for QA-6: deterministic guard against preemption over-firing.

The 2026-07-13 run produced 81 preemption signals from
meta/llama-3.1-8b-instruct, dominated by three junk patterns (49/81 rejected
on replay of these rules):

  - the law's OWN state codes reported as a cross_state_conflict
    ("This passage references the Penal Code, which may conflict with
    federal laws or other states' laws" — CA SB 926, 17+ rows);
  - the prompt's example authorities parroted into related_authority
    ("Dec 2025 Federal EO on AI", "US Constitution Art. I § 8");
  - self-negating descriptions ("...does not appear to conflict with
    federal law" emitted as a signal instead of an abstention).

The credibility rules themselves are tested in test_legal_context.py; this
file covers the agent integration: fabricated preemption_language is nulled
when absent from the passage, non-credible signals are dropped (return
None), and the base extract() loop skips dropped items. Fixtures are real
payloads from that run, lightly trimmed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.preemption import PreemptionAgent
from src.core.llm_provider import LLMUsage


@pytest.fixture()
def agent():
    with patch.object(PreemptionAgent, "__init__", lambda self: None):
        a = PreemptionAgent()
    return a


SAVINGS_CLAUSE = (
    "This act does not alter any rights, obligations, or immunities created "
    "by 47 U.S.C. § 230"
)


class TestPostprocessDrops:
    def test_own_state_citation_dropped(self, agent):
        # SB 926 id 200 — the dominant junk pattern.
        result = {
            "conflict_type": "cross_state_conflict",
            "description": "This passage references the Penal Code, which "
            "may conflict with federal laws or other states' laws.",
            "related_authority": "California Penal Code",
            "preemption_language": None,
            "cross_law_refs": [],
            "jurisdiction": "CA",
        }
        assert agent._postprocess_extraction(result, passage="unused") is None

    def test_self_negating_signal_dropped(self, agent):
        # SB 926 id 178.
        result = {
            "conflict_type": "cross_state_conflict",
            "description": "This passage references the Welfare and "
            "Institutions Code and does not appear to conflict with "
            "federal law.",
            "related_authority": "California Welfare and Institutions Code",
            "preemption_language": None,
            "cross_law_refs": [],
            "jurisdiction": "CA",
        }
        assert agent._postprocess_extraction(result, passage="unused") is None

    def test_grounded_savings_clause_kept(self, agent):
        # AL HB172 id 684.
        passage = f"Section 7. {SAVINGS_CLAUSE}, federal law, or a rule."
        result = {
            "conflict_type": "federal_preemption",
            "description": "Savings clause preserving §230 immunity.",
            "related_authority": "47 U.S.C. § 230",
            "preemption_language": SAVINGS_CLAUSE,
            "cross_law_refs": [],
            "jurisdiction": "AL",
        }
        out = agent._postprocess_extraction(result, passage=passage)
        assert out is not None
        assert out["preemption_language"] == SAVINGS_CLAUSE

    def test_fabricated_preemption_language_nulled(self, agent):
        """A clause that is not in the passage cannot rubber-stamp
        credibility — it gets nulled, then the signal is assessed on its
        remaining anchors (here: none → dropped)."""
        result = {
            "conflict_type": "federal_preemption",
            "description": "This law may be preempted by federal laws.",
            "related_authority": None,
            "preemption_language": "nothing in this act shall preempt "
            "any federal law",
            "cross_law_refs": [],
            "jurisdiction": "CA",
        }
        out = agent._postprocess_extraction(
            result, passage="Completely unrelated passage text."
        )
        assert out is None

    def test_fabricated_language_but_real_citation_kept_without_clause(
        self, agent
    ):
        """Nulling a fabricated clause must not kill a signal that stands on
        a concrete federal citation."""
        result = {
            "conflict_type": "cross_state_conflict",
            "description": "Conflicts with Title VII obligations under "
            "42 U.S.C. § 2000e.",
            "related_authority": "42 U.S.C. § 2000e et seq.",
            "preemption_language": "made-up clause not in the passage",
            "cross_law_refs": [],
            "jurisdiction": "CA",
        }
        out = agent._postprocess_extraction(
            result, passage="Completely unrelated passage text."
        )
        assert out is not None
        assert out["preemption_language"] is None


def _make_preemption_agent():
    with patch("src.agents.base.get_extraction_provider"), \
         patch("src.core.model_config.get_config") as mock_cfg:
        mock_cfg.return_value.agents = {}
        return PreemptionAgent()


class TestExtractLoopSkipsDropped:
    """The base extract() loop treats a None from _postprocess_extraction
    as a drop (QA-6) instead of crashing or storing it."""

    def test_mixed_batch_keeps_only_credible(self):
        agent = _make_preemption_agent()
        passage = f"Section 7. {SAVINGS_CLAUSE}, federal law, or a rule."
        raw = (
            '{"extractions": ['
            '{"conflict_type": "federal_preemption",'
            f' "description": "Savings clause preserving 230 immunity.",'
            f' "related_authority": "47 U.S.C. § 230",'
            f' "preemption_language": "{SAVINGS_CLAUSE}",'
            ' "jurisdiction": "AL", "evidence_spans": []},'
            '{"conflict_type": "cross_state_conflict",'
            ' "description": "This passage references the Penal Code, which'
            ' may conflict with federal laws or other states\' laws.",'
            ' "related_authority": "California Penal Code",'
            ' "jurisdiction": "CA", "evidence_spans": []}'
            "]}"
        )
        usage = LLMUsage(input_tokens=100, output_tokens=50)
        with patch.object(
            agent, "_call_llm", return_value=(raw, usage, "test-model", "stop")
        ):
            result = agent.extract(passage, {})
        assert len(result.extractions) == 1
        assert result.extractions[0]["conflict_type"] == "federal_preemption"

    def test_all_dropped_batch_yields_no_extractions(self):
        agent = _make_preemption_agent()
        raw = (
            '{"extractions": ['
            '{"conflict_type": "cross_state_conflict",'
            ' "description": "This passage references the Welfare and'
            ' Institutions Code and does not appear to conflict with'
            ' federal law.",'
            ' "related_authority": "California Welfare and Institutions Code",'
            ' "jurisdiction": "CA", "evidence_spans": []}'
            "]}"
        )
        usage = LLMUsage(input_tokens=100, output_tokens=50)
        with patch.object(
            agent, "_call_llm", return_value=(raw, usage, "test-model", "stop")
        ):
            result = agent.extract("Some passage.", {})
        assert result.extractions == []
