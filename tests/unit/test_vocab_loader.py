"""Unit tests for B4 vocabulary loader."""

import pytest
from src.core.vocab_loader import (
    flush_unrecognized,
    get_canonical_codes,
    normalize,
    reload_cache,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Ensure a clean cache for each test."""
    reload_cache()
    yield
    reload_cache()


class TestNormalize:
    def test_known_actor_term_returns_code(self):
        # "deployer" is in actor_aliases.csv
        code = normalize("actor", "deployer")
        assert code == "deployer"

    def test_known_actor_term_case_insensitive(self):
        code = normalize("actor", "DEPLOYER")
        assert code == "deployer"

    def test_known_actor_term_with_whitespace(self):
        code = normalize("actor", "  deployer  ")
        assert code == "deployer"

    def test_unknown_actor_returns_fallback(self):
        code = normalize("actor", "totally_made_up_term_xyz")
        assert code == "regulated_entity"

    def test_unknown_actor_queues_unrecognized(self):
        normalize("actor", "xyz_unknown_actor_123")
        items = flush_unrecognized()
        assert any(i.raw_term == "xyz_unknown_actor_123" and i.dimension == "actor" for i in items)

    def test_none_value_returns_fallback(self):
        code = normalize("actor", None)
        assert code == "regulated_entity"

    def test_legal_context_known_term(self):
        # "unclassified" is a canonical code in legal_context
        code = normalize("legal_context", "unclassified")
        assert code == "unclassified"

    def test_legal_context_unknown_returns_fallback(self):
        code = normalize("legal_context", "completely_unknown")
        assert code == "unclassified"

    def test_flush_clears_queue(self):
        normalize("actor", "unknown_actor_abc")
        items = flush_unrecognized()
        assert len(items) > 0
        # Second flush should be empty
        assert flush_unrecognized() == []

    def test_unrecognized_item_has_provisional_code(self):
        normalize("actor", "unknown_xyz")
        items = flush_unrecognized()
        item = next(i for i in items if i.raw_term == "unknown_xyz")
        assert item.provisional_code == "regulated_entity"


class TestGetCanonicalCodes:
    def test_actor_codes_includes_deployer(self):
        codes = get_canonical_codes("actor")
        assert "deployer" in codes

    def test_actor_codes_includes_all_13(self):
        codes = get_canonical_codes("actor")
        expected = {
            "developer", "provider", "deployer", "operator", "distributor",
            "compute_provider", "controller", "processor", "data_broker",
            "regulator", "government_agency", "individual", "regulated_entity",
        }
        assert expected.issubset(set(codes))

    def test_legal_context_codes_includes_true_preemption(self):
        codes = get_canonical_codes("legal_context")
        assert "true_preemption" in codes

    def test_nonexistent_dimension_returns_empty(self):
        codes = get_canonical_codes("totally_made_up_dimension")
        assert codes == []

    def test_obligation_family_codes_includes_documentation(self):
        codes = get_canonical_codes("obligation_family")
        assert "documentation" in codes
        assert len(codes) == 21

    def test_rights_codes_includes_10_codes(self):
        codes = get_canonical_codes("rights")
        expected = {
            "notice", "explanation", "opt_out", "appeal", "deletion",
            "human_review", "non_discrimination", "portability", "access",
            "correction",
        }
        assert expected == set(codes)

    def test_rights_correction_normalizes(self):
        assert normalize("rights", "correction") == "correction"

    def test_rights_correct_alias_normalizes(self):
        assert normalize("rights", "correct") == "correction"

    def test_legal_context_firstamendment_garbled(self):
        # 'firstamendment' (no underscore) is a garbled extraction variant
        assert normalize("legal_context", "firstamendment") == "constitutional_limit"

    def test_enforcement_civil_penalty_normalizes(self):
        assert normalize("enforcement", "civil penalty") == "civil_penalty"

    def test_enforcement_misdemeanor_normalizes(self):
        assert normalize("enforcement", "misdemeanor") == "criminal_penalty"

    def test_enforcement_attorney_general_normalizes(self):
        assert normalize("enforcement", "attorney general") == "ag_enforcement"
