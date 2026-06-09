"""Integration tests for Phase 5 concept grouping (requires Postgres).

Validates the full DB-backed grouping pass: extractions → compliance concepts
with links, tracker refs, grounding, and the review queue.  Uses the same
SessionLocal fixture pattern as test_pipeline_e2e.py — run under docker-compose.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.db.engine import SessionLocal
from src.db.models import (
    ComplianceConcept,
    ConceptExtractionLink,
    ConceptReviewStatus,
    ConfidenceTier,
    DocumentFamily,
    DocumentVersion,
    Extraction,
    ExtractionType,
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
def law_with_extractions(db):
    """Create a law with obligation + compliance_mechanism + rights extractions."""
    source = Source(
        jurisdiction_code="CO",
        jurisdiction_name="Colorado",
        source_type="state_statute",
        connector_id="colorado_ga",
    )
    db.add(source)
    db.flush()

    family = DocumentFamily(
        source_id=source.id,
        canonical_title="Colorado SB 205",
        short_cite="SB 205",
        subject_area="artificial_intelligence",
        metadata_={"key_requirements": "impact assessment, disclosure to consumers"},
    )
    db.add(family)
    db.flush()

    dv = DocumentVersion(
        family_id=family.id,
        version_label="Enrolled",
        temporal_status=TemporalStatus.active,
        effective_date=date(2026, 2, 1),
    )
    db.add(dv)
    db.flush()

    rec = NormalizedSourceRecord(
        document_version_id=dv.id,
        section_path="Section 3",
        ordinal=0,
        text_content="A deployer shall provide disclosure to the consumer.",
        text_hash="hash_concept_e2e_1",
    )
    db.add(rec)
    db.flush()

    # Two obligations with the same family + actor → one concept
    for i in range(2):
        db.add(Extraction(
            source_record_id=rec.id,
            extraction_type=ExtractionType.obligation,
            payload={
                "subject": "deployer",
                "subject_normalized": "deployer",
                "modality": "shall",
                "action": f"provide disclosure to the consumer (clause {i})",
            },
            evidence_spans=[],
            confidence_score=0.72,
            confidence_tier=ConfidenceTier.B,
            review_status=ReviewStatus.pending,
            payload_hash=f"ph_disc_{i}",
        ))

    # A rights extraction → separate concept
    db.add(Extraction(
        source_record_id=rec.id,
        extraction_type=ExtractionType.rights_protection,
        payload={
            "right_holder": "consumer",
            "right_holder_normalized": "individual",
            "right_type": "opt_out",
            "right_description": "right to opt out of profiling",
            "duty_bearer": "deployer",
        },
        evidence_spans=[],
        confidence_score=0.65,
        confidence_tier=ConfidenceTier.C,
        review_status=ReviewStatus.pending,
        payload_hash="ph_optout",
    ))

    # An enforcement extraction → law-wide ref on every concept
    db.add(Extraction(
        source_record_id=rec.id,
        extraction_type=ExtractionType.enforcement,
        payload={"penalty_type": "civil penalty", "enforcing_body": "attorney general"},
        evidence_spans=[],
        confidence_score=0.6,
        confidence_tier=ConfidenceTier.C,
        review_status=ReviewStatus.pending,
        payload_hash="ph_enf",
    ))

    db.flush()
    return dv


def test_grouping_creates_concepts(db, law_with_extractions):
    from src.core.concept_grouping import group_concepts_for_dv

    result = group_concepts_for_dv(db, law_with_extractions.id)
    db.flush()

    # 2 obligations (same family+actor) collapse to 1; rights is a 2nd concept.
    assert result.concepts_created == 2
    assert result.anchors_grouped == 3  # 2 obligations + 1 right

    concepts = db.scalars(
        select_concepts(law_with_extractions.id)
    ).all()
    types = {c.concept_type for c in concepts}
    assert "disclosure_to_user" in types
    assert "right_opt_out" in types


def test_obligation_concept_groups_two_anchors(db, law_with_extractions):
    from src.core.concept_grouping import group_concepts_for_dv

    group_concepts_for_dv(db, law_with_extractions.id)
    db.flush()

    disclosure = db.scalars(
        select_concepts(law_with_extractions.id).where(
            ComplianceConcept.concept_type == "disclosure_to_user"
        )
    ).first()
    assert disclosure is not None
    assert disclosure.member_count == 2
    assert disclosure.regulated_actor_family == "deployer"
    # Enforcement attaches law-wide
    assert len(disclosure.enforcement_refs) >= 1


def test_concept_links_created(db, law_with_extractions):
    from src.core.concept_grouping import group_concepts_for_dv

    group_concepts_for_dv(db, law_with_extractions.id)
    db.flush()

    disclosure = db.scalars(
        select_concepts(law_with_extractions.id).where(
            ComplianceConcept.concept_type == "disclosure_to_user"
        )
    ).first()
    links = db.scalars(
        select_links(disclosure.id)
    ).all()
    roles = {l.role for l in links}
    assert "anchor" in roles
    assert "enforcement" in roles


def test_grounding_tracker_grounded_with_orrick(db, law_with_extractions):
    from src.core.concept_grouping import group_concepts_for_dv

    group_concepts_for_dv(db, law_with_extractions.id)
    db.flush()

    concepts = db.scalars(select_concepts(law_with_extractions.id)).all()
    # Family metadata has key_requirements → Orrick present → tracker_grounded
    assert all(c.grounding_status == "tracker_grounded" for c in concepts)
    assert all("orrick:CO/SB 205" in (c.tracker_ref_ids or []) for c in concepts)


def test_idempotent_regrouping(db, law_with_extractions):
    from src.core.concept_grouping import group_concepts_for_dv

    group_concepts_for_dv(db, law_with_extractions.id)
    db.flush()
    first = db.scalars(select_concepts(law_with_extractions.id)).all()
    first_count = len(first)

    group_concepts_for_dv(db, law_with_extractions.id)
    db.flush()
    second = db.scalars(select_concepts(law_with_extractions.id)).all()

    assert len(second) == first_count  # no duplicates on re-run


def test_review_queue_surfaces_ungrounded(db):
    """A law with no tracker data yields ungrounded concepts in the queue."""
    from src.core.concept_grouping import group_concepts_for_dv
    from src.core.concept_review import get_concept_review_queue

    source = Source(
        jurisdiction_code="ZZ",
        jurisdiction_name="Nowhere",
        source_type="state_statute",
        connector_id="zz",
    )
    db.add(source)
    db.flush()
    family = DocumentFamily(
        source_id=source.id,
        canonical_title="Nowhere AI Act",
        short_cite="ZZ 1",
        subject_area="artificial_intelligence",
        metadata_={},  # no Orrick data
    )
    db.add(family)
    db.flush()
    dv = DocumentVersion(
        family_id=family.id, version_label="v1",
        temporal_status=TemporalStatus.active,
    )
    db.add(dv)
    db.flush()
    rec = NormalizedSourceRecord(
        document_version_id=dv.id, section_path="S1", ordinal=0,
        text_content="An operator shall maintain record_keeping logs.",
        text_hash="hash_ungrounded_1",
    )
    db.add(rec)
    db.flush()
    db.add(Extraction(
        source_record_id=rec.id,
        extraction_type=ExtractionType.obligation,
        payload={
            "subject": "operator", "subject_normalized": "operator",
            "modality": "shall", "action": "maintain record_keeping logs",
        },
        evidence_spans=[], confidence_score=0.55,
        confidence_tier=ConfidenceTier.C, review_status=ReviewStatus.pending,
        payload_hash="ph_ungrounded",
    ))
    db.flush()

    group_concepts_for_dv(db, dv.id)
    db.flush()

    queue = get_concept_review_queue(db, jurisdiction="ZZ")
    assert len(queue) >= 1
    assert all(i.grounding_status == "ungrounded" for i in queue)


# --- small query helpers (avoid importing select at module top twice) ---

def select_concepts(dv_id):
    from sqlalchemy import select
    return select(ComplianceConcept).where(
        ComplianceConcept.document_version_id == dv_id
    )


def select_links(concept_id):
    from sqlalchemy import select
    return select(ConceptExtractionLink).where(
        ConceptExtractionLink.concept_id == concept_id
    )
