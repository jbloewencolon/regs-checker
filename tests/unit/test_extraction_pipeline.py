"""Unit tests for the AI extraction pipeline enhancements.

Tests cover:
  - Multi-extraction support (multiple items per passage)
  - Prompt template loading and rendering
  - ExtractionResult dataclass and token tracking
  - Content-hash deduplication
  - TokenUsageSummary aggregation
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents.base import BaseExtractionAgent, ExtractionResult
from src.agents.prompt_loader import load_prompt_template, render_prompt, get_template_version
from src.ingestion.extractor import (
    TokenUsageSummary,
    _content_hash,
    _confidence_to_priority,
)
from src.schemas.extraction import AbstentionResult


class TestPromptLoader:
    def test_load_existing_template(self):
        """Should load the obligation template from prompts/."""
        # Clear the lru_cache before testing
        load_prompt_template.cache_clear()
        template = load_prompt_template("obligation")
        assert template is not None
        assert template["version"] == "1.0"
        assert template["agent"] == "obligation"
        assert "system_prompt" in template
        assert "extraction_prompt" in template

    def test_load_nonexistent_template(self):
        """Should return None for agents without template files."""
        load_prompt_template.cache_clear()
        template = load_prompt_template("nonexistent_agent_xyz")
        assert template is None

    def test_render_prompt_basic(self):
        """Should render Jinja2 template with context variables."""
        template_str = "Extract from: {{ passage }}\nDoc: {{ document_title }}"
        result = render_prompt(template_str, {
            "passage": "Some legal text",
            "document_title": "CO SB205",
        })
        assert "Some legal text" in result
        assert "CO SB205" in result

    def test_render_prompt_optional_vars(self):
        """Should handle optional/missing context variables gracefully."""
        template_str = "{% if jurisdiction %}JURISDICTION: {{ jurisdiction }}{% endif %}"
        # With value
        result = render_prompt(template_str, {"jurisdiction": "CO"})
        assert "CO" in result
        # Without value
        result = render_prompt(template_str, {})
        assert result == ""

    def test_get_template_version(self):
        """Should return version string from template."""
        load_prompt_template.cache_clear()
        version = get_template_version("obligation")
        assert version == "1.0"

    def test_get_template_version_missing(self):
        """Should return None for agents without templates."""
        load_prompt_template.cache_clear()
        version = get_template_version("no_such_agent")
        assert version is None

    def test_all_four_templates_load(self):
        """All 4 agent templates should load successfully."""
        load_prompt_template.cache_clear()
        for agent in ["obligation", "definition_actor", "threshold_exception", "ambiguity"]:
            template = load_prompt_template(agent)
            assert template is not None, f"Template for {agent} should load"
            assert "version" in template, f"Template for {agent} missing version"
            assert "system_prompt" in template, f"Template for {agent} missing system_prompt"
            assert "extraction_prompt" in template, f"Template for {agent} missing extraction_prompt"


class TestExtractionResult:
    def test_abstention_result(self):
        """ExtractionResult with abstention should have empty extractions."""
        result = ExtractionResult(
            extractions=[],
            abstention=AbstentionResult(detected=False, reason="No obligations found"),
            input_tokens=100,
            output_tokens=50,
            prompt_hash="abc123",
            model_id="claude-sonnet-4-20250514",
            template_version="1.0",
        )
        assert len(result.extractions) == 0
        assert result.abstention is not None
        assert result.input_tokens == 100
        assert result.output_tokens == 50

    def test_multi_extraction_result(self):
        """ExtractionResult should hold multiple extractions."""
        result = ExtractionResult(
            extractions=[
                {"subject": "developer", "modality": "shall", "action": "disclose"},
                {"subject": "deployer", "modality": "must", "action": "notify"},
            ],
            abstention=None,
            input_tokens=500,
            output_tokens=300,
            prompt_hash="def456",
            model_id="claude-sonnet-4-20250514",
            template_version="1.0",
        )
        assert len(result.extractions) == 2
        assert result.abstention is None


class TestTokenUsageSummary:
    def test_initial_state(self):
        usage = TokenUsageSummary()
        assert usage.total_input_tokens == 0
        assert usage.total_output_tokens == 0
        assert usage.total_tokens == 0
        assert usage.total_calls == 0

    def test_add_usage(self):
        usage = TokenUsageSummary()
        usage.add(100, 50)
        usage.add(200, 100)
        assert usage.total_input_tokens == 300
        assert usage.total_output_tokens == 150
        assert usage.total_tokens == 450
        assert usage.total_calls == 2

    def test_total_tokens_property(self):
        usage = TokenUsageSummary(total_input_tokens=1000, total_output_tokens=500, total_calls=4)
        assert usage.total_tokens == 1500


class TestContentHash:
    def test_deterministic(self):
        """Same inputs should produce same hash."""
        h1 = _content_hash("obligation", "some text")
        h2 = _content_hash("obligation", "some text")
        assert h1 == h2

    def test_different_agents_different_hash(self):
        """Different agent names should produce different hashes."""
        h1 = _content_hash("obligation", "same text")
        h2 = _content_hash("ambiguity", "same text")
        assert h1 != h2

    def test_different_text_different_hash(self):
        """Different passage text should produce different hashes."""
        h1 = _content_hash("obligation", "text A")
        h2 = _content_hash("obligation", "text B")
        assert h1 != h2

    def test_hash_length(self):
        """Hash should be 24 hex chars."""
        h = _content_hash("test", "test")
        assert len(h) == 24


class TestConfidenceToPriority:
    def test_tier_a(self):
        assert _confidence_to_priority("A") == 0

    def test_tier_b(self):
        assert _confidence_to_priority("B") == 1

    def test_tier_c(self):
        assert _confidence_to_priority("C") == 2

    def test_tier_d(self):
        assert _confidence_to_priority("D") == 3

    def test_unknown_tier(self):
        assert _confidence_to_priority("X") == 1


class TestBaseAgentTemplateIntegration:
    """Test that agents correctly resolve prompts from templates vs inline."""

    @patch("src.agents.base.anthropic.Anthropic")
    def test_obligation_agent_uses_template(self, mock_anthropic):
        """ObligationAgent should load its template and use it."""
        load_prompt_template.cache_clear()
        from src.agents.obligation import ObligationAgent
        agent = ObligationAgent()
        assert agent._template is not None
        assert agent._template["version"] == "1.0"

    @patch("src.agents.base.anthropic.Anthropic")
    def test_system_prompt_from_template(self, mock_anthropic):
        """System prompt should come from template when available."""
        load_prompt_template.cache_clear()
        from src.agents.obligation import ObligationAgent
        agent = ObligationAgent()
        prompt = agent._resolve_system_prompt()
        assert "extractions" in prompt.lower() or "obligation" in prompt.lower()

    @patch("src.agents.base.anthropic.Anthropic")
    def test_extraction_prompt_renders_context(self, mock_anthropic):
        """Extraction prompt should render passage and context variables."""
        load_prompt_template.cache_clear()
        from src.agents.obligation import ObligationAgent
        agent = ObligationAgent()
        prompt = agent._resolve_extraction_prompt(
            "Test passage text here",
            {"document_title": "CO SB205", "jurisdiction": "CO"},
        )
        assert "Test passage text here" in prompt
        assert "CO SB205" in prompt
