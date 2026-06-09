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
from src.core.circuit_breaker import CircuitBreakerTripped, FailureTracker
from src.ingestion.extractor import (
    CIRCUIT_BREAKER_THRESHOLD,
    MergedPassage,
    TokenUsageSummary,
    _content_hash,
    _confidence_to_priority,
    _select_agents_for_passage,
    _wrap_passages,
)
from src.schemas.extraction import AbstentionResult, ObligationPayload


class TestPromptLoader:
    def test_load_existing_template(self):
        """Should load the obligation template from prompts/."""
        # Clear the lru_cache before testing
        load_prompt_template.cache_clear()
        template = load_prompt_template("obligation")
        assert template is not None
        assert template["version"] in ("1.0", "1.1")
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
        assert version in ("1.0", "1.1")

    def test_get_template_version_missing(self):
        """Should return None for agents without templates."""
        load_prompt_template.cache_clear()
        version = get_template_version("no_such_agent")
        assert version is None

    def test_active_agent_templates_load(self):
        """All active agent templates should load successfully (ambiguity retired)."""
        load_prompt_template.cache_clear()
        for agent in ["obligation", "definition_actor", "threshold_exception"]:
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
        assert usage.clause_level_input_tokens == 0
        assert usage.bill_level_input_tokens == 0
        assert usage.abstention_count == 0
        assert usage.error_count == 0
        assert usage.extraction_item_count == 0
        assert usage.llm_call_count == 0

    def test_add_usage(self):
        """Legacy add() routes to add_clause() for backward compat."""
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

    def test_add_clause_updates_named_bucket(self):
        usage = TokenUsageSummary()
        usage.add_clause(500, 200)
        usage.add_clause(300, 100)
        assert usage.clause_level_input_tokens == 800
        assert usage.clause_level_output_tokens == 300
        assert usage.total_input_tokens == 800
        assert usage.total_output_tokens == 300
        assert usage.llm_call_count == 2
        assert usage.bill_level_input_tokens == 0

    def test_add_bill_level_updates_named_bucket(self):
        usage = TokenUsageSummary()
        usage.add_bill_level(1000, 400)
        assert usage.bill_level_input_tokens == 1000
        assert usage.bill_level_output_tokens == 400
        assert usage.total_input_tokens == 1000
        assert usage.clause_level_input_tokens == 0
        assert usage.llm_call_count == 1

    def test_mixed_clause_and_bill_level(self):
        usage = TokenUsageSummary()
        usage.add_clause(500, 200)
        usage.add_bill_level(1000, 400)
        assert usage.clause_level_input_tokens == 500
        assert usage.bill_level_input_tokens == 1000
        assert usage.total_input_tokens == 1500
        assert usage.total_tokens == 2100
        assert usage.llm_call_count == 2

    def test_invocation_counters(self):
        usage = TokenUsageSummary()
        usage.add_clause(100, 50)
        usage.abstention_count += 1
        usage.add_clause(200, 80)
        usage.error_count += 1
        usage.extraction_item_count += 3
        assert usage.llm_call_count == 2  # only non-error calls
        assert usage.abstention_count == 1
        assert usage.error_count == 1
        assert usage.extraction_item_count == 3


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

    @patch("src.agents.base.get_extraction_provider")
    def test_obligation_agent_uses_template(self, mock_get_provider):
        """ObligationAgent should load its template and use it."""
        load_prompt_template.cache_clear()
        from src.agents.obligation import ObligationAgent
        agent = ObligationAgent()
        assert agent._template is not None
        assert agent._template["version"] in ("1.0", "1.1")

    @patch("src.agents.base.get_extraction_provider")
    def test_system_prompt_from_template(self, mock_get_provider):
        """System prompt should come from template when available."""
        load_prompt_template.cache_clear()
        from src.agents.obligation import ObligationAgent
        agent = ObligationAgent()
        prompt = agent._resolve_system_prompt()
        assert "extractions" in prompt.lower() or "obligation" in prompt.lower()

    @patch("src.agents.base.get_extraction_provider")
    def test_extraction_prompt_renders_context(self, mock_get_provider):
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


