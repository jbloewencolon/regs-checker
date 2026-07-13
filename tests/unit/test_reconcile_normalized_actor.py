"""Tests for QA-3: reconcile LLM-normalized actor fields with the raw phrase.

Real misfire (AZ SB 1359, 2026-07-12 run): compliance_mechanism returned
responsible_party "person who acts as a creator" with
responsible_party_normalized "developer" — the prompt offered only four
buckets, so the model force-fit the nearest one. reconcile_normalized_actor
keeps an LLM value only when the raw phrase supports it, otherwise defers
to the ratified actor alias table or an honest null.
"""

from __future__ import annotations

from src.core.actor_normalizer import reconcile_normalized_actor


class TestReconcileNormalizedActor:
    def test_real_misfire_creator_normalized_to_developer_nulled(self):
        assert reconcile_normalized_actor(
            "person who acts as a creator", "developer"
        ) is None

    def test_lexically_supported_value_kept(self):
        assert reconcile_normalized_actor(
            "developer of the AI system", "developer"
        ) == "developer"

    def test_plural_raw_phrase_supports_singular_value(self):
        assert reconcile_normalized_actor(
            "deployers of automated decision systems", "deployer"
        ) == "deployer"

    def test_unsupported_value_replaced_by_alias_table_hit(self):
        """Raw phrase is a genuine alias — its code wins over the LLM guess."""
        result = reconcile_normalized_actor("operator", "vendor")
        assert result == "operator"

    def test_alias_table_agreement_keeps_llm_value(self):
        """Both sides mapping to the same canonical code is agreement, even
        without a lexical substring hit (e.g. vendor→provider folds)."""
        # "developer" raw maps to "developer"; LLM said "developer" — trivially kept.
        assert reconcile_normalized_actor("Developer", "developer") == "developer"

    def test_no_raw_phrase_passes_llm_value_through(self):
        assert reconcile_normalized_actor(None, "deployer") == "deployer"
        assert reconcile_normalized_actor("", "deployer") == "deployer"

    def test_garbled_llm_value_still_sanitized(self):
        # Existing garble filter applies before reconciliation.
        assert reconcile_normalized_actor("developer", "de   veloper") == "developer"

    def test_both_unknown_returns_none(self):
        assert reconcile_normalized_actor(
            "the citizens advisory board", "vendor"
        ) is None

    def test_none_normalized_with_alias_raw_backfills(self):
        """LLM omitted the normalized field but the raw phrase is a known
        alias — backfill deterministically."""
        assert reconcile_normalized_actor("operator", None) == "operator"


class TestComplianceMechanismPayloadIntegration:
    def test_model_validator_repairs_force_fit(self):
        from src.schemas.extraction import ComplianceMechanismPayload

        payload = ComplianceMechanismPayload(
            mechanism_type="disclosure",
            description="Synthetic media message includes a clear disclosure.",
            responsible_party="person who acts as a creator",
            responsible_party_normalized="developer",
        )
        assert payload.responsible_party_normalized is None
        # Raw field is never touched — provenance preserved.
        assert payload.responsible_party == "person who acts as a creator"

    def test_model_validator_keeps_supported_value(self):
        from src.schemas.extraction import ComplianceMechanismPayload

        payload = ComplianceMechanismPayload(
            mechanism_type="reporting",
            description="Annual impact assessment report.",
            responsible_party="the developer of the high-risk system",
            responsible_party_normalized="developer",
        )
        assert payload.responsible_party_normalized == "developer"


class TestPayloadAdapterIntegration:
    def test_sync_adapter_repairs_stored_rows_retroactively(self):
        from src.core.payload_adapter import _adapt_compliance_mechanism

        stored = {
            "mechanism_type": "disclosure",
            "description": "d",
            "responsible_party": "person who acts as a creator",
            "responsible_party_normalized": "developer",
        }
        adapted = _adapt_compliance_mechanism(stored)
        assert adapted["responsible_party_normalized"] is None
        assert adapted["responsible_party"] == "person who acts as a creator"
