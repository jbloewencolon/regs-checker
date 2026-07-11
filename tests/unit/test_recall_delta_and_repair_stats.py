"""Unit tests for SFH-1d (audit SF-02) and SFH-1e (audit SF-05).

SF-02: 5% of passages bypass routing and run the full agent battery so
routing false-narrowing can be measured — but nothing ever computed the
measurement. select_agent_names_with_decision() now exposes what routing
WOULD have chosen alongside what ran.

SF-05: truncated-JSON salvage drops trailing array elements to make the
payload parse; the number discarded was never counted, and per-strategy
repair hits were never aggregated.
"""

from __future__ import annotations

from unittest.mock import patch

from src.agents.base import BaseExtractionAgent, _estimate_array_elements
from src.ingestion.routing import select_agent_names_with_decision

_ALL_AGENTS = {
    "obligation", "definition_actor", "threshold_exception",
    "rights_protection", "compliance_mechanism", "preemption",
}


class TestRoutingDecision:
    def test_no_sampling_selected_equals_routed(self):
        text = "The operator shall comply with the registration requirement."
        decision = select_agent_names_with_decision(
            text, _ALL_AGENTS, recall_sample_rate=0.0
        )
        assert decision.bypassed is False
        assert decision.selected == decision.routed

    def test_sampled_passage_exposes_routed_set(self):
        # Force the sample (rate=1.0): selected = full battery, routed = what
        # signal routing would have chosen — the delta SF-02 needs.
        text = "The operator shall comply with the registration requirement."
        with patch("src.ingestion.routing.random.random", return_value=0.0):
            decision = select_agent_names_with_decision(
                text, _ALL_AGENTS, recall_sample_rate=1.0
            )
        assert decision.bypassed is True
        assert decision.selected == frozenset(_ALL_AGENTS)
        # routed is what signal routing computes for this text — a subset,
        # computed unconditionally even though the passage was sampled.
        assert decision.routed <= frozenset(_ALL_AGENTS)

    def test_boilerplate_never_sampled(self):
        # A pure structural header (same fixture as test_routing.py) must be
        # skipped entirely, even at 100% sample rate.
        decision = select_agent_names_with_decision(
            "Table of Contents",
            _ALL_AGENTS,
            recall_sample_rate=1.0,
        )
        assert decision.selected == frozenset()
        assert decision.bypassed is False

    def test_wrapper_matches_decision_selected(self):
        from src.ingestion.routing import select_agent_names

        text = "A developer must maintain documentation of training data."
        names = select_agent_names(text, _ALL_AGENTS, recall_sample_rate=0.0)
        decision = select_agent_names_with_decision(
            text, _ALL_AGENTS, recall_sample_rate=0.0
        )
        assert names == set(decision.selected)


class TestEstimateArrayElements:
    def test_counts_extraction_envelope_elements(self):
        text = '{"extractions": [{"a": 1}, {"b": 2}, {"c": 3}]}'
        assert _estimate_array_elements(text) == 3

    def test_counts_truncated_elements_too(self):
        # The whole point: the started-but-incomplete element is counted, so
        # started(3) - kept(2) = 1 dropped.
        text = '{"extractions": [{"a": 1}, {"b": 2}, {"c": '
        assert _estimate_array_elements(text) == 3

    def test_ignores_braces_inside_strings(self):
        text = '{"extractions": [{"a": "has { brace"}, {"b": 2}]}'
        assert _estimate_array_elements(text) == 2

    def test_nested_objects_not_counted(self):
        text = '{"extractions": [{"a": {"nested": 1}}]}'
        assert _estimate_array_elements(text) == 1


class TestRepairReport:
    def test_truncation_salvage_reports_strategy_and_estimate(self):
        # Bare-array shape: the form the truncation salvage actually repairs
        # (extract() normalizes bare arrays into the envelope post-parse; the
        # envelope shape cut mid-value is unrepairable by the current chain
        # and gets discarded — a pre-existing salvage limitation, documented
        # here rather than hidden).
        raw = '[{"a": 1}, {"b": 2}, {"c": '
        report: dict = {}
        repaired = BaseExtractionAgent._repair_json(raw, report=report)
        import json

        parsed = json.loads(repaired)
        kept = len(parsed)
        assert "truncation_salvage" in report["strategies"]
        assert report["items_started_estimate"] == 3
        # started(3) - kept(2) = 1 element discarded by the salvage.
        assert report["items_started_estimate"] - kept == 1

    def test_trailing_comma_strategy_reported(self):
        raw = '{"extractions": [{"a": 1},]}'
        report: dict = {}
        BaseExtractionAgent._repair_json(raw, report=report)
        assert "trailing_comma_strip" in report["strategies"]

    def test_clean_json_reports_nothing(self):
        raw = '{"extractions": [{"a": 1}]}'
        report: dict = {}
        BaseExtractionAgent._repair_json(raw, report=report)
        assert report.get("strategies", []) == []

    def test_report_optional_backward_compat(self):
        # Callers that don't pass a report must behave exactly as before.
        raw = '{"extractions": [{"a": 1},]}'
        out = BaseExtractionAgent._repair_json(raw)
        import json

        assert json.loads(out)["extractions"] == [{"a": 1}]