# ---------------------------------------------------------------------------
# _wrap_passages (replaces _merge_short_passages)
# ---------------------------------------------------------------------------


class TestWrapPassages:
    def test_empty_records(self):
        assert _wrap_passages([]) == []

    def test_single_record(self):
        rec = MagicMock()
        rec.text_content = "Some legislative text here."
        rec.document_version_id = 1
        rec.ordinal = 0
        result = _wrap_passages([rec])
        assert len(result) == 1
        assert result[0].text == "Some legislative text here."
        assert result[0].source_records == [rec]

    def test_no_merging_even_for_short_adjacent(self):
        """Short adjacent passages should NOT be merged (merging disabled)."""
        r1 = MagicMock()
        r1.text_content = "Short A"
        r1.document_version_id = 1
        r1.ordinal = 0

        r2 = MagicMock()
        r2.text_content = "Short B"
        r2.document_version_id = 1
        r2.ordinal = 1

        result = _wrap_passages([r1, r2])
        assert len(result) == 2
        # Each passage wraps exactly one record
        assert len(result[0].source_records) == 1
        assert len(result[1].source_records) == 1

    def test_sorted_by_doc_version_and_ordinal(self):
        """Records should be sorted by (document_version_id, ordinal)."""
        r1 = MagicMock()
        r1.text_content = "Doc2 Passage"
        r1.document_version_id = 2
        r1.ordinal = 0

        r2 = MagicMock()
        r2.text_content = "Doc1 Passage"
        r2.document_version_id = 1
        r2.ordinal = 0

        result = _wrap_passages([r1, r2])
        assert result[0].text == "Doc1 Passage"
        assert result[1].text == "Doc2 Passage"


# ---------------------------------------------------------------------------
# CircuitBreakerTripped
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_threshold_constant(self):
        assert CIRCUIT_BREAKER_THRESHOLD == 10

    def test_exception_is_runtime_error(self):
        assert issubclass(CircuitBreakerTripped, RuntimeError)

    def test_exception_message(self):
        exc = CircuitBreakerTripped("test message")
        assert "test message" in str(exc)


# ---------------------------------------------------------------------------
# Batch custom_id format
# ---------------------------------------------------------------------------


class TestBatchCustomIdFormat:
    """Verify the new '--' delimiter produces unambiguous custom IDs."""

    def test_new_format_single_record(self):
        """Single record ID with simple agent name."""
        custom_id = "123--obligation"
        record_ids_str, _, agent_name = custom_id.partition("--")
        assert record_ids_str == "123"
        assert agent_name == "obligation"
        assert [int(r) for r in record_ids_str.split("-")] == [123]

    def test_new_format_compound_agent(self):
        """Compound agent name (threshold_exception) parses without ambiguity."""
        custom_id = "456--threshold_exception"
        record_ids_str, _, agent_name = custom_id.partition("--")
        assert record_ids_str == "456"
        assert agent_name == "threshold_exception"

    def test_new_format_multiple_records(self):
        """Multiple record IDs separated by single dashes."""
        custom_id = "10-20-30--definition_actor"
        record_ids_str, _, agent_name = custom_id.partition("--")
        assert record_ids_str == "10-20-30"
        assert agent_name == "definition_actor"
        assert [int(r) for r in record_ids_str.split("-")] == [10, 20, 30]

    def test_legacy_format_detected(self):
        """Legacy format without '--' should be detected."""
        custom_id = "123_obligation"
        assert "--" not in custom_id  # Falls to legacy parsing path


# ---------------------------------------------------------------------------
# Recall-safe agent selection (negative screening)
# ---------------------------------------------------------------------------


