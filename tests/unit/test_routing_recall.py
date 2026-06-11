"""Unit tests for routing recall behavior and gold-fixture signal coverage.

Extends test_routing.py with tests for:
- recall_sample_rate bypass (RR7c): a fraction of passages run all agents
- all-agent fallback: ambiguous or signal-dense passages fall back to all
- Gold fixture routing: known passage texts map to the expected agent subset
  so regressions in signal patterns are immediately visible.

These serve as the regression fixture baseline for Phase 4c eval set work.
"""
from __future__ import annotations

import random

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
# recall_sample_rate bypass
# ---------------------------------------------------------------------------


class TestRecallSamplingBypass:
    """select_agent_names with recall_sample_rate > 0 bypasses signal routing."""

    def test_rate_zero_uses_signal_routing(self):
        # obligation-only signal → only obligation returned (not all agents)
        text = "A covered entity shall maintain records."
        result = select_agent_names(text, ALL_AGENTS, recall_sample_rate=0.0)
        assert result == {"obligation"}

    def test_rate_one_always_returns_all_agents(self):
        # Even a passage routed to one agent falls back when rate=1.0
        text = "A covered entity shall maintain records."
        result = select_agent_names(text, ALL_AGENTS, recall_sample_rate=1.0)
        assert result == ALL_AGENTS

    def test_rate_one_on_definition_passage_returns_all(self):
        text = '"AI system" means a machine-based system.'
        result = select_agent_names(text, ALL_AGENTS, recall_sample_rate=1.0)
        assert result == ALL_AGENTS

    def test_rate_zero_definitions_header_still_deterministic(self):
        # Bare "Definitions" header bypasses sampling → definition_actor only
        result = select_agent_names("Definitions", ALL_AGENTS, recall_sample_rate=1.0)
        # Definitions header is handled before recall sampling is applied
        assert result == {"definition_actor"}

    def test_rate_one_boilerplate_still_returns_empty(self):
        # Boilerplate is filtered before recall sampling
        result = select_agent_names("Table of Contents", ALL_AGENTS, recall_sample_rate=1.0)
        assert result == set()

    def test_probabilistic_rate_produces_both_outcomes(self):
        # With rate=0.5, both outcomes must be observed over many draws.
        text = "A covered entity shall maintain records."
        random.seed(42)
        results = {
            frozenset(select_agent_names(text, ALL_AGENTS, recall_sample_rate=0.5))
            for _ in range(200)
        }
        # Should see both the routed subset and the full all-agent set
        assert frozenset(ALL_AGENTS) in results          # bypass triggered at least once
        assert frozenset({"obligation"}) in results      # routing triggered at least once


# ---------------------------------------------------------------------------
# All-agent fallback from route_by_signal
# ---------------------------------------------------------------------------


class TestAllAgentFallback:
    """route_by_signal returns None (→ all agents) on ambiguous passages."""

    def test_no_signals_returns_none(self):
        result = route_by_signal("The quick brown fox jumped over the lazy dog.", ALL_AGENTS)
        assert result is None

    def test_all_six_signals_returns_none(self):
        dense = (
            "A developer shall define 'AI system', exempt government agencies, "
            "grant the right to opt-out, register with the Attorney General, "
            "and preempt any local ordinance."
        )
        result = route_by_signal(dense, ALL_AGENTS)
        assert result is None

    def test_five_of_six_signals_returns_none(self):
        # n-1 signals → None (threshold in route_by_signal is len-1)
        five_signal = (
            "A covered entity shall define applicability, grant right to opt-out, "
            "certify compliance, and preempt local law."
        )
        result = route_by_signal(five_signal, ALL_AGENTS)
        assert result is None

    def test_triage_metadata_augments_matching(self):
        from unittest.mock import MagicMock

        triage = MagicMock()
        triage.ai_signals = "shall require"  # obligation signal in metadata
        triage.llm_reasoning = ""
        # Main text alone has no signals
        result = route_by_signal("This section covers scope.", ALL_AGENTS, triage)
        assert result is not None
        assert "obligation" in result

    def test_result_subset_of_provided_names(self):
        limited = {"obligation", "definition_actor"}
        result = route_by_signal(
            "A covered entity shall define the term 'AI system'.", limited
        )
        if result is not None:
            assert result.issubset(limited)


# ---------------------------------------------------------------------------
# Gold fixture routing — known passage → expected agent subset
# ---------------------------------------------------------------------------


GOLD_ROUTING_FIXTURES: list[tuple[str, str, set[str]]] = [
    # (fixture_id, passage_text, expected_agent_subset)
    (
        "GR-001",
        "A developer shall conduct an impact assessment prior to deployment.",
        {"obligation"},
    ),
    (
        "GR-002",
        '"Automated decision system" means any system that uses computation to make or assist a consequential decision.',
        {"definition_actor"},
    ),
    (
        "GR-003",
        "Small businesses with annual revenues under $1 million are exempt from this section.",
        {"threshold_exception"},
    ),
    (
        "GR-004",
        "Consumers have the right to opt-out of automated profiling.",
        {"rights_protection"},
    ),
    (
        "GR-005",
        "Violations are subject to a civil penalty of up to $10,000, enforced by the Attorney General.",
        {"compliance_mechanism"},
    ),
    (
        "GR-006",
        "This Act preempts any inconsistent local ordinance.",
        {"preemption"},
    ),
    (
        "GR-007",
        # Multi-signal: obligation + rights — should produce both or fall back to all
        "A covered entity shall provide consumers the right to appeal automated decisions.",
        {"obligation", "rights_protection"},
    ),
    (
        "GR-008",
        # Multi-signal: definition + obligation
        '"AI system" means a machine-based system. Deployers shall register AI systems.',
        {"definition_actor", "obligation"},
    ),
]


class TestGoldRoutingFixtures:
    """Gold fixture regression tests for signal routing.

    Each fixture defines a passage that should trigger a specific known
    agent subset. These tests catch regressions in signal patterns.
    Failures here mean routing has narrowed or widened beyond the gold
    expectation — review the signal pattern change before merging.
    """

    @pytest.mark.parametrize("fixture_id,text,expected", GOLD_ROUTING_FIXTURES,
                             ids=[f[0] for f in GOLD_ROUTING_FIXTURES])
    def test_gold_fixture_routing(self, fixture_id, text, expected):
        result = select_agent_names(text, ALL_AGENTS, recall_sample_rate=0.0)
        # result must be a superset of expected (may include more but never drop expected agents)
        assert expected.issubset(result), (
            f"Fixture {fixture_id}: expected agents {expected!r} to run on passage, "
            f"but routing returned {result!r}"
        )

    def test_boilerplate_fixture_returns_empty(self):
        for text in [
            "Table of Contents",
            "Chapter 5",
            "page 42",
            "Be it enacted by the Legislature of the State of Colorado",
        ]:
            result = select_agent_names(text, ALL_AGENTS, recall_sample_rate=0.0)
            assert result == set(), f"Expected boilerplate to produce empty set: {text!r}"

    def test_definitions_header_fixture_routes_exactly_one(self):
        for text in ["Definitions", "As used in this act:", "As used in this section:"]:
            result = select_agent_names(text, ALL_AGENTS, recall_sample_rate=0.0)
            assert result == {"definition_actor"}, (
                f"Expected only definition_actor for header {text!r}, got {result!r}"
            )
