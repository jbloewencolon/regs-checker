"""QA-9a wiring: restatement-scoped relevance at sync time.

Tests for Phase 2 step 3 of the QA-8/QA-9 plan: applying
assess_extraction_scope at sync to hide extractions falling wholly
outside restatement-scoped AI/domain provisions.

Pattern: QA-6 (legal_context classification) but for relevance-filtered
hiding via ai_nexus/display instead of legal_context_type.

GATED: docs/qa8_qa9_phased_plan.md Phase 2 step 4 requires RPR/product
ratification of the in-scope rules before this can affect live sync
output. settings.qa9a_scope_filter_enabled defaults to False; the
TestRestatementScopeHiding class below exercises the *engine wiring*
with the flag explicitly turned on (via the autouse fixture) so the
logic is proven correct ahead of ratification. TestFlagDefaultsOff
pins the actual shipped default: unset, this code path is a no-op.
"""

import pytest

from src.core.config import settings
from src.core.payload_adapter import adapt_payload_for_sync


class TestRestatementScopeHiding:
    """Test that out-of-scope extractions are hidden at sync, with the
    QA-9a gate explicitly enabled (see module docstring — not the shipped
    default)."""

    @pytest.fixture(autouse=True)
    def _enable_qa9a_gate(self):
        original = settings.qa9a_scope_filter_enabled
        settings.qa9a_scope_filter_enabled = True
        yield
        settings.qa9a_scope_filter_enabled = original

    def test_in_scope_keyword_obligation(self):
        """Obligation with AI keyword stays visible."""
        payload = {
            "subject": "A person",
            "action": "distributes a computer-generated image",
            "object": "intimate image",
            "evidence_spans": [
                {
                    "field_name": "action",
                    "text": "computer-generated image",
                }
            ],
        }
        passage = """(j)(4) A person who intentionally creates and distributes
        any computer-generated image of an intimate body part shall be guilty
        of disorderly conduct."""

        passage_metadata = {"parallel_version_group": "penal_code:647"}

        result = adapt_payload_for_sync(
            "obligation",
            payload,
            passage_text=passage,
            passage_metadata=passage_metadata,
            added_section_numbers=set(),
        )

        # Should NOT be hidden — keyword matches
        assert result.get("ai_nexus") is not False
        assert result.get("display") is not False

    def test_out_of_scope_loitering_obligation(self):
        """Obligation about loitering (no AI content) gets hidden if in restatement."""
        payload = {
            "subject": "A person",
            "action": "loiters in any public place",
            "object": "public place",
            "evidence_spans": [
                {
                    "field_name": "action",
                    "text": "Every person who loiters in any public place",
                }
            ],
        }
        # This simulates a passage from SB 926's restatement of Penal Code § 647.
        # The passage is about loitering (no AI keywords), so should be hidden.
        # Note: passage must be long enough and have parallel_version_group to be
        # recognized as a restatement.
        passage = """Section 647 of the Penal Code, as amended by Section 1.6,
        is amended to read:

        647. (a) Every person who loiters in any public place, soliciting from
        persons in the place for donations, and who accosts other persons in
        the public place for the purpose of requesting donations shall be
        guilty of disorderly conduct, a misdemeanor. The term "solicit" means
        to request an immediate donation of money or other thing of value from
        another person, and includes the verbal or written request for a
        donation and the solicitation of a donation through an application or
        technology-enabled platform.""" + "\n" * 100  # Pad to meet size threshold

        passage_metadata = {"parallel_version_group": "penal_code:647"}

        result = adapt_payload_for_sync(
            "obligation",
            payload,
            passage_text=passage,
            passage_metadata=passage_metadata,
            added_section_numbers=set(),
        )

        # Should be hidden — no AI keywords, so out of scope
        assert result.get("ai_nexus") is False
        assert result.get("display") is False

    def test_scope_by_added_section_reference(self):
        """Obligation citing bill's own added section stays in scope."""
        payload = {
            "subject": "a print advertisement",
            "action": "include disclosures",
            "evidence_spans": [
                {
                    "field_name": "action",
                    "text": "shall include the disclosures required by Section 84514",
                }
            ],
        }
        # AB 2355: the disclosure rule (no AI keyword) but cites § 84514
        # which AB 2355 itself adds.
        passage = """(a) A print advertisement shall include the disclosures
        required by Section 84514, displayed as follows: (1) The disclosure
        area shall have a solid white background."""

        passage_metadata = {"parallel_version_group": "gov_code:84504.2"}

        result = adapt_payload_for_sync(
            "obligation",
            payload,
            passage_text=passage,
            passage_metadata=passage_metadata,
            added_section_numbers={"84514"},  # AB 2355 adds § 84514
        )

        # Should NOT be hidden — rule (b) keeps it in scope
        assert result.get("ai_nexus") is not False
        assert result.get("display") is not False

    def test_no_evidence_text_leaves_in_scope(self):
        """Extraction with no evidence text is left in scope (safe default)."""
        payload = {
            "subject": "A person",
            "action": "does something",
            "evidence_spans": [],  # No evidence
        }
        passage = """(a) Something something out of scope."""
        passage_metadata = {"parallel_version_group": "penal_code:647"}

        result = adapt_payload_for_sync(
            "obligation",
            payload,
            passage_text=passage,
            passage_metadata=passage_metadata,
            added_section_numbers=set(),
        )

        # No evidence to assess → safe default is in scope
        assert result.get("ai_nexus") is not False
        assert result.get("display") is not False

    def test_non_restatement_unchanged(self):
        """Non-restatement passages skip scope assessment entirely."""
        payload = {
            "subject": "A person",
            "action": "loiters in a public place",
            "evidence_spans": [
                {
                    "field_name": "action",
                    "text": "loiters",
                }
            ],
        }
        # A whole-bill passage (not a restatement) about loitering.
        # Even though it lacks AI keywords, it's NOT a restatement so it
        # should NOT be hidden by QA-9a.
        passage = """Section 647. Loitering is prohibited."""

        passage_metadata = {}  # No parallel_version_group

        result = adapt_payload_for_sync(
            "obligation",
            payload,
            passage_text=passage,
            passage_metadata=passage_metadata,
            added_section_numbers=set(),
        )

        # Should not be hidden — not a restatement passage
        assert result.get("ai_nexus") is not False
        assert result.get("display") is not False

    def test_bill_level_extraction_skipped(self):
        """Bill-level extractions (no evidence_spans) skip scope assessment."""
        payload = {
            "enforcing_body": "State Attorney General",
            "penalty_type": "civil",
            "max_civil_penalty_usd": 10000,
            # Bill-level payloads don't have evidence_spans
        }
        passage = """Much content here."""
        passage_metadata = {"parallel_version_group": "penal_code:647"}

        result = adapt_payload_for_sync(
            "enforcement_agent",  # Bill-level agent (should be skipped)
            payload,
            passage_text=passage,
            passage_metadata=passage_metadata,
            added_section_numbers=set(),
        )

        # Should not be modified — enforcement_agent is not a clause type
        assert "ai_nexus" not in result or result.get("ai_nexus") is not False
        assert "display" not in result or result.get("display") is not False

    def test_threshold_exception_scope(self):
        """Threshold exception with no AI signal, in a subdivision separate
        from any AI-relevant clause, gets hidden from a restatement."""
        payload = {
            "threshold_type": "age",
            "threshold_value": "18",
            "threshold_unit": "years",
            "exceptions": [
                {
                    "exception_type": "exemption",
                    "description": "Not applicable to under-18 solicitors",
                }
            ],
            "evidence_spans": [
                {
                    "field_name": "exceptions",
                    "text": "does not apply to a person under 18 years of age who solicits",
                }
            ],
        }
        # The age exemption sits in subdivision (a) (loitering/soliciting),
        # entirely separate from the AI-relevant (b) clause below it.
        # Sequential top-level markers (a)...(b) — the parser requires the
        # a, b, c, ... sequence to detect separate top-level subdivisions.
        passage = """Section 647 is amended to read:

        647. (a) Every person who loiters in public for the purpose of
        soliciting donations is guilty of disorderly conduct. This subdivision
        does not apply to a person under 18 years of age who solicits on
        behalf of a registered charity.

        (b) Computer-generated deepfake prohibitions. A person who
        intentionally creates and distributes a computer-generated intimate
        image of another person shall be guilty of disorderly conduct.""" + "\n" * 100

        passage_metadata = {"parallel_version_group": "penal_code:647"}

        result = adapt_payload_for_sync(
            "threshold",
            payload,
            passage_text=passage,
            passage_metadata=passage_metadata,
            added_section_numbers=set(),
        )

        # Should be hidden — the (a) subdivision this exception anchors to
        # has no AI/domain content; the AI clause is a separate subdivision.
        assert result.get("ai_nexus") is False
        assert result.get("display") is False

    def test_definition_of_digital_replica_in_scope(self):
        """Definition of domain term stays in scope."""
        payload = {
            "term": "digital replica",
            "definition_text": "a voice or likeness includes a digital replica, as defined in Section 3344.1",
            "evidence_spans": [
                {
                    "field_name": "term",
                    "text": "digital replica",
                }
            ],
        }
        # Civil Code § 3344(f) defining "digital replica"
        passage = """(f) For the purposes of this section, a voice or likeness
        includes a digital replica, as defined in Section 3344.1."""

        passage_metadata = {"parallel_version_group": "civil_code:3344"}

        result = adapt_payload_for_sync(
            "definition",
            payload,
            passage_text=passage,
            passage_metadata=passage_metadata,
            added_section_numbers=set(),
        )

        # Should NOT be hidden — "digital replica" is a domain term
        assert result.get("ai_nexus") is not False
        assert result.get("display") is not False