class TestRecallSafeAgentSelection:
    """Tests for the negative-screening agent selection.

    The system runs ALL agents by default and only excludes agents when the
    passage is definitively irrelevant (boilerplate, enacting clauses, etc.).
    This prevents false negatives from obligations phrased in non-standard ways.
    """

    @patch("src.agents.base.get_extraction_provider")
    def _make_agents(self, mock_provider) -> dict:
        """Create a minimal set of agents for testing."""
        from src.agents.compliance_mechanism import ComplianceMechanismAgent
        from src.agents.definition_actor import DefinitionActorAgent
        from src.agents.obligation import ObligationAgent
        from src.agents.rights_protection import RightsProtectionAgent
        from src.agents.threshold_exception import ThresholdExceptionAgent

        # ambiguity agent retired — findings embedded as interpretation_risks on obligation/rights
        return {
            "obligation": ObligationAgent(),
            "definition_actor": DefinitionActorAgent(),
            "threshold_exception": ThresholdExceptionAgent(),
            "rights_protection": RightsProtectionAgent(),
            "compliance_mechanism": ComplianceMechanismAgent(),
        }

    def test_standard_obligation_routes_obligation(self):
        """A passage with 'shall' should route to the obligation agent.
        definition_actor only runs on definition signals, not always-on."""
        agents = self._make_agents()
        text = "A developer shall implement cybersecurity protections."
        selected = _select_agents_for_passage(text, agents)
        assert "obligation" in selected
        assert len(selected) >= 1

    def test_nonstandard_obligation_is_expected_to(self):
        """'is expected to' doesn't contain shall/must — must still run obligation agent."""
        agents = self._make_agents()
        text = "A developer is expected to provide deployers with sufficient documentation."
        selected = _select_agents_for_passage(text, agents)
        assert "obligation" in selected, (
            "Obligation agent must run on 'is expected to' phrasing"
        )

    def test_nonstandard_obligation_has_duty_to(self):
        """'has a duty to' — must still run obligation agent."""
        agents = self._make_agents()
        text = "A developer has a duty to use reasonable care to protect consumers."
        selected = _select_agents_for_passage(text, agents)
        assert "obligation" in selected

    def test_nonstandard_obligation_is_responsible_for(self):
        """'is responsible for ensuring' — must still run obligation agent."""
        agents = self._make_agents()
        text = "A person who operates generative AI is responsible for ensuring clear disclosure."
        selected = _select_agents_for_passage(text, agents)
        assert "obligation" in selected

    def test_nonstandard_obligation_is_directed_to(self):
        """'is directed to' — must still run obligation agent."""
        agents = self._make_agents()
        text = "The Secretary of Commerce is directed to establish guidelines."
        selected = _select_agents_for_passage(text, agents)
        assert "obligation" in selected

    def test_nonstandard_obligation_it_is_the_policy(self):
        """'it is the policy of this State' — must still run obligation agent."""
        agents = self._make_agents()
        text = "It is the policy of this State that individuals retain the right to know when AI is used."
        selected = _select_agents_for_passage(text, agents)
        assert "obligation" in selected

    def test_nonstandard_obligation_is_to_notify(self):
        """'is to notify' — must still run obligation agent."""
        agents = self._make_agents()
        text = "An employer is to notify each candidate that an automated tool will be used."
        selected = _select_agents_for_passage(text, agents)
        assert "obligation" in selected

    def test_nonstandard_obligation_will_destroy(self):
        """'will destroy' — must still run obligation agent."""
        agents = self._make_agents()
        text = "Upon request by the applicant, the employer will destroy the video interview within 30 days."
        selected = _select_agents_for_passage(text, agents)
        assert "obligation" in selected

    def test_nonstandard_obligation_no_person_may(self):
        """'No person may' prohibition — must still run obligation agent."""
        agents = self._make_agents()
        text = "No developer of a covered model may make the model available for use unless the developer has shutdown capability."
        selected = _select_agents_for_passage(text, agents)
        assert "obligation" in selected

    def test_boilerplate_toc_excluded(self):
        """Table of contents entries should exclude all agents."""
        agents = self._make_agents()
        text = "Table of Contents"
        selected = _select_agents_for_passage(text, agents)
        assert len(selected) == 0

    def test_boilerplate_page_number_excluded(self):
        """Bare page numbers should exclude all agents."""
        agents = self._make_agents()
        text = "Page 42"
        selected = _select_agents_for_passage(text, agents)
        assert len(selected) == 0

    def test_enacting_clause_excluded(self):
        """Pure enacting clauses should exclude all agents."""
        agents = self._make_agents()
        text = "Be it enacted by the General Assembly of the State of Colorado"
        selected = _select_agents_for_passage(text, agents)
        assert len(selected) == 0

    def test_definitions_header_only_definition_agent(self):
        """A bare 'DEFINITIONS' header should only run the definition agent."""
        agents = self._make_agents()
        text = "As used in this act:"
        selected = _select_agents_for_passage(text, agents)
        assert "definition_actor" in selected
        assert len(selected) == 1

    def test_substantive_passage_runs_all_agents(self):
        """Regular legislative text should run all agents."""
        agents = self._make_agents()
        text = (
            "The developer of a high-risk AI system shall conduct an impact assessment "
            "annually. Any individual affected by a consequential decision has the right "
            "to appeal. This requirement does not apply to AI systems used solely for "
            "cybersecurity purposes."
        )
        selected = _select_agents_for_passage(text, agents)
        # Signal routing narrows to the agents with matched signals;
        # obligation (shall), rights_protection (right to), threshold_exception (does not apply)
        assert "obligation" in selected
        assert "rights_protection" in selected
        assert len(selected) >= 3

    def test_long_enacting_clause_not_excluded(self):
        """A long passage starting with enacting language should NOT be excluded
        because it likely contains substantive content beyond the clause."""
        agents = self._make_agents()
        text = (
            "Be it enacted by the legislature that developers of artificial intelligence "
            "systems shall implement bias testing, conduct annual audits, and maintain "
            "records of all training data used. Penalties for non-compliance include fines "
            "up to fifty thousand dollars per violation. " * 3
        )
        selected = _select_agents_for_passage(text, agents)
        # The passage is > 300 chars, so even though it starts with "Be it enacted",
        # it should NOT be excluded.  Signal routing narrows to obligation +
        # compliance_mechanism (shall, audits, penalties/fines).
        assert len(selected) >= 2
        assert "obligation" in selected
        assert "compliance_mechanism" in selected


