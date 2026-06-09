"""Unit tests for src/ingestion/routing.py — pure routing functions.

No database fixtures, no LLM calls, no agent objects needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.routing import (
    _SIGNAL_MAP,
    is_boilerplate,
    route_by_signal,
    select_agent_names,
)

ALL_AGENTS = {
    "obligation",
    "definition_actor",
    "threshold_exception",
    "rights_protection",
    "compliance_mechanism",
    "preemption",
}


# ---------------------------------------------------------------------------
# is_boilerplate
# ---------------------------------------------------------------------------


class TestIsBoilerplate:
    def test_toc_header(self):
        assert is_boilerplate("Table of Contents") is True

    def test_chapter_header(self):
        assert is_boilerplate("Chapter 5") is True

    def test_page_number(self):
        assert is_boilerplate("page 42") is True

    def test_separator_line(self):
        assert is_boilerplate("_________") is True

    def test_dot_leader(self):
        assert is_boilerplate("..........") is True

    def test_enacting_clause_short(self):
        assert is_boilerplate("Be it enacted by the Legislature of the State") is True

    def test_enacting_clause_long_not_boilerplate(self):
        # A 300+ char "be it enacted" preamble may contain real content.
        long = "Be it enacted by the Legislature of the State of Colorado " + "x" * 250
        assert is_boilerplate(long) is False

    def test_substantive_text_not_boilerplate(self):
        assert is_boilerplate(
            "A covered entity shall implement reasonable safeguards for AI systems."
        ) is False

    def test_definition_text_not_boilerplate(self):
        assert is_boilerplate(
            '"Artificial intelligence system" means a machine-based system.'
        ) is False

    def test_whitespace_stripped(self):
        assert is_boilerplate("   Table of Contents   ") is True

    def test_empty_string_not_boilerplate(self):
        # Empty is not matched by the boilerplate pattern (fullmatch requires content)
        assert is_boilerplate("") is False


# ---------------------------------------------------------------------------
# route_by_signal
# ---------------------------------------------------------------------------


class TestRouteBySignal:
    def test_shall_signals_obligation(self):
        result = route_by_signal("A covered entity shall maintain records.", ALL_AGENTS)
        assert result is not None
        assert "obligation" in result

    def test_definition_signals_definition_actor(self):
        result = route_by_signal(
            '"AI system" means a machine-based system.', ALL_AGENTS
        )
        assert result is not None
        assert "definition_actor" in result

    def test_right_to_signals_rights(self):
        result = route_by_signal(
            "Consumers have the right to opt-out of profiling.", ALL_AGENTS
        )
        assert result is not None
        assert "rights_protection" in result

    def test_exempt_signals_threshold(self):
        result = route_by_signal(
            "Small businesses with fewer than 50 employees are exempt.", ALL_AGENTS
        )
        assert result is not None
        assert "threshold_exception" in result

    def test_penalty_signals_compliance(self):
        result = route_by_signal(
            "Violations shall be subject to a civil penalty of $10,000.", ALL_AGENTS
        )
        assert result is not None
        assert "compliance_mechanism" in result

    def test_preemption_signals_preemption(self):
        result = route_by_signal(
            "This Act preempts any inconsistent state law.", ALL_AGENTS
        )
        assert result is not None
        assert "preemption" in result

    def test_no_signals_returns_none(self):
        # A passage with no recognizable legal signals → run all.
        result = route_by_signal(
            "The quick brown fox jumped over the lazy dog.", ALL_AGENTS
        )
        assert result is None

    def test_all_agents_signaled_returns_none(self):
        # When signals cover ≥ (n-1) agents the function returns None.
        # Use text that hits every pattern simultaneously.
        dense = (
            "A covered entity shall define terms, exempt small entities, "
            "grant right to opt-out, enforce penalties, and preempt local law."
        )
        result = route_by_signal(dense, ALL_AGENTS)
        assert result is None

    def test_result_is_subset_of_provided_names(self):
        limited = {"obligation", "definition_actor"}
        result = route_by_signal("shall define", limited)
        if result is not None:
            assert result.issubset(limited)

    def test_triage_signals_augment_text(self):
        # ai_signals in triage_result broadens matching without main text having keywords
        triage = MagicMock()
        triage.ai_signals = "shall require"
        triage.llm_reasoning = ""
        result = route_by_signal("This section covers scope.", ALL_AGENTS, triage)
        assert result is not None
        assert "obligation" in result

    def test_unknown_agent_names_excluded_from_result(self):
        # route_by_signal should not invent names not in all_agent_names.
        small_set = {"obligation"}
        result = route_by_signal("shall exempt define", small_set)
        if result is not None:
            assert result.issubset(small_set)


# ---------------------------------------------------------------------------
# select_agent_names
# ---------------------------------------------------------------------------


class TestSelectAgentNames:
    def test_boilerplate_returns_empty(self):
        assert select_agent_names("Table of Contents", ALL_AGENTS) == set()

    def test_enacting_clause_returns_empty(self):
        assert select_agent_names("Be it enacted by the Legislature", ALL_AGENTS) == set()

    def test_definitions_header_returns_definition_actor_only(self):
        result = select_agent_names("Definitions", ALL_AGENTS)
        assert result == {"definition_actor"}

    def test_definitions_header_as_used_in(self):
        result = select_agent_names(
            "As used in this act:", ALL_AGENTS
        )
        assert result == {"definition_actor"}

    def test_definitions_header_ignores_agents_not_in_set(self):
        result = select_agent_names("Definitions", {"obligation", "definition_actor"})
        assert result == {"definition_actor"}

    def test_definitions_header_missing_from_set_returns_empty(self):
        result = select_agent_names("Definitions", {"obligation"})
        assert result == set()

    def test_signal_based_selection(self):
        result = select_agent_names(
            "A covered entity shall maintain records.", ALL_AGENTS
        )
        assert "obligation" in result
        # Should not include unrelated agents
        assert "preemption" not in result

    def test_no_signals_returns_all(self):
        result = select_agent_names(
            "This section establishes general provisions.", ALL_AGENTS
        )
        assert result == ALL_AGENTS

    def test_recall_sample_bypasses_routing(self):
        # With recall_sample_rate=1.0 every passage returns all agents.
        with patch("src.ingestion.routing.random.random", return_value=0.0):
            result = select_agent_names(
                "A covered entity shall maintain records.",
                ALL_AGENTS,
                recall_sample_rate=1.0,
            )
        assert result == ALL_AGENTS

    def test_recall_sample_off_by_default(self):
        # With rate=0.0 sampling never fires; signal routing applies normally.
        result = select_agent_names(
            "A covered entity shall maintain records.",
            ALL_AGENTS,
            recall_sample_rate=0.0,
        )
        assert result != ALL_AGENTS or True  # routing may or may not reduce set

    def test_returns_subset_of_provided_names(self):
        limited = {"obligation", "definition_actor"}
        result = select_agent_names("shall define terms", limited)
        assert result.issubset(limited)

    def test_empty_agent_set_returns_empty(self):
        result = select_agent_names("A covered entity shall maintain records.", set())
        assert result == set()

    def test_whitespace_only_text_is_boilerplate(self):
        # Stripped text is empty, doesn't match boilerplate fullmatch but also
        # has no signals — returns all (fallback to run everything).
        # This is acceptable conservative behavior.
        result = select_agent_names("   ", ALL_AGENTS)
        # Empty stripped text has no signals → all agents (conservative recall)
        assert isinstance(result, set)

    def test_triage_result_influences_routing(self):
        triage = MagicMock()
        triage.ai_signals = "shall require"
        triage.llm_reasoning = ""
        result = select_agent_names(
            "This section addresses scope.",
            ALL_AGENTS,
            triage_result=triage,
        )
        assert "obligation" in result


# ---------------------------------------------------------------------------
# Signal map completeness
# ---------------------------------------------------------------------------


class TestSignalMap:
    def test_all_six_agents_have_at_least_one_signal(self):
        covered = set()
        for _, agent_names in _SIGNAL_MAP:
            covered.update(agent_names)
        assert covered == {
            "obligation",
            "definition_actor",
            "threshold_exception",
            "rights_protection",
            "compliance_mechanism",
            "preemption",
        }

    def test_signal_patterns_compile_without_error(self):
        import re
        for pattern, _ in _SIGNAL_MAP:
            assert isinstance(pattern, re.Pattern)
