"""Tests for generate_summary() in src/core/summary_generator.py.

Tests all 12 extraction types registered in _TEMPLATE_GENERATORS.
All functions are pure (template-based, no LLM), so no mocking needed.
"""

import pytest

from src.core.summary_generator import generate_summary


class TestObligationSummary:
    def test_basic_obligation(self):
        payload = {"subject": "AI developer", "modality": "must", "action": "conduct impact assessment"}
        result = generate_summary("obligation", payload)
        assert "Ai Developer" in result
        assert "must" in result
        assert "conduct impact assessment" in result

    def test_obligation_with_timeline(self):
        payload = {
            "subject": "deployer",
            "action": "submit report",
            "timeline": {"effective_date": "2025-07-01", "compliance_deadline": "2026-01-01"},
        }
        result = generate_summary("obligation", payload)
        assert "Effective: 2025-07-01" in result
        assert "Compliance deadline: 2026-01-01" in result

    def test_obligation_with_enforcement(self):
        payload = {
            "subject": "developer",
            "action": "register",
            "enforcement": {
                "enforcing_body": "AG",
                "max_civil_penalty_usd": 50000,
                "cure_period_days": 60,
            },
        }
        result = generate_summary("obligation", payload)
        assert "Enforced by AG" in result
        assert "$50,000" in result
        assert "60-day cure period" in result

    def test_obligation_with_section_and_jurisdiction(self):
        payload = {"subject": "entity", "action": "comply", "section_reference": "6-1-1703"}
        result = generate_summary("obligation", payload, jurisdiction="CO")
        assert "§ 6-1-1703" in result
        assert "(CO)" in result

    def test_empty_obligation(self):
        result = generate_summary("obligation", {})
        assert "Entity" in result  # default subject


class TestTimelineSummary:
    """Timeline uses the same template as obligation."""

    def test_timeline_type(self):
        payload = {"subject": "system", "action": "comply", "timeline": {"effective_date": "2025-01-01"}}
        result = generate_summary("timeline", payload)
        assert "Effective: 2025-01-01" in result


class TestThresholdSummary:
    def test_basic_threshold(self):
        payload = {"threshold_value": "50", "threshold_unit": "employees"}
        result = generate_summary("threshold", payload)
        assert "50 employees" in result

    def test_threshold_with_exceptions(self):
        payload = {
            "threshold_value": "100",
            "threshold_unit": "users",
            "exceptions": [{"description": "Exempt if nonprofit"}],
        }
        result = generate_summary("threshold", payload)
        assert "100 users" in result
        assert "Exempt if nonprofit" in result

    def test_threshold_with_compute(self):
        payload = {"compute_flops": 1e26, "compute_description": "Frontier model threshold"}
        result = generate_summary("threshold", payload)
        assert "FLOPS" in result

    def test_threshold_with_sectors(self):
        payload = {"threshold_value": "any", "sector_applicability": ["healthcare", "finance"]}
        result = generate_summary("threshold", payload)
        assert "healthcare" in result
        assert "finance" in result

    def test_empty_threshold(self):
        result = generate_summary("threshold", {})
        assert "no summary details" in result.lower() or "Threshold" in result


class TestExceptionSummary:
    """Exception uses the same template as threshold."""

    def test_exception_type(self):
        payload = {"exceptions": [{"description": "Small business carveout"}]}
        result = generate_summary("exception", payload)
        assert "Small business carveout" in result


class TestDefinitionSummary:
    def test_basic_definition(self):
        payload = {"term": "artificial intelligence", "definition_text": "a machine-based system"}
        result = generate_summary("definition", payload)
        assert '"artificial intelligence"' in result
        assert "machine-based system" in result

    def test_definition_with_actors(self):
        payload = {
            "term": "deployer",
            "definition_text": "a person who deploys",
            "actors": [{"actor_name": "Cloud Provider"}, {"actor_name": "End User"}],
        }
        result = generate_summary("definition", payload)
        assert "Cloud Provider" in result

    def test_definition_with_framework_refs(self):
        payload = {
            "term": "AI system",
            "definition_text": "per NIST framework",
            "framework_refs": [{"framework_name": "NIST AI RMF"}],
        }
        result = generate_summary("definition", payload)
        assert "NIST AI RMF" in result

    def test_long_definition_truncated(self):
        payload = {"term": "test", "definition_text": "x" * 300}
        result = generate_summary("definition", payload)
        assert "..." in result


class TestActorMappingSummary:
    """actor_mapping uses the definition template."""

    def test_actor_mapping(self):
        payload = {"term": "developer", "definition_text": "builds AI", "actors": [{"actor_name": "Google"}]}
        result = generate_summary("actor_mapping", payload)
        assert "Google" in result