# ---------------------------------------------------------------------------
# Preemption signals schema
# ---------------------------------------------------------------------------


class TestPreemptionSignals:
    """Tests for the preemption_signals field on ObligationPayload."""

    def test_preemption_signals_default_empty(self):
        """preemption_signals should default to empty list."""
        payload = ObligationPayload(
            subject="developer",
            modality="shall",
            action="comply",
        )
        assert payload.preemption_signals == []

    def test_preemption_signals_populated(self):
        """preemption_signals should accept a list of strings."""
        signals = [
            "This section does not preempt any federal law.",
            "Nothing in this chapter shall be construed to supersede federal requirements.",
        ]
        payload = ObligationPayload(
            subject="developer",
            modality="shall",
            action="comply",
            preemption_signals=signals,
        )
        assert len(payload.preemption_signals) == 2
        assert "preempt" in payload.preemption_signals[0]

    def test_preemption_signals_serialization(self):
        """preemption_signals should round-trip through JSON."""
        signals = ["Notwithstanding any state law to the contrary."]
        payload = ObligationPayload(
            subject="developer",
            modality="shall",
            action="comply",
            preemption_signals=signals,
        )
        data = payload.model_dump()
        assert data["preemption_signals"] == signals

        # Re-validate from dict
        restored = ObligationPayload.model_validate(data)
        assert restored.preemption_signals == signals


# ---------------------------------------------------------------------------
# Gold standard fixture validation
# ---------------------------------------------------------------------------


