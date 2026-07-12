"""Unit tests for SFH-1a (audit SF-04) — loop-detected truncation handling.

Bug: the provider's loop detection cuts model output at the third repetition
and returns stop_reason='loop' — a payload that by definition lost content.
But every truncation safeguard (the truncated flag that drives the EA2-3
tier cap + forced review, and the retry logic) keyed on stop_reason=='length'
alone, so loop-truncated garbage sailed through with full confidence
eligibility. These tests pin the fix: 'loop' now sets truncated=True, does
NOT trigger the doubled-budget retry (more budget just buys more repetition),
and the stop_reason is carried on the result for telemetry.

Pattern mirrors test_was_repaired_flag.py: real extract() with _call_llm mocked.
"""

from __future__ import annotations

from unittest.mock import patch

from src.core.llm_provider import LLMUsage

_VALID_RAW = (
    '{"extractions": [{"subject": "developer", "action": "comply",'
    ' "evidence_spans": []}]}'
)


def _make_obligation_agent():
    with patch("src.agents.base.get_extraction_provider"), \
         patch("src.core.model_config.get_config") as mock_cfg:
        mock_cfg.return_value.agents = {}
        from src.agents.obligation import ObligationAgent
        return ObligationAgent()


def _mock_call_llm(raw_text: str, stop_reason: str = "stop"):
    usage = LLMUsage(input_tokens=100, output_tokens=50)
    return (raw_text, usage, "test-model", stop_reason)


class TestLoopTruncationFlagged:
    def test_loop_stop_reason_sets_truncated(self):
        agent = _make_obligation_agent()
        with patch.object(
            agent, "_call_llm", return_value=_mock_call_llm(_VALID_RAW, "loop")
        ):
            result = agent.extract("A developer shall comply.", {})
        # The whole point of SF-04: loop cutoffs are truncation.
        assert result.truncated is True
        assert result.stop_reason == "loop"

    def test_length_stop_reason_still_truncated(self):
        agent = _make_obligation_agent()
        # max_retries=0 so the doubled-budget retry loop can't consume the call.
        agent.max_retries = 0
        with patch.object(
            agent, "_call_llm", return_value=_mock_call_llm(_VALID_RAW, "length")
        ):
            result = agent.extract("A developer shall comply.", {})
        assert result.truncated is True
        assert result.stop_reason == "length"

    def test_clean_stop_not_truncated(self):
        agent = _make_obligation_agent()
        with patch.object(
            agent, "_call_llm", return_value=_mock_call_llm(_VALID_RAW, "stop")
        ):
            result = agent.extract("A developer shall comply.", {})
        assert result.truncated is False
        assert result.stop_reason == "stop"

    def test_abstention_path_carries_loop_truncation(self):
        agent = _make_obligation_agent()
        raw = '{"detected": false, "reason": "nothing here"}'
        with patch.object(
            agent, "_call_llm", return_value=_mock_call_llm(raw, "loop")
        ):
            result = agent.extract("Some text.", {})
        assert result.abstention is not None
        assert result.truncated is True
        assert result.stop_reason == "loop"


class TestLoopDoesNotEscalateBudget:
    def test_loop_truncation_does_not_retry_with_doubled_budget(self):
        # A length cutoff retries with a doubled budget; a loop cutoff must
        # NOT — the model isn't budget-starved, it's repeating. Exactly one
        # provider call should happen.
        agent = _make_obligation_agent()
        calls = []

        def spy(prompt, attempt, call_max_tokens=None):
            calls.append(call_max_tokens)
            return _mock_call_llm(_VALID_RAW, "loop")

        with patch.object(agent, "_call_llm", side_effect=spy):
            result = agent.extract("A developer shall comply.", {})
        assert len(calls) == 1
        assert result.truncated is True

    def test_length_truncation_does_retry_with_doubled_budget(self):
        # Regression guard for the pre-existing length behavior: it must
        # still escalate (the SFH-1a change narrowed the condition — this
        # proves it didn't narrow too far). Escalation only fires when the
        # current budget is below the cap, so start explicitly small.
        agent = _make_obligation_agent()
        calls = []

        def spy(prompt, attempt, call_max_tokens=None):
            calls.append(call_max_tokens)
            return _mock_call_llm(_VALID_RAW, "length")

        with patch.object(agent, "_call_llm", side_effect=spy):
            agent.extract("A developer shall comply.", {}, call_max_tokens=256)
        # First call at 256, then at least one escalated retry at 512.
        assert len(calls) >= 2
        assert calls[0] == 256
        assert calls[1] == 512

    def test_loop_truncation_does_not_retry_even_with_small_budget(self):
        # The precise SFH-1a boundary: same small budget as the length test
        # above, but a loop cutoff — must NOT escalate.
        agent = _make_obligation_agent()
        calls = []

        def spy(prompt, attempt, call_max_tokens=None):
            calls.append(call_max_tokens)
            return _mock_call_llm(_VALID_RAW, "loop")

        with patch.object(agent, "_call_llm", side_effect=spy):
            result = agent.extract("A developer shall comply.", {}, call_max_tokens=256)
        assert calls == [256]
        assert result.truncated is True
