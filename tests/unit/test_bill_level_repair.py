"""Unit tests for BillLevelAgent JSON repair helpers.

Covers _repair_json() (static method) and _parse_json_payload() (instance
method) in src/agents/bill_level_base.py.  These helpers share the same
surface contract as BaseExtractionAgent but live independently in
bill_level_base and are tested separately.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


def _make_stub_agent():
    """Create a minimal concrete BillLevelAgent for testing repair methods."""
    from src.agents.bill_level_base import BillLevelAgent

    class _StubAgent(BillLevelAgent):
        agent_name = "test_stub"

        def get_prompt(self, full_text: str, context: dict) -> str:
            return "prompt"

        def parse_response(self, raw: str) -> dict:
            return self._parse_json_payload(raw)

    with patch("src.agents.bill_level_base.get_extraction_provider"), \
         patch("src.agents.bill_level_base.get_config") as mock_cfg:
        mock_cfg.return_value.agents = {}
        return _StubAgent()


# ---------------------------------------------------------------------------
# _repair_json (static method — can test without instantiation)
# ---------------------------------------------------------------------------


class TestRepairJson:
    """Tests for BillLevelAgent._repair_json."""

    @staticmethod
    def repair(text: str) -> str:
        from src.agents.bill_level_base import BillLevelAgent
        return BillLevelAgent._repair_json(text)

    def test_valid_json_unchanged(self):
        text = '{"enforcing_body": "AG", "max_civil_penalty_usd": 5000}'
        assert self.repair(text) == text

    def test_markdown_fence_stripped(self):
        text = '```json\n{"a": 1}\n```'
        cleaned = self.repair(text)
        parsed = json.loads(cleaned)
        assert parsed == {"a": 1}

    def test_markdown_fence_no_lang_stripped(self):
        text = '```\n{"a": 1}\n```'
        cleaned = self.repair(text)
        assert json.loads(cleaned) == {"a": 1}

    def test_trailing_comma_in_object(self):
        text = '{"a": 1, "b": 2,}'
        cleaned = self.repair(text)
        assert json.loads(cleaned) == {"a": 1, "b": 2}

    def test_trailing_comma_in_nested_array(self):
        text = '{"key_deadlines": [{"action": "Register",},]}'
        cleaned = self.repair(text)
        parsed = json.loads(cleaned)
        assert len(parsed["key_deadlines"]) == 1
        assert parsed["key_deadlines"][0]["action"] == "Register"

    def test_control_chars_stripped(self):
        # \x01 through \x08 are invalid control chars in JSON strings
        text = '{"text": "content\x01with\x07control"}'
        cleaned = self.repair(text)
        assert json.loads(cleaned)["text"] == "contentwithcontrol"

    def test_empty_string_unchanged(self):
        assert self.repair("") == ""

    def test_whitespace_only_stripped(self):
        assert self.repair("   ") == ""

    def test_plain_text_unchanged(self):
        text = "This is not JSON at all"
        assert self.repair(text) == text


# ---------------------------------------------------------------------------
# _parse_json_payload (instance method)
# ---------------------------------------------------------------------------


class TestParseJsonPayload:
    """Tests for BillLevelAgent._parse_json_payload error paths."""

    @pytest.fixture(autouse=True)
    def agent(self):
        self.agent = _make_stub_agent()

    def test_clean_json_returned(self):
        raw = '{"max_civil_penalty_usd": 10000, "enforcing_body": "AG"}'
        result = self.agent._parse_json_payload(raw)
        assert result["max_civil_penalty_usd"] == 10000

    def test_markdown_fenced_json_parsed(self):
        raw = "```json\n{\"enforcing_body\": \"Department of Commerce\"}\n```"
        result = self.agent._parse_json_payload(raw)
        assert result["enforcing_body"] == "Department of Commerce"

    def test_trailing_comma_json_parsed(self):
        raw = '{"enforcing_body": "AG", "cure_period_days": 30,}'
        result = self.agent._parse_json_payload(raw)
        assert result["cure_period_days"] == 30

    def test_first_object_extracted_from_prefix_text(self):
        raw = 'Here is the JSON output:\n{"enforcing_body": "AG"}\nExtra text after.'
        result = self.agent._parse_json_payload(raw)
        assert result["enforcing_body"] == "AG"

    def test_unparseable_raises_value_error(self):
        with pytest.raises(ValueError, match="Could not parse JSON"):
            self.agent._parse_json_payload("this is not json at all!!!!")

    def test_empty_string_raises_value_error(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            self.agent._parse_json_payload("")

    def test_nested_object_preserved(self):
        raw = json.dumps({
            "size_thresholds": {
                "revenue_usd": 25000000,
                "employee_count": 50,
            }
        })
        result = self.agent._parse_json_payload(raw)
        assert result["size_thresholds"]["revenue_usd"] == 25000000

    def test_array_value_preserved(self):
        raw = json.dumps({
            "covered_entity_types": ["developer", "deployer"],
        })
        result = self.agent._parse_json_payload(raw)
        assert result["covered_entity_types"] == ["developer", "deployer"]


# ---------------------------------------------------------------------------
# extract_bill retry loop
# ---------------------------------------------------------------------------


class TestExtractBillRetryLoop:
    """Tests for BillLevelAgent.extract_bill retry behavior on parse failure."""

    @pytest.fixture(autouse=True)
    def agent(self):
        self.agent = _make_stub_agent()

    def test_success_on_first_attempt(self):
        """Should return BillLevelResult on clean output."""
        from src.agents.bill_level_base import BillLevelResult

        good_raw = '{"enforcing_body": "AG", "max_civil_penalty_usd": 5000}'

        with patch.object(self.agent, "_call_llm") as mock_llm:
            mock_llm.return_value = (good_raw, 100, 20, "test-model", False)
            result = self.agent.extract_bill("Some bill text")

        assert isinstance(result, BillLevelResult)
        assert result.payload.get("enforcing_body") == "AG"
        assert result.model_id == "test-model"
        assert mock_llm.call_count == 1

    def test_retry_on_parse_failure(self):
        """Should retry once when parse_response raises, then succeed."""
        from src.agents.bill_level_base import BillLevelResult

        bad_raw = "INVALID JSON {"
        good_raw = '{"enforcing_body": "AG"}'

        with patch.object(self.agent, "_call_llm") as mock_llm:
            mock_llm.side_effect = [
                (bad_raw, 100, 10, "test-model", False),   # attempt 0: bad
                (good_raw, 100, 15, "test-model", False),  # attempt 1: good
            ]
            result = self.agent.extract_bill("Some bill text")

        assert result.payload.get("enforcing_body") == "AG"
        assert mock_llm.call_count == 2

    def test_exhausted_retries_returns_error_payload(self):
        """Should return payload with _error key after all retries fail."""
        from src.agents.bill_level_base import BillLevelResult

        with patch.object(self.agent, "_call_llm") as mock_llm:
            mock_llm.return_value = ("NOT JSON", 100, 5, "test-model", False)
            result = self.agent.extract_bill("Some bill text")

        assert "_error" in result.payload
        assert result.model_id == ""
        # max_retries=1 → 2 attempts total
        assert mock_llm.call_count == 2

    def test_truncated_flag_propagated(self):
        """BillLevelResult.truncated reflects stop_reason from LLM call."""
        from src.agents.bill_level_base import BillLevelResult

        raw = '{"enforcing_body": "AG"}'

        with patch.object(self.agent, "_call_llm") as mock_llm:
            mock_llm.return_value = (raw, 200, 1024, "test-model", True)  # truncated=True
            result = self.agent.extract_bill("Some bill text")

        assert result.truncated is True

    def test_text_truncated_to_max_chars(self):
        """Input text beyond MAX_BILL_TEXT_CHARS is silently truncated."""
        from src.agents.bill_level_base import MAX_BILL_TEXT_CHARS

        long_text = "x" * (MAX_BILL_TEXT_CHARS + 10_000)
        captured_prompts: list[str] = []

        def fake_call(prompt: str, attempt: int):
            captured_prompts.append(prompt)
            return ('{"a": 1}', 1000, 20, "model", False)

        with patch.object(self.agent, "_call_llm", side_effect=fake_call):
            self.agent.extract_bill(long_text)

        # Prompt is built from truncated text; full text wasn't passed through
        assert len(captured_prompts) == 1
