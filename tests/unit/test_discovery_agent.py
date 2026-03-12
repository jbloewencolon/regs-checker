"""Tests for discovery agent (local LLM bill classification and metadata extraction)."""

from unittest.mock import MagicMock, patch

import pytest

from src.agents.discovery import (
    ClassificationResult,
    DiscoveryAgent,
    MetadataResult,
)
from src.core.llm_provider import LLMResponse, LLMUsage


def _make_response(text: str, input_tokens: int = 50, output_tokens: int = 20) -> LLMResponse:
    return LLMResponse(
        text=text,
        usage=LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        model_id="local:llama-3.1-8b",
        stop_reason="stop",
    )


class TestDiscoveryAgentClassify:
    @patch("src.agents.discovery.get_discovery_provider")
    def test_classify_ai_bill(self, mock_get_provider):
        mock_provider = MagicMock()
        mock_provider.model_id = "local:llama-3.1-8b"
        mock_provider.call.return_value = _make_response(
            '{"is_ai_legislation": true, "confidence": 0.95, '
            '"reasoning": "Bill regulates automated decision-making", '
            '"ai_topics": ["automated decision-making", "algorithmic accountability"]}'
        )
        mock_get_provider.return_value = mock_provider

        agent = DiscoveryAgent()
        result = agent.classify_bill("AN ACT concerning automated decision systems...")

        assert isinstance(result, ClassificationResult)
        assert result.is_ai_legislation is True
        assert result.confidence == 0.95
        assert "automated decision-making" in result.ai_topics
        assert result.input_tokens == 50
        assert result.model_id == "local:llama-3.1-8b"

    @patch("src.agents.discovery.get_discovery_provider")
    def test_classify_non_ai_text(self, mock_get_provider):
        mock_provider = MagicMock()
        mock_provider.model_id = "local:llama-3.1-8b"
        mock_provider.call.return_value = _make_response(
            '{"is_ai_legislation": false, "confidence": 0.9, '
            '"reasoning": "This is a tax bill", "ai_topics": []}'
        )
        mock_get_provider.return_value = mock_provider

        agent = DiscoveryAgent()
        result = agent.classify_bill("AN ACT to amend the tax code...")

        assert result.is_ai_legislation is False
        assert result.ai_topics == []

    @patch("src.agents.discovery.get_discovery_provider")
    def test_classify_truncates_long_text(self, mock_get_provider):
        mock_provider = MagicMock()
        mock_provider.model_id = "local:llama-3.1-8b"
        mock_provider.call.return_value = _make_response(
            '{"is_ai_legislation": false, "confidence": 0.5, '
            '"reasoning": "Unclear", "ai_topics": []}'
        )
        mock_get_provider.return_value = mock_provider

        agent = DiscoveryAgent()
        long_text = "x" * 10000
        agent.classify_bill(long_text, max_chars=4000)

        call_args = mock_provider.call.call_args
        user_prompt = call_args.kwargs.get("user_prompt", call_args[1].get("user_prompt", ""))
        # The prefix "Classify the following text:\n\n" + 4000 chars
        assert len(user_prompt) <= 4100

    @patch("src.agents.discovery.get_discovery_provider")
    def test_classify_handles_malformed_json(self, mock_get_provider):
        mock_provider = MagicMock()
        mock_provider.model_id = "local:llama-3.1-8b"
        mock_provider.call.return_value = _make_response("not valid json at all")
        mock_get_provider.return_value = mock_provider

        agent = DiscoveryAgent()
        result = agent.classify_bill("some text")

        # Should default to safe values
        assert result.is_ai_legislation is False
        assert result.confidence == 0.0

    @patch("src.agents.discovery.get_discovery_provider")
    def test_classify_handles_code_fences(self, mock_get_provider):
        mock_provider = MagicMock()
        mock_provider.model_id = "local:llama-3.1-8b"
        mock_provider.call.return_value = _make_response(
            '```json\n{"is_ai_legislation": true, "confidence": 0.8, '
            '"reasoning": "AI bill", "ai_topics": ["AI"]}\n```'
        )
        mock_get_provider.return_value = mock_provider

        agent = DiscoveryAgent()
        result = agent.classify_bill("AI regulation text")

        assert result.is_ai_legislation is True
        assert result.confidence == 0.8


class TestDiscoveryAgentMetadata:
    @patch("src.agents.discovery.get_discovery_provider")
    def test_extract_metadata(self, mock_get_provider):
        mock_provider = MagicMock()
        mock_provider.model_id = "local:llama-3.1-8b"
        mock_provider.call.return_value = _make_response(
            '{"title": "Colorado AI Act", "jurisdiction_code": "CO", '
            '"bill_number": "SB 205", "effective_date": "2026-02-01", '
            '"status": "enacted", "ai_scope": "Regulates high-risk AI systems", '
            '"key_requirements": ["Impact assessments", "Transparency disclosures"]}'
        )
        mock_get_provider.return_value = mock_provider

        agent = DiscoveryAgent()
        result = agent.extract_metadata("Colorado SB 205 text...")

        assert isinstance(result, MetadataResult)
        assert result.title == "Colorado AI Act"
        assert result.jurisdiction_code == "CO"
        assert result.bill_number == "SB 205"
        assert result.status == "enacted"
        assert len(result.key_requirements) == 2

    @patch("src.agents.discovery.get_discovery_provider")
    def test_extract_metadata_with_nulls(self, mock_get_provider):
        mock_provider = MagicMock()
        mock_provider.model_id = "local:llama-3.1-8b"
        mock_provider.call.return_value = _make_response(
            '{"title": "Unknown Bill", "jurisdiction_code": null, '
            '"bill_number": null, "effective_date": null, '
            '"status": "unknown", "ai_scope": null, "key_requirements": []}'
        )
        mock_get_provider.return_value = mock_provider

        agent = DiscoveryAgent()
        result = agent.extract_metadata("Some bill text")

        assert result.title == "Unknown Bill"
        assert result.jurisdiction_code is None
        assert result.bill_number is None
        assert result.key_requirements == []

    @patch("src.agents.discovery.get_discovery_provider")
    def test_extract_metadata_handles_malformed_json(self, mock_get_provider):
        mock_provider = MagicMock()
        mock_provider.model_id = "local:llama-3.1-8b"
        mock_provider.call.return_value = _make_response("broken json {{{")
        mock_get_provider.return_value = mock_provider

        agent = DiscoveryAgent()
        result = agent.extract_metadata("text")

        # Should return None/empty defaults
        assert result.title is None
        assert result.key_requirements == []


class TestParseJson:
    def test_strips_code_fences(self):
        text = '```json\n{"key": "value"}\n```'
        result = DiscoveryAgent._parse_json(text)
        assert result == {"key": "value"}

    def test_plain_json(self):
        result = DiscoveryAgent._parse_json('{"a": 1}')
        assert result == {"a": 1}

    def test_invalid_json_returns_empty(self):
        result = DiscoveryAgent._parse_json("not json")
        assert result == {}
