"""Unit tests for legal-context classification (Phase 2d) and the QA-6
preemption credibility assessment."""

from src.core.legal_context import (
    AGENCY_JURISDICTION,
    CONSTITUTIONAL_LIMIT,
    CROSS_LAW_REFERENCE,
    INTERSTATE_CONFLICT,
    LEGAL_CONTEXT_TYPES,
    TRUE_PREEMPTION,
    UNCLASSIFIED,
    assess_preemption_credibility,
    classify_legal_context,
    is_low_value,
)


class TestClassifyLegalContext:
    def test_federal_preemption_is_true_preemption(self):
        out = classify_legal_context(
            {
                "conflict_type": "federal_preemption",
                "preemption_language": "nothing in this act shall preempt federal law",
            }
        )
        assert out["legal_context_type"] == TRUE_PREEMPTION
        assert out["display"] is True
        assert out["raw_conflict_type"] == "federal_preemption"

    def test_unanchored_federal_preemption_hidden(self):
        """QA-6: a conflict-asserting type with no clause, no citation, and
        no named other state is not credible and gets hidden."""
        out = classify_legal_context({"conflict_type": "federal_preemption"})
        assert out["legal_context_type"] == TRUE_PREEMPTION
        assert out["display"] is False
        assert out["credible"] is False
        assert out["credibility_reason"] == "no_external_authority"

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
        # Anchored conflict assertion is high-value…
        assert is_low_value({
            "conflict_type": "federal_preemption",
            "related_authority": "47 U.S.C. § 230",
        }) is False
        # …an unanchored one is not (QA-6).
        assert is_low_value({"conflict_type": "federal_preemption"}) is True

    def test_all_mapped_categories_are_known(self):
        for raw in (
            "federal_preemption", "dormant_commerce_clause", "first_amendment",
            "interstate_commerce", "cross_state_conflict", "agency_jurisdiction",
            "other",
        ):
            out = classify_legal_context({"conflict_type": raw})
            assert out["legal_context_type"] in LEGAL_CONTEXT_TYPES


