"""End-to-end integration tests for the Law Card data layer (LC-1c):
law_card_assembler.py + edit_service.py against a real Postgres database.

Requires a database connection (REGS_DATABASE_URL / docker-compose), same
convention as test_pipeline_e2e.py — the `db` fixture rolls back after each
test, so nothing here needs manual cleanup.
"""
from __future__ import annotations

from datetime import date

import pytest

from src.core.edit_service import apply_edit, propose_edit, revert_edit, validate_edit
from src.core.law_card_assembler import assemble_card
from src.db.engine import SessionLocal
from src.db.models import (
    ConfidenceTier,
    DocumentFamily,
    DocumentVersion,
    Extraction,
    ExtractionType,
    FieldEditStatus,
    NormalizedSourceRecord,
    ReviewStatus,
    Source,
    TemporalStatus,
)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def seeded_law(db):
    """A realistic CO-SB205-shaped law: Source -> DocumentFamily (with
    canonical_key, the real lookup key) -> DocumentVersion -> two
    NormalizedSourceRecords -> two Extractions (one obligation with a
    verified evidence span and an enforcement sub-object, one definition)."""
    source = Source(
        jurisdiction_code="CO", jurisdiction_name="Colorado",
        source_type="state_statute", connector_id="colorado_ga",
    )
    db.add(source)
    db.flush()

    family = DocumentFamily(
        source_id=source.id,
        canonical_title="Colorado AI Act — SB 24-205",
        short_cite="SB 24-205",
        canonical_key="US-CO-SB24-205-TEST",
        subject_area="artificial_intelligence",
        primary_source_url="https://leg.colorado.gov/sb24-205",
    )
    db.add(family)
    db.flush()

    version = DocumentVersion(
        family_id=family.id,
        version_label="Enrolled",
        temporal_status=TemporalStatus.active,
        effective_date=date(2026, 6, 30),
    )
    db.add(version)
    db.flush()

    passage = NormalizedSourceRecord(
        document_version_id=version.id,
        section_path="Section 7 - Enforcement",
        ordinal=0,
        text_content=(
            "The attorney general has exclusive authority to enforce this article. "
            "A violation of this article constitutes a deceptive trade practice, "
            "subject to a civil penalty not to exceed $20,000 per violation. "
            "There is no private right of action under this article."
        ),
        text_hash="lc-e2e-hash-1",
    )
    db.add(passage)
    db.flush()

    obligation = Extraction(
        source_record_id=passage.id,
        extraction_type=ExtractionType.obligation,
        agent_name="obligation",
        payload={
            "subject": "The attorney general",
            "subject_normalized": "regulator",
            "modality": "shall",
            "action": "enforce this article",
            "object": "this article",
            "jurisdiction": "CO",
            "section_reference": "Section 7",
            "enforcement": {
                "enforcing_body": "attorney general",
                "penalty_type": "deceptive trade practice",
                "max_civil_penalty_usd": 20000,
                "private_right_of_action": False,
            },
        },
        evidence_spans=[
            {
                "field_name": "action", "text": "enforce this article",
                "verified": True, "match_tier": 1,
            },
            {
                "field_name": "enforcement.max_civil_penalty_usd",
                "text": "a civil penalty not to exceed $20,000 per violation",
                "verified": True, "match_tier": 1,
            },
        ],
        confidence_score=0.9,
        confidence_tier=ConfidenceTier.A,
        review_status=ReviewStatus.pending,
    )
    db.add(obligation)
    db.flush()

    return {"source": source, "family": family, "version": version,
            "passage": passage, "obligation": obligation}


class TestAssembleCard:
    def test_unknown_canonical_key_returns_not_found(self, db):
        result = assemble_card(db, "US-XX-NOPE")
        assert result.found is False
        assert result.card is None

    def test_known_law_assembles(self, db, seeded_law):
        result = assemble_card(db, "US-CO-SB24-205-TEST")
        assert result.found is True
        assert result.card["law"]["title"] == "Colorado AI Act — SB 24-205"
        assert result.card["law"]["jurisdiction"] == "CO"
        assert result.card["law"]["status"] == "active"

    def test_render_hint_is_full_when_extractions_exist(self, db, seeded_law):
        result = assemble_card(db, "US-CO-SB24-205-TEST")
        assert result.card["render_hint"] == "full"
        assert len(result.card["extractions"]) == 1

    def test_extraction_fields_carry_catalog_labels(self, db, seeded_law):
        result = assemble_card(db, "US-CO-SB24-205-TEST")
        ext = result.card["extractions"][0]
        fields_by_path = {f["path"]: f for f in ext["fields"]}
        assert fields_by_path["subject"]["label"] == "Who must comply"
        assert fields_by_path["subject"]["value"] == "The attorney general"
        assert fields_by_path["modality"]["widget"] == "select"

    def test_evidence_attached_to_matching_field(self, db, seeded_law):
        result = assemble_card(db, "US-CO-SB24-205-TEST")
        ext = result.card["extractions"][0]
        fields_by_path = {f["path"]: f for f in ext["fields"]}
        action_evidence = fields_by_path["action"]["evidence"]
        assert len(action_evidence) == 1
        assert action_evidence[0]["verified"] is True
        assert action_evidence[0]["match_tier"] == 1

    def test_gaps_include_no_preemption_when_none_extracted(self, db, seeded_law):
        result = assemble_card(db, "US-CO-SB24-205-TEST")
        assert "no_preemption_extractions" in result.card["gaps"]

    def test_stub_render_hint_when_zero_extractions(self, db):
        source = Source(
            jurisdiction_code="NY", jurisdiction_name="New York",
            source_type="state_statute", connector_id="ny_leg",
        )
        db.add(source)
        db.flush()
        family = DocumentFamily(
            source_id=source.id, canonical_title="Empty Test Law",
            canonical_key="US-NY-EMPTY-TEST",
        )
        db.add(family)
        db.flush()
        version = DocumentVersion(
            family_id=family.id, version_label="v1",
            temporal_status=TemporalStatus.active,
        )
        db.add(version)
        db.flush()

        result = assemble_card(db, "US-NY-EMPTY-TEST")
        assert result.found is True
        assert result.card["render_hint"] == "stub"
        assert "no_extractions" in result.card["gaps"]


