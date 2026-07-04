"""Unit tests for EA0-4 — bill-level input-truncation visibility.

Bug: ``BillLevelAgent.extract_bill()`` silently truncated ``full_text`` to
``MAX_BILL_TEXT_CHARS`` (128,000) with no trace in the stored payload —
only output truncation (``finish_reason=length``) was ever recorded via
``truncated``. Enforcement sections conventionally sit at the end of state
bills, so truncation bias hits the enforcement agent's fields hardest,
and it happened invisibly.

These tests exercise ``extract_bill()`` with ``_call_llm`` mocked so no
live LLM is required.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.agents.bill_level_base import MAX_BILL_TEXT_CHARS


def _make_agent():
    with patch("src.agents.bill_level_base.get_extraction_provider"), \
         patch("src.agents.bill_level_base.get_config") as mock_cfg:
        mock_cfg.return_value.agents = {}
        from src.agents.enforcement_agent import EnforcementAgent
        return EnforcementAgent()


_CLEAN_RESPONSE = json.dumps({
    "enforcing_body": "Attorney General",
    "max_civil_penalty_usd": 10000,
    "penalty_per": "violation",
    "cure_period_days": 30,
    "private_right_of_action": True,
    "criminal_penalties": False,
    "criminal_penalty_description": None,
    "enforcement_text": "Violations shall be punished.",
})


class TestInputWithinBudget:
    def test_short_bill_not_flagged_truncated(self):
        agent = _make_agent()
        with patch.object(
            agent, "_call_llm",
            return_value=(_CLEAN_RESPONSE, 100, 50, "test-model", False),
        ):
            result = agent.extract_bill("Short bill text." * 10)
        assert result.input_truncated is False
        assert result.chars_dropped == 0
        assert "_input_truncated" not in result.payload
        assert "_chars_dropped" not in result.payload

    def test_exactly_at_budget_not_flagged(self):
        agent = _make_agent()
        full_text = "x" * MAX_BILL_TEXT_CHARS
        with patch.object(
            agent, "_call_llm",
            return_value=(_CLEAN_RESPONSE, 100, 50, "test-model", False),
        ):
            result = agent.extract_bill(full_text)
        assert result.input_truncated is False
        assert result.chars_dropped == 0


class TestInputOverBudget:
    def test_long_bill_flagged_in_result_and_payload(self):
        agent = _make_agent()
        full_text = "x" * (MAX_BILL_TEXT_CHARS + 5000)
        with patch.object(
            agent, "_call_llm",
            return_value=(_CLEAN_RESPONSE, 100, 50, "test-model", False),
        ):
            result = agent.extract_bill(full_text)
        assert result.input_truncated is True
        assert result.chars_dropped == 5000
        assert result.payload["_input_truncated"] is True
        assert result.payload["_chars_dropped"] == 5000
        # The genuine extracted fields are still present alongside the flag.
        assert result.payload["enforcing_body"] == "Attorney General"

    def test_prompt_only_sees_truncated_text(self):
        # get_prompt must receive the truncated slice, not the full text —
        # this is existing behavior; the test pins it so the truncation
        # flag can never drift out of sync with what was actually sent.
        agent = _make_agent()
        full_text = "A" * MAX_BILL_TEXT_CHARS + "B" * 100
        captured = {}
        original_get_prompt = agent.get_prompt

        def _spy_get_prompt(full_text_arg, context):
            captured["text"] = full_text_arg
            return original_get_prompt(full_text_arg, context)

        with patch.object(agent, "get_prompt", side_effect=_spy_get_prompt), \
             patch.object(
                 agent, "_call_llm",
                 return_value=(_CLEAN_RESPONSE, 100, 50, "test-model", False),
             ):
            agent.extract_bill(full_text)
        assert len(captured["text"]) == MAX_BILL_TEXT_CHARS
        assert "B" not in captured["text"]

    def test_failure_path_still_reports_input_truncation(self):
        # Even when every retry fails to parse, the truncation flag must
        # survive into the failure payload — an operator debugging a bill
        # with no enforcement data extracted needs to know whether the
        # relevant section was ever sent to the model at all.
        agent = _make_agent()
        full_text = "x" * (MAX_BILL_TEXT_CHARS + 1000)
        with patch.object(
            agent, "_call_llm",
            return_value=("not valid json", 100, 50, "test-model", False),
        ):
            result = agent.extract_bill(full_text)
        assert "_error" in result.payload
        assert result.input_truncated is True
        assert result.chars_dropped == 1000
        assert result.payload["_input_truncated"] is True
        assert result.payload["_chars_dropped"] == 1000

    def test_does_not_overwrite_model_produced_field_of_same_name(self):
        # Defensive: if a model ever emitted a field literally named
        # _input_truncated/_chars_dropped, our flag must not clobber it
        # (setdefault semantics).
        agent = _make_agent()
        full_text = "x" * (MAX_BILL_TEXT_CHARS + 1000)
        weird_response = json.dumps({
            "enforcing_body": "Attorney General",
            "max_civil_penalty_usd": None,
            "penalty_per": None,
            "cure_period_days": None,
            "private_right_of_action": None,
            "criminal_penalties": None,
            "criminal_penalty_description": None,
            "enforcement_text": None,
            "_input_truncated": "model-said-this",
        })
        with patch.object(
            agent, "_call_llm",
            return_value=(weird_response, 100, 50, "test-model", False),
        ):
            result = agent.extract_bill(full_text)
        assert result.payload["_input_truncated"] == "model-said-this"
