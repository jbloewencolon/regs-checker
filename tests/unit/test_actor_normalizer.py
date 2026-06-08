"""Unit tests for B1.5 actor field sanitizer."""

import pytest
from src.core.actor_normalizer import (
    INVALID_NONACTOR_TERMS,
    is_invalid_actor,
    sanitize_normalized_actor,
)


class TestSanitizeNormalizedActor:
    def test_valid_actor_code_passes_through(self):
        assert sanitize_normalized_actor("deployer") == "deployer"
        assert sanitize_normalized_actor("controller") == "controller"
        assert sanitize_normalized_actor("regulator") == "regulator"

    def test_none_returns_none(self):
        assert sanitize_normalized_actor(None) is None

    def test_empty_string_returns_none(self):
        assert sanitize_normalized_actor("") is None
        assert sanitize_normalized_actor("   ") is None

    def test_known_invalid_terms_return_none(self):
        for term in ("contract", "document", "website", "program"):
            assert sanitize_normalized_actor(term) is None, f"Expected None for '{term}'"

    def test_case_insensitive_invalid_match(self):
        assert sanitize_normalized_actor("CONTRACT") is None
        assert sanitize_normalized_actor("Document") is None

    def test_garbled_with_double_space_returns_none(self):
        assert sanitize_normalized_actor("deploy   ployer") is None

    def test_garbled_with_tab_returns_none(self):
        assert sanitize_normalized_actor("covered\tentity") is None

    def test_very_short_string_returns_none(self):
        assert sanitize_normalized_actor("op") is None
        assert sanitize_normalized_actor("x") is None

    def test_three_chars_boundary(self):
        # 3 chars → filtered (≤ 3), 4 chars → passes
        assert sanitize_normalized_actor("opa") is None
        assert sanitize_normalized_actor("firm") == "firm"

    def test_strips_whitespace_before_check(self):
        assert sanitize_normalized_actor("  deployer  ") == "deployer"
        assert sanitize_normalized_actor("  contract  ") is None

    def test_operat_garbled_value(self):
        assert sanitize_normalized_actor("operat") is None  # in INVALID set

    def test_socia_garbled_value(self):
        assert sanitize_normalized_actor("socia") is None  # in INVALID set

    def test_software_tool_invalid(self):
        assert sanitize_normalized_actor("software_tool") is None

    def test_automated_decision_making_system_invalid(self):
        assert sanitize_normalized_actor("automated decision-making system") is None


class TestIsInvalidActor:
    def test_invalid_term_is_true(self):
        assert is_invalid_actor("contract") is True
        assert is_invalid_actor("document") is True

    def test_valid_term_is_false(self):
        assert is_invalid_actor("deployer") is False
        assert is_invalid_actor("developer") is False

    def test_none_is_not_invalid(self):
        assert is_invalid_actor(None) is False


class TestPydanticIntegration:
    """Validates that the field_validator hooks fire in the schemas."""

    def test_obligation_payload_sanitizes_subject_normalized(self):
        from src.schemas.extraction import ObligationPayload

        p = ObligationPayload(
            subject="contract",
            subject_normalized="contract",
            modality="shall",
            action="comply",
        )
        assert p.subject == "contract"        # raw preserved
        assert p.subject_normalized is None   # sanitized

    def test_obligation_payload_valid_normalized_passes(self):
        from src.schemas.extraction import ObligationPayload

        p = ObligationPayload(
            subject="Developer",
            subject_normalized="developer",
            modality="shall",
            action="comply",
        )
        assert p.subject_normalized == "developer"

    def test_actor_mapping_sanitizes_actor_type(self):
        from src.schemas.extraction import ActorMapping

        m = ActorMapping(actor_name="automated decision-making system", actor_type="software_tool")
        assert m.actor_name == "automated decision-making system"  # raw preserved
        assert m.actor_type is None

    def test_rights_protection_sanitizes_right_holder_normalized(self):
        from src.schemas.extraction import RightsProtectionPayload

        p = RightsProtectionPayload(
            right_holder="document",
            right_holder_normalized="document",
            right_type="notice",
            right_description="right to notice",
        )
        assert p.right_holder == "document"
        assert p.right_holder_normalized is None

    def test_compliance_mechanism_sanitizes_responsible_party_normalized(self):
        from src.schemas.extraction import ComplianceMechanismPayload

        p = ComplianceMechanismPayload(
            mechanism_type="reporting",
            description="quarterly report",
            responsible_party="program",
            responsible_party_normalized="program",
        )
        assert p.responsible_party == "program"
        assert p.responsible_party_normalized is None