class TestEditLifecycleAgainstRealDb:
    def test_propose_apply_updates_current_payload(self, db, seeded_law):
        obligation = seeded_law["obligation"]
        edit = propose_edit(
            db, obligation,
            canonical_key="US-CO-SB24-205-TEST",
            extraction_identity="obligation:obligation:testhash",
            field_path="enforcement.max_civil_penalty_usd",
            new_value="30000",
            reason="Corrected per amended penalty schedule",
            editor="test-analyst",
        )
        assert edit.status == FieldEditStatus.proposed

        result = apply_edit(db, edit.id, editor="test-analyst")
        assert result.success is True

        db.flush()
        db.refresh(obligation)
        assert obligation.current_payload["enforcement"]["max_civil_penalty_usd"] == 30000
        # Base payload must be untouched — the whole point of G-1's fix.
        assert obligation.payload["enforcement"]["max_civil_penalty_usd"] == 20000
        assert obligation.human_review_state == "edited"

    def test_original_recoverable_after_revert(self, db, seeded_law):
        obligation = seeded_law["obligation"]
        edit = propose_edit(
            db, obligation, canonical_key="US-CO-SB24-205-TEST",
            extraction_identity="obligation:obligation:testhash",
            field_path="subject", new_value="A regulated developer",
            reason="test revert", editor="test-analyst",
        )
        apply_edit(db, edit.id, editor="test-analyst")
        db.flush()
        db.refresh(obligation)
        assert obligation.current_payload["subject"] == "A regulated developer"

        revert_result = revert_edit(db, edit.id)
        assert revert_result.success is True
        db.flush()
        db.refresh(obligation)
        assert obligation.current_payload["subject"] == "The attorney general"
        assert obligation.human_review_state == "unedited"

    def test_second_edit_to_same_field_supersedes_first(self, db, seeded_law):
        obligation = seeded_law["obligation"]
        edit1 = propose_edit(
            db, obligation, canonical_key="US-CO-SB24-205-TEST",
            extraction_identity="x", field_path="subject",
            new_value="Draft correction", reason="r1", editor="a",
        )
        edit2 = propose_edit(
            db, obligation, canonical_key="US-CO-SB24-205-TEST",
            extraction_identity="x", field_path="subject",
            new_value="Final correction", reason="r2", editor="a",
        )
        db.flush()
        db.refresh(edit1)
        assert edit1.status == FieldEditStatus.superseded
        assert edit2.status == FieldEditStatus.proposed

    def test_apply_with_invalid_value_does_not_touch_payload(self, db, seeded_law):
        obligation = seeded_law["obligation"]
        edit = propose_edit(
            db, obligation, canonical_key="US-CO-SB24-205-TEST",
            extraction_identity="x",
            field_path="enforcement.max_civil_penalty_usd",
            new_value="not a number at all",
            reason="bad edit", editor="a",
        )
        result = apply_edit(db, edit.id, editor="a")
        assert result.success is False
        db.flush()
        db.refresh(obligation)
        assert obligation.effective_payload is None
        assert obligation.current_payload["enforcement"]["max_civil_penalty_usd"] == 20000

    def test_multiple_applied_edits_coexist_in_effective_payload(self, db, seeded_law):
        obligation = seeded_law["obligation"]
        e1 = propose_edit(
            db, obligation, canonical_key="US-CO-SB24-205-TEST", extraction_identity="x",
            field_path="subject", new_value="Corrected subject",
            reason="r1", editor="a",
        )
        apply_edit(db, e1.id, editor="a")
        e2 = propose_edit(
            db, obligation, canonical_key="US-CO-SB24-205-TEST", extraction_identity="x",
            field_path="jurisdiction", new_value="CO-corrected",
            reason="r2", editor="a",
        )
        apply_edit(db, e2.id, editor="a")

        db.flush()
        db.refresh(obligation)
        assert obligation.current_payload["subject"] == "Corrected subject"
        assert obligation.current_payload["jurisdiction"] == "CO-corrected"
        # Untouched fields survive the overlay unchanged
        assert obligation.current_payload["action"] == "enforce this article"

    def test_validate_edit_against_real_extraction_object(self, db, seeded_law):
        # Sanity check that validate_edit works against a real ORM-loaded
        # Extraction (not just the SimpleNamespace stand-in in the unit tests).
        obligation = seeded_law["obligation"]
        report = validate_edit(obligation, "enforcement.max_civil_penalty_usd", "999999")
        assert report.valid is True
        assert any("doesn't match" in w for w in report.warnings)
