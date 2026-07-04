"""Unit tests for EA2-3 — was_repaired detection in BaseExtractionAgent.extract().

Bug/gap: a raw LLM response that required JSON repair (control-char strip,
trailing-comma removal, truncated-JSON salvage, stringified-object unwrap,
etc.) to become parseable was indistinguishable, downstream, from a response
that was clean JSON from the start — both just produced a normal-looking
extraction. A heavily-repaired payload may have lost real content during
salvage (e.g. a dropped exception clause), so this should be visible and
should cap the resulting confidence tier (see test_confidence.py::
TestCapAtTierC and the extractor.py wiring), not disappear into "just
another successful extraction."

These tests exercise the real `extract()` method end-to-end with
`_call_llm` mocked, so the actual code path (strip fences -> strip think
blocks -> repair -> parse) is exercised, not just the static repair helper
in isolation (already covered by test_json_repair.py).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.llm_provider import LLMUsage


def _make_obligation_agent():
    with patch("src.agents.base.get_extraction_provider"), \
         patch("src.core.model_config.get_config") as mock_cfg:
        mock_cfg.return_value.agents = {}
        from src.agents.obligation import ObligationAgent
        return ObligationAgent()


def _mock_call_llm(raw_text: str, stop_reason: str = "stop"):
    usage = LLMUsage(input_tokens=100, output_tokens=50)
    return (raw_text, usage, "test-model", stop_reason)


class TestCleanResponseNotFlaggedRepaired:
    def test_clean_json_is_not_repaired(self):
        agent = _make_obligation_agent()
        raw = (
            '{"extractions": [{"subject": "developer", "action": "comply",'
            ' "evidence_spans": []}]}'
        )
        with patch.object(agent, "_call_llm", return_value=_mock_call_llm(raw)):
            result = agent.extract("A developer shall comply.", {})
        assert result.was_repaired is False

    def test_clean_json_with_whitespace_padding_not_flagged(self):
        # Leading/trailing whitespace alone (no real repair) must not count —
        # pre_repair is stripped before comparison specifically to avoid this
        # false positive.
        agent = _make_obligation_agent()
        raw = (
            '  \n  {"extractions": [{"subject": "developer", "action": "comply",'
            ' "evidence_spans": []}]}  \n  '
        )
        with patch.object(agent, "_call_llm", return_value=_mock_call_llm(raw)):
            result = agent.extract("A developer shall comply.", {})
        assert result.was_repaired is False

    def test_abstention_path_also_reports_repair_status(self):
        agent = _make_obligation_agent()
        raw = '{"detected": false, "reason": "no obligation here"}'
        with patch.object(agent, "_call_llm", return_value=_mock_call_llm(raw)):
            result = agent.extract("Some unrelated text.", {})
        assert result.abstention is not None
        assert result.was_repaired is False


class TestMalformedResponseFlaggedRepaired:
    def test_trailing_comma_triggers_repair_flag(self):
        agent = _make_obligation_agent()
        raw = (
            '{"extractions": [{"subject": "developer", "action": "comply",'
            ' "evidence_spans": [],}]}'
        )
        with patch.object(agent, "_call_llm", return_value=_mock_call_llm(raw)):
            result = agent.extract("A developer shall comply.", {})
        assert result.was_repaired is True

    def test_control_characters_trigger_repair_flag(self):
        agent = _make_obligation_agent()
        raw = (
            '{"extractions": [{"subject": "developer", "action": "comply\x02here",'
            ' "evidence_spans": []}]}'
        )
        with patch.object(agent, "_call_llm", return_value=_mock_call_llm(raw)):
            result = agent.extract("A developer shall comply.", {})
        assert result.was_repaired is True

    def test_abstention_path_flags_repair_when_needed(self):
        agent = _make_obligation_agent()
        raw = '{"detected": false, "reason": "no obligation here",}'
        with patch.object(agent, "_call_llm", return_value=_mock_call_llm(raw)):
            result = agent.extract("Some unrelated text.", {})
        assert result.abstention is not None
        assert result.was_repaired is True


class TestTruncationAndRepairAreIndependentFlags:
    def test_truncated_but_not_repaired(self):
        # finish_reason=length with otherwise-clean (already-complete) JSON:
        # truncated=True, was_repaired=False — these are different defects.
        agent = _make_obligation_agent()
        raw = (
            '{"extractions": [{"subject": "developer", "action": "comply",'
            ' "evidence_spans": []}]}'
        )
        with patch.object(agent, "_call_llm", return_value=_mock_call_llm(raw, stop_reason="length")):
            result = agent.extract("A developer shall comply.", {})
        assert result.truncated is True
        assert result.was_repaired is False

    def test_repaired_but_not_truncated(self):
        agent = _make_obligation_agent()
        raw = (
            '{"extractions": [{"subject": "developer", "action": "comply",'
            ' "evidence_spans": [],}]}'
        )
        with patch.object(agent, "_call_llm", return_value=_mock_call_llm(raw, stop_reason="stop")):
            result = agent.extract("A developer shall comply.", {})
        assert result.truncated is False
        assert result.was_repaired is True
