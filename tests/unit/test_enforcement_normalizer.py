"""Unit tests for the enforcement normalizer (Phase 2c).

Covers the pure merge function: source precedence, obligation coalescing,
provenance tracking, and the sparsity fix (a fact in any one source surfaces).
"""

from src.core.enforcement_normalizer import (
    ENFORCEMENT_FIELDS,
    normalize_enforcement,
)


class TestNormalizeEnforcement:
    def test_empty_sources_yields_no_enforcement(self):
        rec = normalize_enforcement()
        assert rec["_has_enforcement"] is False
        assert rec["_sources_present"] == []
        for f in ENFORCEMENT_FIELDS:
            assert rec[f] is None

    def test_orrick_wins_over_lower_sources(self):
        rec = normalize_enforcement(
            bill_level={"enforcing_body": "Department of Commerce"},
            orrick_facts={"enforcing_body": "Attorney General"},
        )
        assert rec["enforcing_body"] == "Attorney General"
        assert rec["_provenance"]["enforcing_body"] == "orrick"

    def test_bill_level_wins_over_obligation(self):
        rec = normalize_enforcement(
            bill_level={"enforcing_body": "Department of Commerce"},
            obligation_enforcements=[{"enforcing_body": "City Clerk"}],
        )
        assert rec["enforcing_body"] == "Department of Commerce"
        assert rec["_provenance"]["enforcing_body"] == "bill_level"

    def test_field_level_merge_across_sources(self):
        """Each field independently takes its first non-null source — the
        core sparsity fix: facts from different sources combine."""
        rec = normalize_enforcement(
            bill_level={"max_civil_penalty_usd": 10000},
            obligation_enforcements=[{"cure_period_days": 60}],
            orrick_facts={"enforcing_body": "Attorney General"},
        )
        assert rec["enforcing_body"] == "Attorney General"
        assert rec["max_civil_penalty_usd"] == 10000
        assert rec["cure_period_days"] == 60
        assert rec["_provenance"] == {
            "enforcing_body": "orrick",
            "max_civil_penalty_usd": "bill_level",
            "cure_period_days": "obligation",
        }
        assert set(rec["_sources_present"]) == {"orrick", "bill_level", "obligation"}
        assert rec["_has_enforcement"] is True

    def test_sources_present_ordered_by_precedence(self):
        rec = normalize_enforcement(
            bill_level={"penalty_per": "violation"},
            orrick_facts={"enforcing_body": "AG"},
        )
        # orrick before bill_level regardless of arg order
        assert rec["_sources_present"] == ["orrick", "bill_level"]

    def test_obligation_penalty_takes_maximum(self):
        """Across many partial obligation rows, the largest stated penalty
        is the law's ceiling."""
        rec = normalize_enforcement(
            obligation_enforcements=[
                {"max_civil_penalty_usd": 5000},
                {"max_civil_penalty_usd": 25000},
                {"max_civil_penalty_usd": None},
            ],
        )
        assert rec["max_civil_penalty_usd"] == 25000
        assert rec["_provenance"]["max_civil_penalty_usd"] == "obligation"

    def test_obligation_first_non_null_for_scalars(self):
        rec = normalize_enforcement(
            obligation_enforcements=[
                {"enforcing_body": None},
                {"enforcing_body": "Attorney General"},
                {"enforcing_body": "Ignored Second"},
            ],
        )
        assert rec["enforcing_body"] == "Attorney General"

    def test_empty_string_is_treated_as_null(self):
        rec = normalize_enforcement(
            orrick_facts={"enforcing_body": ""},
            bill_level={"enforcing_body": "Attorney General"},
        )
        assert rec["enforcing_body"] == "Attorney General"
        assert rec["_provenance"]["enforcing_body"] == "bill_level"

    def test_boolean_false_is_preserved(self):
        """A trusted False (e.g. Orrick affirming no private right of action)
        must win over a lower source's True — False is a real finding."""
        rec = normalize_enforcement(
            orrick_facts={"private_right_of_action": False},
            obligation_enforcements=[{"private_right_of_action": True}],
        )
        assert rec["private_right_of_action"] is False
        assert rec["_provenance"]["private_right_of_action"] == "orrick"

    def test_internal_source_fields_ignored(self):
        """The Orrick parser tags its dict with _source — that must not leak
        into the canonical fields."""
        rec = normalize_enforcement(
            orrick_facts={"enforcing_body": "AG", "_source": "orrick"},
        )
        assert rec["enforcing_body"] == "AG"
        assert "_source" not in ENFORCEMENT_FIELDS

    def test_iapp_precedence_below_orrick(self):
        rec = normalize_enforcement(
            orrick_facts={"enforcing_body": "Orrick Body"},
            iapp_facts={"enforcing_body": "IAPP Body"},
        )
        assert rec["enforcing_body"] == "Orrick Body"
        assert rec["_provenance"]["enforcing_body"] == "orrick"

    def test_iapp_fills_when_orrick_absent(self):
        rec = normalize_enforcement(
            iapp_facts={"enforcing_body": "IAPP Body"},
        )
        assert rec["enforcing_body"] == "IAPP Body"
        assert rec["_provenance"]["enforcing_body"] == "iapp"