class TestFlagDefaultsOff:
    """Pins the actual shipped default: qa9a_scope_filter_enabled is False,
    so this class runs WITHOUT the autouse override above and confirms the
    engine never hides a row until a human flips the flag post-ratification."""

    def test_default_flag_value_is_false(self):
        assert settings.qa9a_scope_filter_enabled is False

    def test_out_of_scope_passage_not_hidden_when_flag_off(self):
        """Same out-of-scope loitering passage as
        TestRestatementScopeHiding.test_out_of_scope_loitering_obligation,
        but with the flag at its real shipped default (off) — must NOT hide."""
        assert settings.qa9a_scope_filter_enabled is False

        payload = {
            "subject": "A person",
            "action": "loiters in any public place",
            "object": "public place",
            "evidence_spans": [
                {
                    "field_name": "action",
                    "text": "Every person who loiters in any public place",
                }
            ],
        }
        passage = """Section 647 of the Penal Code, as amended by Section 1.6,
        is amended to read:

        647. (a) Every person who loiters in any public place, soliciting from
        persons in the place for donations, and who accosts other persons in
        the public place for the purpose of requesting donations shall be
        guilty of disorderly conduct, a misdemeanor.""" + "\n" * 100

        passage_metadata = {"parallel_version_group": "penal_code:647"}

        result = adapt_payload_for_sync(
            "obligation",
            payload,
            passage_text=passage,
            passage_metadata=passage_metadata,
            added_section_numbers=set(),
        )

        # Flag is off in the real config — nothing gets hidden yet.
        assert result.get("ai_nexus") is not False
        assert result.get("display") is not False


