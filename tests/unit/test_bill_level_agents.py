"""Unit tests for bill-level agent parse_response() methods.

Tests cover type coercion, field normalization, null handling, and
list/dict enforcement for the three bill-level agents: enforcement,
applicability, and compliance_timeline.

No LLM calls or DB connections — parse_response() is pure data transformation.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


def _make_agent(cls):
    """Instantiate a bill-level agent with mocked LLM provider and config."""
    with patch("src.agents.bill_level_base.get_extraction_provider"), \
         patch("src.agents.bill_level_base.get_config") as mock_cfg:
        mock_cfg.return_value.agents = {}  # no agent-specific overrides
        return cls()


# ---------------------------------------------------------------------------
# EnforcementAgent
# ---------------------------------------------------------------------------


class TestEnforcementAgentParseResponse:
    @pytest.fixture(autouse=True)
    def agent(self):
        from src.agents.enforcement_agent import EnforcementAgent
        self.agent = _make_agent(EnforcementAgent)

    def test_clean_payload_passthrough(self):
        raw = json.dumps({
            "enforcing_body": "Attorney General",
            "max_civil_penalty_usd": 10000,
            "penalty_per": "violation",
            "cure_period_days": 30,
            "private_right_of_action": True,
            "criminal_penalties": False,
            "criminal_penalty_description": None,
            "enforcement_text": "Violations shall be punished.",
        })
        result = self.agent.parse_response(raw)
        assert result["enforcing_body"] == "Attorney General"
        assert result["max_civil_penalty_usd"] == 10000
        assert result["cure_period_days"] == 30
        assert result["private_right_of_action"] is True
        assert result["criminal_penalties"] is False

    def test_string_int_coercion_penalty(self):
        raw = json.dumps({"max_civil_penalty_usd": "$10,000"})
        assert self.agent.parse_response(raw)["max_civil_penalty_usd"] == 10000

    def test_string_int_coercion_cure_period(self):
        raw = json.dumps({"cure_period_days": "30 days"})
        assert self.agent.parse_response(raw)["cure_period_days"] == 30

    def test_string_with_no_digits_becomes_none(self):
        raw = json.dumps({"max_civil_penalty_usd": "not specified"})
        assert self.agent.parse_response(raw)["max_civil_penalty_usd"] is None

    def test_non_int_non_str_becomes_none(self):
        raw = json.dumps({"max_civil_penalty_usd": [10000]})
        assert self.agent.parse_response(raw)["max_civil_penalty_usd"] is None

    def test_string_bool_true_variants(self):
        for truthy in ("true", "yes", "1"):
            raw = json.dumps({"private_right_of_action": truthy, "criminal_penalties": truthy})
            result = self.agent.parse_response(raw)
            assert result["private_right_of_action"] is True, f"Expected True for {truthy!r}"
            assert result["criminal_penalties"] is True, f"Expected True for {truthy!r}"

    def test_string_bool_false_variant(self):
        raw = json.dumps({"private_right_of_action": "false", "criminal_penalties": "no"})
        result = self.agent.parse_response(raw)
        assert result["private_right_of_action"] is False
        assert result["criminal_penalties"] is False

    def test_null_fields_preserved(self):
        raw = json.dumps({
            "enforcing_body": None,
            "max_civil_penalty_usd": None,
            "cure_period_days": None,
            "private_right_of_action": None,
            "criminal_penalties": None,
        })
        result = self.agent.parse_response(raw)
        assert result["enforcing_body"] is None
        assert result["max_civil_penalty_usd"] is None
        assert result["private_right_of_action"] is None

    def test_markdown_fence_stripped(self):
        raw = "```json\n{\"enforcing_body\": \"AG\", \"max_civil_penalty_usd\": 5000}\n```"
        result = self.agent.parse_response(raw)
        assert result["enforcing_body"] == "AG"
        assert result["max_civil_penalty_usd"] == 5000

    def test_trailing_comma_handled(self):
        raw = '{"enforcing_body": "AG", "max_civil_penalty_usd": 500,}'
        result = self.agent.parse_response(raw)
        assert result["enforcing_body"] == "AG"
        assert result["max_civil_penalty_usd"] == 500


class TestEnforcementAgentPenaltyTiers:
    """EA5-4: 'if a range is given, use the maximum' collapses legally
    distinct tiers (negligent vs. willful, first vs. subsequent violation).
    penalty_tiers preserves the structure; max_civil_penalty_usd keeps
    serving as the flattened matrix column."""

    @pytest.fixture(autouse=True)
    def agent(self):
        from src.agents.enforcement_agent import EnforcementAgent
        self.agent = _make_agent(EnforcementAgent)

    def test_missing_penalty_tiers_key_is_none(self):
        raw = json.dumps({"max_civil_penalty_usd": 10000})
        assert self.agent.parse_response(raw)["penalty_tiers"] is None

    def test_null_penalty_tiers_stays_none(self):
        raw = json.dumps({"max_civil_penalty_usd": 10000, "penalty_tiers": None})
        assert self.agent.parse_response(raw)["penalty_tiers"] is None

    def test_two_tier_structure_preserved(self):
        raw = json.dumps({
            "max_civil_penalty_usd": 7500,
            "penalty_tiers": [
                {"condition": "negligent violation", "amount_usd": 2500},
                {"condition": "intentional violation", "amount_usd": 7500},
            ],
        })
        result = self.agent.parse_response(raw)
        assert result["penalty_tiers"] == [
            {"condition": "negligent violation", "amount_usd": 2500},
            {"condition": "intentional violation", "amount_usd": 7500},
        ]

    def test_max_civil_penalty_self_heals_from_tiers_when_null(self):
        raw = json.dumps({
            "max_civil_penalty_usd": None,
            "penalty_tiers": [
                {"condition": "first violation", "amount_usd": 2500},
                {"condition": "subsequent violation", "amount_usd": 7500},
            ],
        })
        result = self.agent.parse_response(raw)
        assert result["max_civil_penalty_usd"] == 7500

    def test_max_civil_penalty_self_heals_when_inconsistently_low(self):
        # Model reported the matrix column but got it wrong relative to its
        # own tiers — correct upward, never down.
        raw = json.dumps({
            "max_civil_penalty_usd": 2500,
            "penalty_tiers": [
                {"condition": "negligent violation", "amount_usd": 2500},
                {"condition": "willful violation", "amount_usd": 7500},
            ],
        })
        result = self.agent.parse_response(raw)
        assert result["max_civil_penalty_usd"] == 7500

    def test_max_civil_penalty_not_lowered_when_already_higher(self):
        raw = json.dumps({
            "max_civil_penalty_usd": 50000,
            "penalty_tiers": [{"condition": "per violation", "amount_usd": 7500}],
        })
        result = self.agent.parse_response(raw)
        assert result["max_civil_penalty_usd"] == 50000

    def test_tier_with_string_amount_coerced(self):
        raw = json.dumps({
            "penalty_tiers": [{"condition": "willful violation", "amount_usd": "$7,500"}],
        })
        result = self.agent.parse_response(raw)
        assert result["penalty_tiers"] == [{"condition": "willful violation", "amount_usd": 7500}]

    def test_tier_missing_condition_dropped(self):
        raw = json.dumps({
            "penalty_tiers": [
                {"amount_usd": 7500},
                {"condition": "willful violation", "amount_usd": 2500},
            ],
        })
        result = self.agent.parse_response(raw)
        assert result["penalty_tiers"] == [{"condition": "willful violation", "amount_usd": 2500}]

    def test_tier_with_unparseable_amount_dropped(self):
        raw = json.dumps({
            "penalty_tiers": [{"condition": "willful violation", "amount_usd": "not specified"}],
        })
        result = self.agent.parse_response(raw)
        assert result["penalty_tiers"] is None

    def test_all_tiers_malformed_yields_none_not_empty_list(self):
        raw = json.dumps({"penalty_tiers": [{"foo": "bar"}, "not a dict"]})
        assert self.agent.parse_response(raw)["penalty_tiers"] is None

    def test_penalty_tiers_not_a_list_becomes_none(self):
        raw = json.dumps({"penalty_tiers": "negligent: $2,500, willful: $7,500"})
        assert self.agent.parse_response(raw)["penalty_tiers"] is None

    def test_single_flat_penalty_no_tiers_untouched(self):
        # No penalty_tiers at all — max_civil_penalty_usd is used exactly as
        # the model reported it, matching pre-EA5-4 behavior.
        raw = json.dumps({"max_civil_penalty_usd": 10000})
        result = self.agent.parse_response(raw)
        assert result["max_civil_penalty_usd"] == 10000
        assert result["penalty_tiers"] is None


# ---------------------------------------------------------------------------
# ApplicabilityAgent
# ---------------------------------------------------------------------------


class TestApplicabilityAgentParseResponse:
    @pytest.fixture(autouse=True)
    def agent(self):
        from src.agents.applicability_agent import ApplicabilityAgent
        self.agent = _make_agent(ApplicabilityAgent)

    def test_clean_payload_passthrough(self):
        raw = json.dumps({
            "covered_entity_types": ["developer", "deployer"],
            "covered_sectors": ["employment", "healthcare"],
            "ai_system_types_in_scope": ["high_risk_ai"],
            "size_thresholds": {
                "revenue_usd": 25000000,
                "employee_count": 50,
                "consumer_data_volume": None,
                "compute_flops": None,
            },
            "geographic_scope": "Entities doing business in Colorado",
            "key_exemptions": ["Small business exemption"],
            "government_only": False,
            "applicability_summary": "Applies to AI developers in Colorado.",
        })
        result = self.agent.parse_response(raw)
        assert result["covered_entity_types"] == ["developer", "deployer"]
        assert result["covered_sectors"] == ["employment", "healthcare"]
        assert result["size_thresholds"]["revenue_usd"] == 25000000
        assert result["government_only"] is False

    def test_null_list_fields_become_empty_list(self):
        raw = json.dumps({
            "covered_entity_types": None,
            "covered_sectors": None,
            "ai_system_types_in_scope": None,
            "key_exemptions": None,
        })
        result = self.agent.parse_response(raw)
        assert result["covered_entity_types"] == []
        assert result["covered_sectors"] == []
        assert result["ai_system_types_in_scope"] == []
        assert result["key_exemptions"] == []

    def test_string_list_field_wrapped_in_list(self):
        raw = json.dumps({"covered_entity_types": "developer"})
        result = self.agent.parse_response(raw)
        assert result["covered_entity_types"] == ["developer"]

    def test_empty_string_list_field_becomes_empty_list(self):
        raw = json.dumps({"covered_entity_types": ""})
        result = self.agent.parse_response(raw)
        assert result["covered_entity_types"] == []

    def test_missing_size_thresholds_becomes_null_dict(self):
        raw = json.dumps({})
        result = self.agent.parse_response(raw)
        expected = {
            "revenue_usd": None,
            "employee_count": None,
            "consumer_data_volume": None,
            "compute_flops": None,
        }
        assert result["size_thresholds"] == expected

    def test_non_dict_size_thresholds_replaced(self):
        raw = json.dumps({"size_thresholds": "50+ employees"})
        result = self.agent.parse_response(raw)
        assert isinstance(result["size_thresholds"], dict)
        assert result["size_thresholds"]["employee_count"] is None

    def test_size_threshold_string_int_coercion(self):
        raw = json.dumps({"size_thresholds": {
            "revenue_usd": "25,000,000",
            "employee_count": "50 employees",
            "consumer_data_volume": None,
        }})
        result = self.agent.parse_response(raw)
        assert result["size_thresholds"]["revenue_usd"] == 25000000
        assert result["size_thresholds"]["employee_count"] == 50

    def test_size_threshold_non_int_becomes_none(self):
        raw = json.dumps({"size_thresholds": {"revenue_usd": [1000000]}})
        result = self.agent.parse_response(raw)
        assert result["size_thresholds"]["revenue_usd"] is None

    def test_government_only_string_coercion(self):
        for truthy in ("true", "yes", "1"):
            raw = json.dumps({"government_only": truthy})
            assert self.agent.parse_response(raw)["government_only"] is True

    def test_government_only_false_string(self):
        raw = json.dumps({"government_only": "false"})
        assert self.agent.parse_response(raw)["government_only"] is False


# ---------------------------------------------------------------------------
# ComplianceTimelineAgent
# ---------------------------------------------------------------------------


class TestComplianceTimelineAgentParseResponse:
    @pytest.fixture(autouse=True)
    def agent(self):
        from src.agents.compliance_timeline_agent import ComplianceTimelineAgent
        self.agent = _make_agent(ComplianceTimelineAgent)

    def test_clean_payload_passthrough(self):
        raw = json.dumps({
            "law_effective_date": "2025-01-01",
            "enforcement_start_date": "2025-07-01",
            "sunset_date": None,
            "key_deadlines": [
                {
                    "action": "Submit annual report",
                    "deadline_type": "recurring",
                    "relative_days": None,
                    "frequency_months": 12,
                    "trigger_event": None,
                }
            ],
            "impact_assessment_frequency_months": 12,
            "consumer_request_response_days": 45,
            "cure_period_days": 30,
            "first_compliance_action": "Register AI systems within 90 days of enactment.",
        })
        result = self.agent.parse_response(raw)
        assert result["law_effective_date"] == "2025-01-01"
        assert result["enforcement_start_date"] == "2025-07-01"
        assert len(result["key_deadlines"]) == 1
        assert result["key_deadlines"][0]["frequency_months"] == 12
        assert result["impact_assessment_frequency_months"] == 12
        assert result["consumer_request_response_days"] == 45
        assert result["cure_period_days"] == 30

    def test_null_key_deadlines_becomes_empty_list(self):
        raw = json.dumps({"key_deadlines": None})
        result = self.agent.parse_response(raw)
        assert result["key_deadlines"] == []

    def test_non_list_key_deadlines_becomes_empty_list(self):
        raw = json.dumps({"key_deadlines": "within 90 days of enactment"})
        result = self.agent.parse_response(raw)
        assert result["key_deadlines"] == []

    def test_non_dict_items_in_key_deadlines_dropped(self):
        raw = json.dumps({"key_deadlines": [
            {"action": "Register", "deadline_type": "after_enactment"},
            "file a report by June 1",  # not a dict
        ]})
        result = self.agent.parse_response(raw)
        assert len(result["key_deadlines"]) == 1
        assert result["key_deadlines"][0]["action"] == "Register"

    def test_key_deadline_string_int_coercion(self):
        raw = json.dumps({"key_deadlines": [{
            "action": "Audit",
            "deadline_type": "recurring",
            "relative_days": "90 days",
            "frequency_months": "12 months",
        }]})
        result = self.agent.parse_response(raw)
        d = result["key_deadlines"][0]
        assert d["relative_days"] == 90
        assert d["frequency_months"] == 12

    def test_key_deadline_non_int_becomes_none(self):
        raw = json.dumps({"key_deadlines": [{
            "action": "Report",
            "deadline_type": "one_time",
            "relative_days": ["thirty"],
        }]})
        result = self.agent.parse_response(raw)
        assert result["key_deadlines"][0]["relative_days"] is None

    def test_top_level_int_string_coercion(self):
        raw = json.dumps({
            "impact_assessment_frequency_months": "12 months",
            "consumer_request_response_days": "45 days",
            "cure_period_days": "30",
        })
        result = self.agent.parse_response(raw)
        assert result["impact_assessment_frequency_months"] == 12
        assert result["consumer_request_response_days"] == 45
        assert result["cure_period_days"] == 30

    def test_top_level_non_int_becomes_none(self):
        raw = json.dumps({"impact_assessment_frequency_months": ["annually"]})
        result = self.agent.parse_response(raw)
        assert result["impact_assessment_frequency_months"] is None

    def test_all_null_payload(self):
        raw = json.dumps({
            "law_effective_date": None,
            "enforcement_start_date": None,
            "sunset_date": None,
            "key_deadlines": [],
            "impact_assessment_frequency_months": None,
            "consumer_request_response_days": None,
            "cure_period_days": None,
            "first_compliance_action": None,
        })
        result = self.agent.parse_response(raw)
        assert result["key_deadlines"] == []
        assert result["impact_assessment_frequency_months"] is None
