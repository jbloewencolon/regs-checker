"""Unit tests for legal-context classification (Phase 2d)."""

from src.core.legal_context import (
    AGENCY_JURISDICTION,
    CONSTITUTIONAL_LIMIT,
    CROSS_LAW_REFERENCE,
    INTERSTATE_CONFLICT,
    LEGAL_CONTEXT_TYPES,
    TRUE_PREEMPTION,
    UNCLASSIFIED,
    classify_legal_context,
    is_low_value,
)


class TestClassifyLegalContext:
    def test_federal_preemption_is_true_preemption(self):
        out = classify_legal_context({"conflict_type": "federal_preemption"})
        assert out["legal_context_type"] == TRUE_PREEMPTION
        assert out["display"] is True
        assert out["raw_conflict_type"] == "federal_preemption"

    def test_dormant_commerce_clause_is_true_preemption(self):
        out = classify_legal_context({"conflict_type": "dormant_commerce_clause"})
        assert out["legal_context_type"] == TRUE_PREEMPTION

    def test_first_amendment_is_constitutional_limit(self):
        out = classify_legal_context({"conflict_type": "first_amendment"})
        assert out["legal_context_type"] == CONSTITUTIONAL_LIMIT

    def test_interstate_commerce_is_interstate_conflict(self):
        out = classify_legal_context({"conflict_type": "interstate_commerce"})
        assert out["legal_context_type"] == INTERSTATE_CONFLICT

    def test_cross_state_conflict_is_interstate_conflict(self):
        out = classify_legal_context({"conflict_type": "cross_state_conflict"})
        assert out["legal_context_type"] == INTERSTATE_CONFLICT

    def test_agency_jurisdiction_maps_through(self):
        out = classify_legal_context({"conflict_type": "agency_jurisdiction"})
        assert out["legal_context_type"] == AGENCY_JURISDICTION
        assert out["display"] is True

    def test_other_is_unclassified_and_hidden(self):
        out = classify_legal_context({"conflict_type": "other"})
        assert out["legal_context_type"] == UNCLASSIFIED
        assert out["display"] is False

    def test_unknown_conflict_type_is_unclassified(self):
        out = classify_legal_context({"conflict_type": "made_up_value"})
        assert out["legal_context_type"] == UNCLASSIFIED
        assert out["display"] is False

    def test_other_with_cross_law_refs_becomes_reference(self):
        """A bare 'other' that only carries citations is a cross-law
        reference, not a hidden unclassified row."""
        out = classify_legal_context({
            "conflict_type": "other",
            "cross_law_refs": [{"reference_type": "incorporates", "law_name": "CCPA"}],
        })
        assert out["legal_context_type"] == CROSS_LAW_REFERENCE
        assert out["display"] is True

    def test_other_with_preemption_language_stays_unclassified(self):
        """If there's actual preemption language, the reference fallback does
        not fire — it's not merely a citation."""
        out = classify_legal_context({
            "conflict_type": "other",
            "cross_law_refs": [{"reference_type": "conflicts_with"}],
            "preemption_language": "nothing in this section shall preempt federal law",
        })
        assert out["legal_context_type"] == UNCLASSIFIED

    def test_other_with_related_authority_stays_unclassified(self):
        out = classify_legal_context({
            "conflict_type": "other",
            "cross_law_refs": [{"reference_type": "subject_to"}],
            "related_authority": "Federal EO on AI",
        })
        assert out["legal_context_type"] == UNCLASSIFIED

    def test_case_insensitive_and_whitespace(self):
        out = classify_legal_context({"conflict_type": "  Federal_Preemption  "})
        assert out["legal_context_type"] == TRUE_PREEMPTION

    def test_missing_conflict_type(self):
        out = classify_legal_context({})
        assert out["legal_context_type"] == UNCLASSIFIED
        assert out["raw_conflict_type"] is None

    def test_is_low_value_helper(self):
        assert is_low_value({"conflict_type": "other"}) is True
        assert is_low_value({"conflict_type": "federal_preemption"}) is False

    def test_all_mapped_categories_are_known(self):
        for raw in (
            "federal_preemption", "dormant_commerce_clause", "first_amendment",
            "interstate_commerce", "cross_state_conflict", "agency_jurisdiction",
            "other",
        ):
            out = classify_legal_context({"conflict_type": raw})
            assert out["legal_context_type"] in LEGAL_CONTEXT_TYPES


class TestPayloadAdapterIntegration:
    def test_adapter_adds_legal_context_fields(self):
        from src.core.payload_adapter import adapt_payload_for_sync

        adapted = adapt_payload_for_sync(
            "preemption_signal",
            {
                "conflict_type": "federal_preemption",
                "description": "Federal EO preempts state rule",
                "severity": "high",
            },
        )
        assert adapted["legal_context_type"] == TRUE_PREEMPTION
        assert adapted["display"] is True
        assert adapted["conflict_type"] == "federal_preemption"

    def test_adapter_flags_low_value_other(self):
        from src.core.payload_adapter import adapt_payload_for_sync

        adapted = adapt_payload_for_sync(
            "preemption_signal",
            {"conflict_type": "other", "description": "vague"},
        )
        assert adapted["legal_context_type"] == UNCLASSIFIED
        assert adapted["display"] is False