class TestFrameworkRefSummary:
    """framework_ref uses the definition template."""

    def test_framework_ref(self):
        payload = {"term": "standard", "definition_text": "follows NIST", "framework_refs": [{"framework_name": "ISO 42001"}]}
        result = generate_summary("framework_ref", payload)
        assert "ISO 42001" in result


class TestAmbiguitySummary:
    def test_basic_ambiguity(self):
        payload = {
            "ambiguity_type": "vague_term",
            "severity": "high",
            "ambiguous_text": "reasonable measures",
        }
        result = generate_summary("ambiguity", payload)
        assert "HIGH" in result
        assert "vague term" in result
        assert "reasonable measures" in result

    def test_ambiguity_with_suggestion(self):
        payload = {
            "ambiguity_type": "conflicting_provision",
            "severity": "medium",
            "ambiguous_text": "shall and may conflict",
            "suggested_clarification": "Use 'shall' consistently",
        }
        result = generate_summary("ambiguity", payload)
        assert "Suggested fix" in result


class TestRightsProtectionSummary:
    def test_basic_rights(self):
        payload = {
            "right_holder": "consumer",
            "right_type": "opt_out",
            "right_description": "Right to opt out of automated decisions",
        }
        result = generate_summary("rights_protection", payload)
        assert "Consumer" in result
        assert "opt out" in result

    def test_rights_with_remedies(self):
        payload = {
            "right_holder": "individual",
            "right_type": "explanation",
            "right_description": "Right to explanation of AI decision",
            "remedies": [{"remedy_type": "injunctive", "description": "Court may order cessation"}],
        }
        result = generate_summary("rights_protection", payload)
        assert "injunctive" in result


class TestComplianceMechanismSummary:
    def test_basic_compliance(self):
        payload = {
            "mechanism_type": "bias_audit",
            "responsible_party": "deployer",
            "description": "conduct annual bias audit",
        }
        result = generate_summary("compliance_mechanism", payload)
        assert "Bias Audit" in result
        assert "deployer" in result.lower()

    def test_compliance_with_all_flags(self):
        payload = {
            "mechanism_type": "impact_assessment",
            "responsible_party": "developer",
            "description": "perform assessment",
            "is_bias_testing": True,
            "is_third_party_audit": True,
            "assessment_frequency_months": 12,
        }
        result = generate_summary("compliance_mechanism", payload)
        assert "bias testing" in result.lower()
        assert "third-party audit" in result.lower()
        assert "12 months" in result


class TestPreemptionSignalSummary:
    def test_basic_preemption(self):
        payload = {
            "conflict_type": "express_preemption",
            "severity": "high",
            "description": "Federal law explicitly preempts state regulation",
        }
        result = generate_summary("preemption_signal", payload)
        assert "HIGH" in result
        assert "Express Preemption" in result
        assert "preempts" in result

    def test_preemption_with_authority(self):
        payload = {
            "conflict_type": "commerce_clause",
            "severity": "medium",
            "description": "Potential dormant commerce clause issue",
            "related_authority": "US Constitution Art. I § 8",
        }
        result = generate_summary("preemption_signal", payload)
        assert "Authority:" in result


class TestEnforcementSummary:
    def test_enforcement_with_nested_dict(self):
        payload = {
            "enforcement": {
                "enforcing_body": "FTC",
                "max_civil_penalty_usd": 100000,
                "private_right_of_action": True,
            }
        }
        result = generate_summary("enforcement", payload)
        assert "FTC" in result
        assert "$100,000" in result
        assert "Private right of action" in result

    def test_enforcement_flat(self):
        payload = {"enforcing_body": "State AG"}
        result = generate_summary("enforcement", payload)
        assert "State AG" in result

    def test_empty_enforcement(self):
        result = generate_summary("enforcement", {})
        assert "Enforcement" in result


class TestGenerateSummaryEdgeCases:
    def test_unknown_type_fallback(self):
        result = generate_summary("totally_unknown_type", {"foo": "bar"})
        assert "Totally Unknown Type" in result
        assert "see payload" in result.lower()

    def test_internal_metadata_stripped(self):
        """Fields starting with _ should be stripped before template processing."""
        payload = {
            "subject": "developer",
            "action": "comply",
            "_internal_score": 0.95,
            "_agent_name": "obligation",
        }
        result = generate_summary("obligation", payload)
        assert "_internal" not in result
        assert "Developer" in result

    def test_jurisdiction_none(self):
        payload = {"subject": "entity", "action": "report", "section_reference": "101"}
        result = generate_summary("obligation", payload, jurisdiction=None)
        assert "()" not in result  # no empty parens
