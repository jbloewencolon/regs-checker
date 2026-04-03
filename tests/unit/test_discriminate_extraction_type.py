"""Tests for _discriminate_extraction_type() in src/ingestion/extractor.py.

This function determines the specific extraction sub-type from the agent name
and payload content. Multi-type agents (obligation, definition_actor,
threshold_exception) can produce different sub-types depending on payload.
"""

import pytest

from src.db.models import ExtractionType
from src.ingestion.extractor import _discriminate_extraction_type


# ---------------------------------------------------------------------------
# Obligation agent
# ---------------------------------------------------------------------------


class TestObligationAgent:
    """Tests for the obligation agent's type discrimination."""

    def test_default_obligation(self):
        """Basic obligation with subject + action stays as obligation."""
        payload = {"subject": "AI developer", "action": "conduct impact assessment"}
        result = _discriminate_extraction_type("obligation", payload)
        assert result == ExtractionType.obligation

    def test_enforcement_when_no_core_obligation(self):
        """Enforcement data without subject/action -> enforcement."""
        payload = {
            "subject": "",
            "action": "",
            "enforcement": {"enforcing_body": "Attorney General", "penalty_type": "civil"},
        }
        result = _discriminate_extraction_type("obligation", payload)
        assert result == ExtractionType.enforcement

    def test_timeline_when_no_core_obligation(self):
        """Timeline data without subject/action -> timeline."""
        payload = {
            "subject": "",
            "action": "",
            "timeline": {"effective_date": "2025-01-01", "compliance_deadline": "2026-01-01"},
        }
        result = _discriminate_extraction_type("obligation", payload)
        assert result == ExtractionType.timeline

    def test_enforcement_subject_with_penalty_action(self):
        """Court/AG imposing penalties = enforcement, even with subject+action."""
        payload = {
            "subject": "Attorney General",
            "subject_normalized": "attorney_general",
            "action": "impose civil penalties up to $50,000",
            "enforcement": {"enforcing_body": "AG", "penalty_type": "civil"},
        }
        result = _discriminate_extraction_type("obligation", payload)
        assert result == ExtractionType.enforcement

    def test_court_subject_with_fine_action(self):
        """Court imposing fines = enforcement."""
        payload = {
            "subject": "The court",
            "action": "may fine violators",
            "enforcement": {"enforcing_body": "Court"},
        }
        result = _discriminate_extraction_type("obligation", payload)
        assert result == ExtractionType.enforcement

    def test_developer_subject_stays_obligation(self):
        """Developer with enforcement data but non-penalty action stays obligation."""
        payload = {
            "subject": "AI developer",
            "action": "conduct annual bias audit",
            "enforcement": {"enforcing_body": "AG"},
        }
        result = _discriminate_extraction_type("obligation", payload)
        assert result == ExtractionType.obligation

    def test_obligation_with_both_enforcement_and_timeline(self):
        """Full obligation with enforcement + timeline stays obligation."""
        payload = {
            "subject": "Deployer",
            "action": "implement risk management framework",
            "enforcement": {"enforcing_body": "AG"},
            "timeline": {"effective_date": "2025-07-01"},
        }
        result = _discriminate_extraction_type("obligation", payload)
        assert result == ExtractionType.obligation

    def test_empty_payload_defaults_to_obligation(self):
        """Empty payload defaults to obligation."""
        result = _discriminate_extraction_type("obligation", {})
        assert result == ExtractionType.obligation

    def test_enforcement_priority_over_timeline_when_no_core(self):
        """When no core obligation, enforcement takes priority over timeline."""
        payload = {
            "subject": "",
            "action": "",
            "enforcement": {"enforcing_body": "Commission"},
            "timeline": {"effective_date": "2025-01-01"},
        }
        result = _discriminate_extraction_type("obligation", payload)
        assert result == ExtractionType.enforcement


# ---------------------------------------------------------------------------
# Definition/Actor agent
# ---------------------------------------------------------------------------


