"""Unit tests for the evaluation harness.

Tests fixture loading, scoring logic, and report generation
without requiring LLM API calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.evaluation.harness import (
    AgentScore,
    EvaluationHarness,
    EvaluationResult,
    FieldScore,
)
from src.schemas.extraction import AbstentionResult


class TestFieldScore:
    def test_perfect_precision_recall(self):
        fs = FieldScore(field_name="subject", true_positives=5)
        assert fs.precision == 1.0
        assert fs.recall == 1.0
        assert fs.f1 == 1.0

    def test_zero_scores(self):
        fs = FieldScore(field_name="subject")
        assert fs.precision == 0.0
        assert fs.recall == 0.0
        assert fs.f1 == 0.0

    def test_mixed_scores(self):
        fs = FieldScore(field_name="subject", true_positives=3, false_positives=1, false_negatives=2)
        assert fs.precision == 0.75
        assert fs.recall == 0.6
        assert abs(fs.f1 - 2 * 0.75 * 0.6 / (0.75 + 0.6)) < 0.001


class TestAgentScore:
    def test_detection_metrics(self):
        score = AgentScore(agent_name="obligation", detection_tp=8, detection_fp=2, detection_fn=1)
        assert score.detection_precision == 0.8
        assert abs(score.detection_recall - 8 / 9) < 0.001

    def test_macro_f1(self):
        score = AgentScore(agent_name="obligation")
        score.field_scores["a"] = FieldScore(field_name="a", true_positives=5)
        score.field_scores["b"] = FieldScore(field_name="b", true_positives=0)
        assert score.macro_f1 == 0.5  # (1.0 + 0.0) / 2


class TestHarnessScoring:
    def test_correct_abstention(self):
        harness = EvaluationHarness()
        agent_score = AgentScore(agent_name="test")
        harness._score_case(agent_score, None, AbstentionResult(detected=False, reason="nothing"))
        assert agent_score.detection_fp == 0
        assert agent_score.detection_fn == 0

    def test_false_positive(self):
        harness = EvaluationHarness()
        agent_score = AgentScore(agent_name="test")
        harness._score_case(agent_score, None, {"subject": "developer", "modality": "shall"})
        assert agent_score.detection_fp == 1

    def test_false_negative(self):
        harness = EvaluationHarness()
        agent_score = AgentScore(agent_name="test")
        harness._score_case(
            agent_score,
            {"subject": "developer"},
            AbstentionResult(detected=False, reason="no obligation"),
        )
        assert agent_score.detection_fn == 1

    def test_true_positive_with_fields(self):
        harness = EvaluationHarness()
        agent_score = AgentScore(agent_name="test")
        harness._score_case(
            agent_score,
            {"subject": "developer", "modality": "shall"},
            {"subject": "developer", "modality": "shall"},
        )
        assert agent_score.detection_tp == 1
        assert agent_score.field_scores["subject"].true_positives == 1
        assert agent_score.field_scores["modality"].true_positives == 1

    def test_fuzzy_string_matching(self):
        harness = EvaluationHarness()
        assert harness._values_match("Developer", "developer")
        assert harness._values_match("  shall  ", "shall")
        assert not harness._values_match("shall", "must")


class TestHarnessFixtureLoading:
    def test_loads_all_fixtures(self):
        harness = EvaluationHarness(gold_standard_dir="tests/fixtures/gold_standard")
        cases = harness.load_test_cases()
        assert len(cases) >= 10

    def test_fixture_structure(self):
        harness = EvaluationHarness(gold_standard_dir="tests/fixtures/gold_standard")
        cases = harness.load_test_cases()
        for case in cases:
            assert "passage_id" in case
            assert "passage_text" in case
            assert "expected_extractions" in case
            ee = case["expected_extractions"]
            assert all(k in ee for k in ["obligation", "definition", "threshold_exception", "ambiguity"])


class TestReportGeneration:
    def test_print_report(self):
        result = EvaluationResult(total_cases=5)
        result.agent_scores["obligation"] = AgentScore(
            agent_name="obligation",
            detection_tp=3,
            detection_fp=1,
            detection_fn=1,
            total_cases=5,
        )
        result.agent_scores["obligation"].field_scores["subject"] = FieldScore(
            field_name="subject", true_positives=3, false_positives=0, false_negatives=0
        )

        harness = EvaluationHarness()
        report = harness.print_report(result)

        assert "EVALUATION REPORT" in report
        assert "OBLIGATION" in report
        assert "subject" in report
        assert "Total test cases: 5" in report