class TestGoldStandardFixtures:
    """Validate that all gold standard fixtures are well-formed."""

    def _load_fixtures(self):
        import json
        from pathlib import Path

        fixture_dir = Path("tests/fixtures/gold_standard")
        fixtures = []
        for f in sorted(fixture_dir.glob("*.json")):
            with open(f) as fp:
                fixtures.append((f.name, json.load(fp)))
        return fixtures

    def test_minimum_fixture_count(self):
        """Should have at least 25 fixtures (expanded coverage)."""
        fixtures = self._load_fixtures()
        assert len(fixtures) >= 25, f"Expected >= 25 fixtures, got {len(fixtures)}"

    def test_jurisdiction_diversity(self):
        """Should cover at least 5 distinct jurisdictions."""
        fixtures = self._load_fixtures()
        jurisdictions = {f[1].get("jurisdiction") for f in fixtures}
        jurisdictions.discard(None)
        assert len(jurisdictions) >= 5, (
            f"Expected >= 5 jurisdictions, got {len(jurisdictions)}: {jurisdictions}"
        )

    def test_fixture_structure(self):
        """Every fixture should have required fields."""
        fixtures = self._load_fixtures()
        for name, fixture in fixtures:
            assert "passage_id" in fixture, f"{name}: missing passage_id"
            assert "source_document" in fixture, f"{name}: missing source_document"
            assert "jurisdiction" in fixture, f"{name}: missing jurisdiction"
            assert "passage_text" in fixture, f"{name}: missing passage_text"
            assert "expected_extractions" in fixture, f"{name}: missing expected_extractions"
            assert len(fixture["passage_text"]) > 50, f"{name}: passage_text too short"

    def test_nonstandard_obligation_fixtures_exist(self):
        """Should have fixtures testing non-standard obligation phrasings
        that the old keyword screening would have missed."""
        fixtures = self._load_fixtures()
        # Look for fixtures with notes about non-standard phrasing
        nonstandard = [
            name for name, fix in fixtures
            if fix.get("expected_extractions", {}).get("notes", "")
            and "non-standard" in fix["expected_extractions"].get("notes", "").lower()
        ]
        assert len(nonstandard) >= 3, (
            f"Expected >= 3 non-standard phrasing fixtures, got {len(nonstandard)}"
        )

    def test_preemption_signal_fixtures_exist(self):
        """Should have at least 2 fixtures with preemption_signals."""
        fixtures = self._load_fixtures()
        with_preemption = []
        for name, fix in fixtures:
            obligation = fix.get("expected_extractions", {}).get("obligation")
            if obligation and obligation.get("preemption_signals"):
                with_preemption.append(name)
        assert len(with_preemption) >= 2, (
            f"Expected >= 2 fixtures with preemption_signals, got {len(with_preemption)}"
        )


# ---------------------------------------------------------------------------
# RR1a + RR1b: Idempotency fix regression tests
# ---------------------------------------------------------------------------