class TestAssessPreemptionCredibility:
    """QA-6 — fixtures are real payload shapes from the 2026-07-13 run
    (81 preemption signals, 49 of which these rules reject on replay)."""

    def test_grounded_savings_clause_is_credible(self):
        # AL HB172 id 684 — genuine §230 savings clause.
        out = assess_preemption_credibility({
            "conflict_type": "federal_preemption",
            "description": "This act does not alter any rights created by 47 U.S.C.",
            "related_authority": "47 U.S.C. § 230",
            "preemption_language": "This act does not alter any rights, "
            "obligations, or immunities created by 47 U.S.C. § 230",
            "jurisdiction": "AL",
        })
        assert out == {"credible": True, "reason": None}

    def test_savings_clause_negation_is_not_self_negation(self):
        # AB 325 id 731 — "does not preempt" is the statute's wording, not
        # the model's conclusion; preemption_language wins.
        out = assess_preemption_credibility({
            "conflict_type": "federal_preemption",
            "description": "This section does not preempt federal antitrust laws.",
            "preemption_language": "does not preempt any federal antitrust law",
            "jurisdiction": "CA",
        })
        assert out["credible"] is True

    def test_self_negating_description_dropped(self):
        # SB 926 ids 177-181.
        out = assess_preemption_credibility({
            "conflict_type": "cross_state_conflict",
            "description": "This passage references the Welfare and "
            "Institutions Code and does not appear to conflict with federal law.",
            "related_authority": "California Welfare and Institutions Code",
            "jurisdiction": "CA",
        })
        assert out == {"credible": False, "reason": "self_negating_description"}

    def test_incorporation_is_not_conflict(self):
        # SB 11 ids 777/778 — incorporation by reference reported as conflict.
        out = assess_preemption_credibility({
            "conflict_type": "cross_state_conflict",
            "description": "California law SB 11 incorporates federal law "
            "regarding digital replicas.",
            "related_authority": "Dec 2025 Federal EO on AI",
            "jurisdiction": "CA",
        })
        assert out["credible"] is False

    def test_own_state_code_citation_dropped(self):
        # SB 926 ids 200-203 — the dominant junk pattern.
        out = assess_preemption_credibility({
            "conflict_type": "cross_state_conflict",
            "description": "This passage references the Penal Code, which may "
            "conflict with federal laws or other states' laws.",
            "related_authority": "California Penal Code",
            "cross_law_refs": [
                {"reference_type": "incorporates",
                 "law_name": "California Penal Code", "section": "647"},
            ],
            "jurisdiction": "CA",
        })
        assert out == {"credible": False, "reason": "no_external_authority"}

    def test_parroted_prompt_example_authority_dropped(self):
        # AB 1836 id 758 (tier A on the run!) — authority parroted from the
        # prompt's example, description names nothing concrete.
        out = assess_preemption_credibility({
            "conflict_type": "federal_preemption",
            "description": "This law may be preempted by federal laws "
            "regarding interstate commerce and the use of digital replicas.",
            "related_authority": "Dec 2025 Federal EO on AI",
            "jurisdiction": "CA",
        })
        assert out["credible"] is False

    def test_parroted_constitution_example_with_mojibake_dropped(self):
        # AB 1836 id 757 — 'Â§' mojibake variant of the same parroting.
        out = assess_preemption_credibility({
            "conflict_type": "cross_state_conflict",
            "description": "This California law may conflict with federal "
            "laws or other state laws regarding digital replicas.",
            "related_authority": "US Constitution Art. I Â§ 8",
            "jurisdiction": "CA",
        })
        assert out["credible"] is False

    def test_federal_citation_in_cross_law_refs_anchors(self):
        # TMP-CA-EMPLOYMENTANDS id 490 — Title VII citation carried in refs.
        out = assess_preemption_credibility({
            "conflict_type": "cross_state_conflict",
            "description": "The passage references federal immigration law, "
            "which may conflict with California state law.",
            "related_authority": "US Constitution Art. I § 8",
            "cross_law_refs": [
                {"reference_type": "conflicts_with",
                 "law_name": "42 U.S.C. § 2000e"},
            ],
            "jurisdiction": "CA",
        })
        assert out["credible"] is True

    def test_public_law_citation_anchors(self):
        # SB 1120 id 626 — HIPAA as Public Law 104-191.
        out = assess_preemption_credibility({
            "conflict_type": "cross_state_conflict",
            "description": "Potential conflict with federal Health Insurance "
            "Portability and Accountability Act",
            "related_authority": "Public Law 104-191",
            "jurisdiction": "CA",
        })
        assert out["credible"] is True

    def test_named_other_state_anchors(self):
        out = assess_preemption_credibility({
            "conflict_type": "cross_state_conflict",
            "description": "Requires disclosure of training data that "
            "Colorado's AI Act treats as a trade secret.",
            "related_authority": "Colorado AI Act (SB 24-205)",
            "jurisdiction": "CA",
        })
        assert out["credible"] is True

    def test_own_state_name_does_not_anchor(self):
        # SB 926 ids 157/159 — "California's amendment ... may conflict".
        out = assess_preemption_credibility({
            "conflict_type": "cross_state_conflict",
            "description": "California's amendment of CSAM laws may conflict "
            "with federal laws or other states' laws.",
            "related_authority": "California SB 926",
            "jurisdiction": "CA",
        })
        assert out["credible"] is False

    def test_unknown_jurisdiction_disables_state_anchoring(self):
        # Without knowing which state is "own", a state name alone must not
        # anchor (it could be the law's own name in the description).
        out = assess_preemption_credibility({
            "conflict_type": "cross_state_conflict",
            "description": "California law may conflict with other laws.",
            "related_authority": "California Civil Code",
        })
        assert out["credible"] is False

    def test_non_conflict_asserting_types_pass_through(self):
        # first_amendment / agency_jurisdiction claim no second jurisdiction.
        for ct in ("first_amendment", "agency_jurisdiction", "other"):
            out = assess_preemption_credibility(
                {"conflict_type": ct, "description": "may raise concerns",
                 "jurisdiction": "CA"}
            )
            assert out["credible"] is True, ct


class TestPayloadAdapterIntegration:
    def test_adapter_adds_legal_context_fields(self):
        from src.core.payload_adapter import adapt_payload_for_sync

        adapted = adapt_payload_for_sync(
            "preemption_signal",
            {
                "conflict_type": "federal_preemption",
                "description": "Federal EO 14110 preempts state rule",
                "related_authority": "Executive Order 14110",
                "severity": "high",
            },
        )
        assert adapted["legal_context_type"] == TRUE_PREEMPTION
        assert adapted["display"] is True
        assert adapted["conflict_type"] == "federal_preemption"

    def test_adapter_hides_unanchored_conflict_assertion(self):
        """QA-6 retroactive repair: stored junk rows get display=False at
        sync time without re-extraction."""
        from src.core.payload_adapter import adapt_payload_for_sync

        adapted = adapt_payload_for_sync(
            "preemption_signal",
            {
                "conflict_type": "cross_state_conflict",
                "description": "This passage references the Penal Code, which "
                "may conflict with federal laws or other states' laws.",
                "related_authority": "California Penal Code",
                "jurisdiction": "CA",
            },
        )
        assert adapted["legal_context_type"] == INTERSTATE_CONFLICT
        assert adapted["display"] is False

    def test_adapter_flags_low_value_other(self):
        from src.core.payload_adapter import adapt_payload_for_sync

        adapted = adapt_payload_for_sync(
            "preemption_signal",
            {"conflict_type": "other", "description": "vague"},
        )
        assert adapted["legal_context_type"] == UNCLASSIFIED
        assert adapted["display"] is False