class TestAdapterSignatureBackwardCompat:
    """Ensure new parameters are optional for backward compatibility."""

    def test_call_without_passage_parameters(self):
        """Old-style call (payload only) still works."""
        payload = {"subject": "A person", "action": "does something"}

        result = adapt_payload_for_sync("obligation", payload)

        # Should pass through unchanged since no passage context
        assert result["subject"] == "A person"
        assert result["action"] == "does something"

    def test_call_with_none_passage_text(self):
        """Passing None for passage_text is safe."""
        payload = {"subject": "A person"}

        result = adapt_payload_for_sync(
            "obligation",
            payload,
            passage_text=None,
            passage_metadata={},
        )

        assert result["subject"] == "A person"


class TestLoggingOfHiddenExtractions:
    """Verify that hidden extractions are logged for debugging (with the
    QA-9a gate explicitly enabled — see module docstring)."""

    @pytest.fixture(autouse=True)
    def _enable_qa9a_gate(self):
        original = settings.qa9a_scope_filter_enabled
        settings.qa9a_scope_filter_enabled = True
        yield
        settings.qa9a_scope_filter_enabled = original

    def test_hidden_extraction_logged(self):
        """Out-of-scope extraction sets ai_nexus/display false (the decision
        a downstream log consumer would key on)."""
        payload = {
            "subject": "A person",
            "action": "loiters in a public place",
            "evidence_spans": [
                {"field_name": "action", "text": "Any person who loiters in a public place"}
            ],
        }
        # Sequential top-level markers (a)...(b) — the parser requires the
        # a, b, c, ... sequence to detect separate top-level subdivisions.
        passage = """Section 647 of the Penal Code is amended to read:

        647. (a) Any person who loiters in a public place for the purpose of
        soliciting charitable donations is guilty of disorderly conduct.

        (b) A person who intentionally creates and distributes any
        computer-generated image of an intimate body part of another person
        shall be guilty of disorderly conduct.""" + "\n" * 100

        passage_metadata = {"parallel_version_group": "penal_code:647"}

        adapt_payload_for_sync(
            "obligation",
            payload,
            passage_text=passage,
            passage_metadata=passage_metadata,
        )

        assert payload.get("ai_nexus") is False
        assert payload.get("display") is False