class TestDefinitionActorAgent:
    """Tests for the definition_actor agent's type discrimination."""

    def test_default_definition(self):
        """Term + definition_text -> definition."""
        payload = {"term": "artificial intelligence", "definition_text": "means a machine-based system"}
        result = _discriminate_extraction_type("definition_actor", payload)
        assert result == ExtractionType.definition

    def test_actor_mapping_without_definition(self):
        """Actors without term/definition -> actor_mapping."""
        payload = {
            "term": "",
            "definition_text": "",
            "actors": [{"actor_name": "Developer", "role": "primary"}],
        }
        result = _discriminate_extraction_type("definition_actor", payload)
        assert result == ExtractionType.actor_mapping

    def test_framework_ref_without_definition(self):
        """Framework refs without term/definition -> framework_ref."""
        payload = {
            "term": "",
            "definition_text": "",
            "framework_refs": [{"framework_name": "NIST AI RMF"}],
        }
        result = _discriminate_extraction_type("definition_actor", payload)
        assert result == ExtractionType.framework_ref

    def test_definition_with_actors(self):
        """Term + definition + actors still -> definition (core takes priority)."""
        payload = {
            "term": "deployer",
            "definition_text": "a person who deploys an AI system",
            "actors": [{"actor_name": "Deployer"}],
        }
        result = _discriminate_extraction_type("definition_actor", payload)
        assert result == ExtractionType.definition

    def test_actor_priority_over_framework_when_no_core(self):
        """Without core definition, actors take priority over framework_refs."""
        payload = {
            "term": "",
            "definition_text": "",
            "actors": [{"actor_name": "Developer"}],
            "framework_refs": [{"framework_name": "NIST"}],
        }
        result = _discriminate_extraction_type("definition_actor", payload)
        assert result == ExtractionType.actor_mapping

    def test_empty_payload_defaults_to_definition(self):
        """Empty payload defaults to definition."""
        result = _discriminate_extraction_type("definition_actor", {})
        assert result == ExtractionType.definition


# ---------------------------------------------------------------------------
# Threshold/Exception agent
# ---------------------------------------------------------------------------


class TestThresholdExceptionAgent:
    """Tests for the threshold_exception agent's type discrimination."""

    def test_default_threshold(self):
        """Threshold data present -> threshold."""
        payload = {"threshold_type": "employee_count", "threshold_value": "50"}
        result = _discriminate_extraction_type("threshold_exception", payload)
        assert result == ExtractionType.threshold

    def test_exception_without_threshold(self):
        """Exceptions without threshold data -> exception."""
        payload = {
            "exceptions": [{"description": "Exempt if fewer than 50 employees"}],
        }
        result = _discriminate_extraction_type("threshold_exception", payload)
        assert result == ExtractionType.exception

    def test_threshold_with_exceptions(self):
        """Both threshold + exceptions -> threshold (threshold takes priority)."""
        payload = {
            "threshold_type": "revenue",
            "threshold_value": "$50M",
            "exceptions": [{"description": "Small business exempt"}],
        }
        result = _discriminate_extraction_type("threshold_exception", payload)
        assert result == ExtractionType.threshold

    def test_empty_payload_defaults_to_threshold(self):
        """Empty payload defaults to threshold."""
        result = _discriminate_extraction_type("threshold_exception", {})
        assert result == ExtractionType.threshold

    def test_threshold_condition_counts_as_threshold(self):
        """threshold_condition alone counts as threshold data."""
        payload = {"threshold_condition": "when the system processes biometric data"}
        result = _discriminate_extraction_type("threshold_exception", payload)
        assert result == ExtractionType.threshold


# ---------------------------------------------------------------------------
# Single-type agents
# ---------------------------------------------------------------------------


class TestSingleTypeAgents:
    """Tests for agents that produce only one extraction type."""

    def test_ambiguity(self):
        result = _discriminate_extraction_type("ambiguity", {"ambiguous_text": "reasonable"})
        assert result == ExtractionType.ambiguity

    def test_rights_protection(self):
        result = _discriminate_extraction_type("rights_protection", {"right_type": "opt_out"})
        assert result == ExtractionType.rights_protection

    def test_compliance_mechanism(self):
        result = _discriminate_extraction_type("compliance_mechanism", {"mechanism_type": "audit"})
        assert result == ExtractionType.compliance_mechanism

    def test_preemption(self):
        result = _discriminate_extraction_type("preemption", {"conflict_type": "express_preemption"})
        assert result == ExtractionType.preemption_signal

    def test_unknown_agent_defaults_to_obligation(self):
        """Unknown agent name returns obligation as fallback."""
        result = _discriminate_extraction_type("nonexistent_agent", {})
        assert result == ExtractionType.obligation
