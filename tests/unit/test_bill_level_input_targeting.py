"""Unit tests for SFH-1j (audit B5) — input targeting for the two bill-level
agents EA5-3 didn't cover.

bill_level_base head-truncates long bills, but applicability exemptions and
effective-date clauses conventionally sit at the END of state bills — the
head cut biased against exactly the fields these agents exist to find.
Mirrors test_enforcement_agent_input_targeting.py.
"""

from __future__ import annotations

from src.agents.applicability_agent import ApplicabilityAgent
from src.agents.compliance_timeline_agent import ComplianceTimelineAgent

_HEAD_MARKER = "HEAD_MARKER_DEFINITIONS_XYZZY"
_TAIL_MARKER = "TAIL_MARKER_EXEMPTION_PLUGH"
_MIDDLE_MARKER = "MIDDLE_MARKER_FILLER_QUUX"


def _oversized_bill() -> str:
    # > 2×20k window so the excerpt builders engage. The middle marker sits
    # at the true center (outside both 20k windows) so its absence proves
    # the excerpt is bounded.
    head = _HEAD_MARKER + " lorem ipsum " * 500
    filler = " filler text " * 2000
    middle = filler + _MIDDLE_MARKER + filler
    tail = " closing provisions " * 500 + _TAIL_MARKER
    return head + middle + tail


class TestApplicabilityExcerpt:
    def test_short_bill_sent_in_full(self):
        agent = ApplicabilityAgent()
        text = "Short bill. " + _TAIL_MARKER
        prompt = agent.get_prompt(text, {})
        assert _TAIL_MARKER in prompt

    def test_oversized_bill_keeps_head_and_tail_drops_middle(self):
        agent = ApplicabilityAgent()
        prompt = agent.get_prompt(_oversized_bill(), {})
        # The bias being closed: tail content (exemptions) must survive.
        assert _TAIL_MARKER in prompt
        # Head (scope/definitions) also kept.
        assert _HEAD_MARKER in prompt
        # The dropped middle proves the excerpt is bounded, not the raw text.
        assert _MIDDLE_MARKER not in prompt

    def test_scope_sections_from_context_included(self):
        agent = ApplicabilityAgent()
        scope_text = "SCOPE_SECTION_MARKER: applies to deployers of ADS."
        prompt = agent.get_prompt(_oversized_bill(), {"scope": scope_text})
        assert "SCOPE_SECTION_MARKER" in prompt
        assert _TAIL_MARKER in prompt


class TestTimelineExcerpt:
    def test_short_bill_sent_in_full(self):
        agent = ComplianceTimelineAgent()
        text = "Short bill. Effective July 1, 2027."
        prompt = agent.get_prompt(text, {})
        assert "Effective July 1, 2027." in prompt

    def test_oversized_bill_keeps_tail_effective_dates(self):
        agent = ComplianceTimelineAgent()
        prompt = agent.get_prompt(_oversized_bill(), {})
        assert _TAIL_MARKER in prompt
        assert _HEAD_MARKER in prompt
        assert _MIDDLE_MARKER not in prompt
