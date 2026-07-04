"""Unit tests for EA5-3 — enforcement-agent input targeting.

Bug: EnforcementAgent.get_prompt() sent the raw (truncated-to-128k-chars)
bill prefix, ignoring bill_context entirely. Enforcement/penalty sections
conventionally sit near the end of state bills, so any bill long enough to
be truncated lost exactly the fields (enforcing_body, max_civil_penalty_usd,
etc.) an auditor most needs (EA0-4 flags the truncation itself; this closes
the resulting bias).

Fix: `_build_bill_excerpt()` prefers bill_context["enforcement"] (built by
pattern-matching every passage in the bill regardless of position — no
prefix bias) plus a bounded raw tail as a catch-all, but only for bills long
enough for the distinction to matter; short bills (the corpus median) are
sent in full, unchanged from prior behavior.
"""

from __future__ import annotations

from unittest.mock import patch

from src.agents.enforcement_agent import _TAIL_CHARS, EnforcementAgent


def _make_agent():
    with patch("src.agents.bill_level_base.get_extraction_provider"), \
         patch("src.agents.bill_level_base.get_config") as mock_cfg:
        mock_cfg.return_value.agents = {}
        return EnforcementAgent()


class TestShortBillUnchanged:
    def test_bill_at_or_under_tail_budget_sent_whole(self):
        agent = _make_agent()
        full_text = "Section 1. " + "x" * (_TAIL_CHARS - 20)
        prompt = agent.get_prompt(full_text, {})
        assert full_text in prompt

    def test_short_bill_ignores_context_entirely(self):
        # Even if a (possibly stale) enforcement excerpt is present, a short
        # bill is sent whole rather than replaced by a partial excerpt.
        agent = _make_agent()
        full_text = "Section 1. A developer shall comply."
        prompt = agent.get_prompt(full_text, {"enforcement": "unrelated excerpt"})
        assert full_text in prompt
        assert "unrelated excerpt" not in prompt


class TestLongBillWithEnforcementContext:
    def test_enforcement_excerpt_and_tail_both_present(self):
        agent = _make_agent()
        head = "A" * (_TAIL_CHARS + 5000)
        tail_marker = "TAIL_MARKER_TEXT"
        full_text = head + tail_marker
        enforcement_excerpt = "Section 42. Civil penalty not to exceed $10,000 per violation."

        prompt = agent.get_prompt(full_text, {"enforcement": enforcement_excerpt})

        assert enforcement_excerpt in prompt
        assert tail_marker in prompt

    def test_prefix_beyond_tail_window_is_dropped(self):
        # The whole point of the fix: content further back than the tail
        # window, and not captured by the enforcement excerpt, is not what
        # gets sent — that's the truncation-bias case being closed.
        agent = _make_agent()
        prefix_marker = "PREFIX_MARKER_NEVER_SEEN"
        full_text = prefix_marker + ("B" * (_TAIL_CHARS + 5000))
        enforcement_excerpt = "Civil penalty of $5,000 per violation."

        prompt = agent.get_prompt(full_text, {"enforcement": enforcement_excerpt})

        assert prefix_marker not in prompt
        assert enforcement_excerpt in prompt

    def test_tail_is_exactly_the_last_tail_chars(self):
        agent = _make_agent()
        full_text = ("A" * (_TAIL_CHARS + 1000)) + ("Z" * _TAIL_CHARS)
        prompt = agent.get_prompt(full_text, {"enforcement": "some excerpt"})
        assert "Z" * _TAIL_CHARS in prompt


class TestLongBillWithoutEnforcementContext:
    def test_falls_back_to_tail_when_no_enforcement_excerpt(self):
        # Classifier found nothing — better to send the conventional
        # location for enforcement sections than a raw, biased prefix.
        agent = _make_agent()
        prefix_marker = "PREFIX_MARKER"
        tail_marker = "TAIL_MARKER"
        full_text = prefix_marker + ("C" * _TAIL_CHARS) + tail_marker
        prompt = agent.get_prompt(full_text, {})
        assert prefix_marker not in prompt
        assert tail_marker in prompt

    def test_falls_back_to_tail_when_context_is_none(self):
        agent = _make_agent()
        full_text = "D" * (_TAIL_CHARS + 5000)
        prompt = agent.get_prompt(full_text, None)
        assert len(prompt) > 0

    def test_empty_string_enforcement_excerpt_treated_as_absent(self):
        agent = _make_agent()
        full_text = "E" * (_TAIL_CHARS + 5000)
        prompt_empty = agent.get_prompt(full_text, {"enforcement": ""})
        prompt_missing = agent.get_prompt(full_text, {})
        assert prompt_empty == prompt_missing
