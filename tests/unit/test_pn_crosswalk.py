"""Tests for PNE-2 RC→Policy Navigator crosswalk derivations."""

from __future__ import annotations

import pytest
from src.core import pn_crosswalk
from src.core.pn_crosswalk import (
    derive_actor_role,
    derive_obligation_type,
    derive_trigger,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    pn_crosswalk.reload_cache()
    yield
    pn_crosswalk.reload_cache()


class TestDeriveActorRole:
    def test_direct_code_equivalents(self):
        assert derive_actor_role("a developer", "developer") == ("developer", "developer")
        assert derive_actor_role("a deployer", "deployer") == ("deployer", "deployer")
        assert derive_actor_role("a provider", "provider") == ("provider", "provider")

    def test_alias_recovers_employer_from_raw_term(self):
        # RC folds employer under deployer; alias-aware crosswalk recovers PN's
        # first-class 'employer' from the raw subject.
        rc, pn = derive_actor_role("an employer", "deployer")
        assert rc == "deployer"
        assert pn == "employer"

    def test_alias_matches_plural_and_article(self):
        assert derive_actor_role("employers", "deployer")[1] == "employer"
        assert derive_actor_role("the vendor", "provider")[1] == "vendor"

    def test_alias_does_not_false_match_substring(self):
        # "employment agency" must NOT resolve to employer (word-boundary).
        rc, pn = derive_actor_role("employment agency", "deployer")
        assert pn == "deployer"

    def test_enforcer_has_no_pn_role(self):
        # regulator is an enforcer, not a regulated actor → null PN role so it
        # never displays as a regulated party (Ask 1's core point).
        assert derive_actor_role("Attorney General", "regulator") == ("regulator", None)

    def test_individual_has_no_pn_role(self):
        assert derive_actor_role("a consumer", "individual") == ("individual", None)

    def test_operator_maps_to_deployer(self):
        assert derive_actor_role("an operator", "operator") == ("operator", "deployer")

    def test_empty_subject_returns_nulls(self):
        assert derive_actor_role(None, None) == (None, None)
        assert derive_actor_role("", "  ") == (None, None)

    def test_unmapped_code_falls_back_to_rc_fallback_null_pn(self):
        # A subject that normalizes to regulated_entity has a PN role (deployer),
        # but an unknown term normalizes to the RC fallback regulated_entity.
        rc, pn = derive_actor_role("some novel covered party", None)
        assert rc == "regulated_entity"
        assert pn == "deployer"


class TestDeriveObligationType:
    def test_impact_assessment_maps_to_assessment(self):
        rc, pn = derive_obligation_type("conduct an impact_assessment before deployment")
        assert rc == "impact_assessment"
        assert pn == "assessment"

    def test_disclosure_maps_to_disclosure(self):
        rc, pn = derive_obligation_type("provide disclosure to affected individuals")
        assert rc == "disclosure_to_user"
        assert pn == "disclosure"

    def test_registration_maps_to_registration(self):
        rc, pn = derive_obligation_type("complete registration with the state registry")
        assert rc == "registration"
        assert pn == "registration"

    def test_unmatched_action_yields_general_and_null_pn(self):
        rc, pn = derive_obligation_type("do the thing described elsewhere")
        assert rc == "obligation_general"
        assert pn is None

    def test_empty_action(self):
        assert derive_obligation_type(None) == (None, None)
        assert derive_obligation_type("   ") == (None, None)

    def test_matches_concept_layer_classifier(self):
        # PNE-2b reuses concept_grouping._classify_obligation_family, so the
        # sync-time family must equal what the concept layer derives.
        from src.core.concept_grouping import _classify_obligation_family

        action = "maintain record_keeping of all training data"
        assert derive_obligation_type(action)[0] == _classify_obligation_family(action)


class TestDeriveTrigger:
    def test_more_than_is_gt_not_gte(self):
        # Boundary fidelity: "more than 50" is strictly >50; must not collapse
        # to gte (which would wrongly include 50).
        t = derive_trigger({
            "threshold_type": "numeric",
            "threshold_value": "50",
            "threshold_unit": "employees",
            "threshold_condition": "more than 50 employees",
        })
        assert t["trigger_type"] == "employee_count"
        assert t["trigger_operator"] == "gt"
        assert t["trigger_value"] == 50.0
        assert t["trigger_condition_raw"] == "more than 50 employees"

    def test_at_least_is_gte(self):
        t = derive_trigger({
            "threshold_value": "$25 million",
            "threshold_condition": "at least $25 million in annual revenue",
        })
        assert t["trigger_type"] == "revenue"
        assert t["trigger_operator"] == "gte"
        assert t["trigger_value"] == 25_000_000.0

    def test_compute_flops_caret_notation(self):
        t = derive_trigger({
            "threshold_type": "compute",
            "compute_flops": 1e26,
            "threshold_condition": "models trained above 10^26 FLOPS",
        })
        assert t["trigger_type"] == "compute"
        assert t["trigger_operator"] == "gt"
        assert t["trigger_value"] == 1e26

    def test_consumer_count_with_comma_value(self):
        t = derive_trigger({
            "threshold_value": "100,000",
            "threshold_condition": "100,000 or more consumers",
        })
        assert t["trigger_type"] == "consumer_count"
        assert t["trigger_operator"] == "gte"
        assert t["trigger_value"] == 100_000.0

    def test_no_signal_returns_none(self):
        assert derive_trigger({"threshold_type": None, "threshold_value": None}) is None

    def test_value_but_no_condition_defaults_gte(self):
        t = derive_trigger({"threshold_type": "employee count", "threshold_value": "500"})
        assert t["trigger_operator"] == "gte"
        assert t["trigger_value"] == 500.0

    def test_unparseable_value_kept_as_string_not_wrong_number(self):
        t = derive_trigger({
            "threshold_type": "entity_type",
            "threshold_value": "high-risk systems",
            "threshold_condition": "applies to high-risk systems",
        })
        # No fabricated number — the raw string is preserved.
        assert t["trigger_value"] == "high-risk systems"

    def test_sector_trigger_type(self):
        t = derive_trigger({
            "threshold_type": "categorical",
            "sector_applicability": ["employment", "healthcare"],
            "threshold_condition": "applies in the employment sector",
        })
        assert t["trigger_type"] == "sector"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