class TestRR1Idempotency:
    """Regression tests for RR1a (per-agent dedup) and RR1b (auto-purge gate).

    RR1a bug: existing_hashes was built for ALL agents whenever a passage had
    ANY extraction, so partially-extracted passages silently got no further
    agents run.  Fix: hash keyed on (agent_name, passage_text) only for agents
    that actually produced an extraction.

    RR1b bug: run_extraction() automatically deleted all extractions on every
    unlimited (limit=None) run.  Fix: purge is opt-in via purge=True.
    """

    def test_type_to_agent_reverse_map_is_complete(self):
        """Every ExtractionType in AGENT_EXTRACTION_TYPES maps back to its agent."""
        from src.ingestion.extractor import AGENT_EXTRACTION_TYPES

        type_to_agent = {
            ext_type.value: agent_name
            for agent_name, types in AGENT_EXTRACTION_TYPES.items()
            for ext_type in types
        }
        for agent_name, types in AGENT_EXTRACTION_TYPES.items():
            for ext_type in types:
                assert ext_type.value in type_to_agent, (
                    f"{ext_type.value} missing from reverse map"
                )
                assert type_to_agent[ext_type.value] == agent_name, (
                    f"{ext_type.value} maps to {type_to_agent[ext_type.value]}, expected {agent_name}"
                )

    def test_per_agent_dedup_only_marks_completed_agent(self):
        """When a passage has one obligation extraction, only the obligation
        agent hash should appear in existing_hashes — not all 6 agents.

        This is the core RR1a regression: the old code added hashes for every
        agent on any passage that had any extraction, preventing partially-
        extracted passages from getting their remaining agents filled in.
        """
        from src.db.models import ExtractionType
        from src.ingestion.extractor import AGENT_EXTRACTION_TYPES, _content_hash

        passage_text = "Developer shall disclose training data used for model training."

        _type_to_agent: dict[str, str] = {
            ext_type.value: agent_name
            for agent_name, types in AGENT_EXTRACTION_TYPES.items()
            for ext_type in types
        }

        # Simulate the fixed DB query returning one (text, extraction_type) row
        existing_hashes: set[str] = set()
        simulated_rows = [(passage_text, ExtractionType.obligation)]
        for text_content, ext_type in simulated_rows:
            ext_type_val = ext_type.value if hasattr(ext_type, "value") else str(ext_type)
            agent_name = _type_to_agent.get(ext_type_val)
            if agent_name:
                existing_hashes.add(_content_hash(agent_name, text_content))

        # Only the obligation agent's hash must be present
        assert _content_hash("obligation", passage_text) in existing_hashes

        # All other agents are NOT marked — they can still run on this passage
        for other_agent in [
            "definition_actor",
            "threshold_exception",
            "rights_protection",
            "compliance_mechanism",
            "preemption",
        ]:
            assert _content_hash(other_agent, passage_text) not in existing_hashes, (
                f"Agent '{other_agent}' was pre-empted even though it never ran"
            )

    def test_old_buggy_logic_would_block_all_agents(self):
        """Demonstrates the RR1a bug: old code pre-populated hashes for all
        agents on any passage with any extraction, blocking resumption."""
        from src.ingestion.extractor import _content_hash

        passage_text = "Developer shall disclose training data used for model training."
        all_agents = [
            "obligation", "definition_actor", "threshold_exception",
            "rights_protection", "compliance_mechanism", "preemption",
        ]

        # Old buggy approach: add hashes for all agents for any extracted passage
        buggy_hashes: set[str] = set()
        for agent_name in all_agents:
            buggy_hashes.add(_content_hash(agent_name, passage_text))

        # The bug: all 6 agents get blocked, even ones that never ran
        for agent_name in all_agents:
            assert _content_hash(agent_name, passage_text) in buggy_hashes

    def test_purge_defaults_to_false(self):
        """run_extraction must default purge=False — never purge automatically."""
        import inspect
        from src.ingestion.extractor import run_extraction

        sig = inspect.signature(run_extraction)
        assert "purge" in sig.parameters, "purge parameter missing from run_extraction"
        assert sig.parameters["purge"].default is False, (
            "purge must default to False — auto-purge is forbidden"
        )

    def test_per_agent_dedup_uses_all_types_for_agent(self):
        """An agent that produces multiple extraction types (e.g. obligation
        produces obligation/timeline/enforcement) is considered 'done' for a
        passage if ANY of its output types is in the DB."""
        from src.db.models import ExtractionType
        from src.ingestion.extractor import AGENT_EXTRACTION_TYPES, _content_hash

        passage_text = "The deadline for compliance is January 1, 2026."

        _type_to_agent: dict[str, str] = {
            ext_type.value: agent_name
            for agent_name, types in AGENT_EXTRACTION_TYPES.items()
            for ext_type in types
        }

        # Passage has a timeline extraction (produced by the obligation agent)
        existing_hashes: set[str] = set()
        simulated_rows = [(passage_text, ExtractionType.timeline)]
        for text_content, ext_type in simulated_rows:
            ext_type_val = ext_type.value if hasattr(ext_type, "value") else str(ext_type)
            agent_name = _type_to_agent.get(ext_type_val)
            if agent_name:
                existing_hashes.add(_content_hash(agent_name, text_content))

        # The obligation agent (which produced timeline) is considered done
        assert _content_hash("obligation", passage_text) in existing_hashes

        # Other agents are NOT blocked
        for other_agent in [
            "definition_actor", "threshold_exception",
            "rights_protection", "compliance_mechanism", "preemption",
        ]:
            assert _content_hash(other_agent, passage_text) not in existing_hashes
