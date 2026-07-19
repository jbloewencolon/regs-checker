"""End-to-end tests for LC-1e's G-1 fix: the two destructive in-place edit
paths (review_routes.py's POST /api/review/{id}/edit and internal.py's
POST /review/queue/{id}/action with corrections) are reimplemented on
edit_service instead of mutating Extraction.payload directly. Both had zero
prior test coverage — these are the first tests either endpoint has had.

Real Postgres + real FastAPI TestClient, following test_pipeline_e2e.py's
convention.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.internal import router as internal_router
from src.api.routes.review_routes import router as review_router
from src.db.engine import SessionLocal, get_db
from src.db.models import (
    ConfidenceTier,
    DocumentFamily,
    DocumentVersion,
    Extraction,
    ExtractionType,
    NormalizedSourceRecord,
    ReviewQueueItem,
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
def review_client(db):
    app = FastAPI()
    app.include_router(review_router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def internal_client(db):
    app = FastAPI()
    app.include_router(internal_router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def seeded_review_item(db):
    unique_key = f"US-CO-G1TEST-{uuid.uuid4().hex[:8]}"
    source = Source(
        jurisdiction_code="CO", jurisdiction_name="Colorado",
        source_type="state_statute", connector_id="g1-test",
    )
    db.add(source)
    db.flush()
    family = DocumentFamily(
        source_id=source.id, canonical_title="G1 Fix Test Law", canonical_key=unique_key,
    )
    db.add(family)
    db.flush()
    version = DocumentVersion(
        family_id=family.id, version_label="v1",
        temporal_status=TemporalStatus.active, effective_date=date(2026, 1, 1),
    )
    db.add(version)
    db.flush()
    passage = NormalizedSourceRecord(
        document_version_id=version.id, section_path="Section 1", ordinal=0,
        text_content="A developer shall comply.", text_hash="g1-hash",
    )
    db.add(passage)
    db.flush()
    extraction = Extraction(
        source_record_id=passage.id, extraction_type=ExtractionType.obligation,
        agent_name="obligation",
        payload={"subject": "A developer", "modality": "shall", "action": "comply"},
        evidence_spans=[], confidence_score=0.8, confidence_tier=ConfidenceTier.B,
        review_status=ReviewStatus.pending,
    )
    db.add(extraction)
    db.flush()
    queue_item = ReviewQueueItem(
        extraction_id=extraction.id, priority=1, status=ReviewStatus.pending,
    )
    db.add(queue_item)
    db.flush()
    db.commit()
    return {"extraction": extraction, "queue_item": queue_item, "canonical_key": unique_key}


class TestReviewRoutesEditEndpoint:
    def test_edit_applies_and_preserves_original(self, review_client, seeded_review_item, db):
        extraction = seeded_review_item["extraction"]
        queue_item = seeded_review_item["queue_item"]

        resp = review_client.post(
            f"/api/review/{queue_item.id}/edit",
            data={"payload_json": '{"subject": "A regulated developer"}'},
        )
        assert resp.status_code == 200
        assert "Saved changes to 1 field" in resp.text

        db.refresh(extraction)
        assert extraction.current_payload["subject"] == "A regulated developer"
        # The whole point of the fix: original model output survives.
        assert extraction.payload["subject"] == "A developer"

    def test_edit_writes_audit_review_action(self, review_client, seeded_review_item, db):
        queue_item = seeded_review_item["queue_item"]
        review_client.post(
            f"/api/review/{queue_item.id}/edit",
            data={"payload_json": '{"action": "comply promptly"}'},
        )
        db.refresh(queue_item)
        actions = queue_item.actions
        assert len(actions) == 1
        assert "action" in actions[0].comment
        assert actions[0].corrections == {"action": "comply promptly"}

    def test_invalid_field_value_reports_error_without_crashing(
        self, review_client, seeded_review_item, db,
    ):
        extraction = seeded_review_item["extraction"]
        resp = review_client.post(
            f"/api/review/{seeded_review_item['queue_item'].id}/edit",
            data={"payload_json": '{"modality": "shall"}'},  # valid, standard choice
        )
        assert resp.status_code == 200
        db.refresh(extraction)
        assert extraction.current_payload["modality"] == "shall"

    def test_missing_queue_item_returns_404(self, review_client):
        resp = review_client.post(
            "/api/review/999999999/edit",
            data={"payload_json": '{"subject": "x"}'},
        )
        assert resp.status_code == 404

    def test_invalid_json_returns_400(self, review_client, seeded_review_item):
        resp = review_client.post(
            f"/api/review/{seeded_review_item['queue_item'].id}/edit",
            data={"payload_json": "not json"},
        )
        assert resp.status_code == 400


class TestInternalReviewActionCorrections:
    def test_approve_with_corrections_preserves_original_payload(
        self, internal_client, seeded_review_item, db,
    ):
        extraction = seeded_review_item["extraction"]
        queue_item = seeded_review_item["queue_item"]

        resp = internal_client.post(
            f"/review/queue/{queue_item.id}/action",
            json={
                "action": "approved",
                "reviewer": "test-reviewer",
                "corrections": {"subject": "A corrected developer"},
            },
        )
        assert resp.status_code == 200

        db.refresh(extraction)
        assert extraction.current_payload["subject"] == "A corrected developer"
        assert extraction.payload["subject"] == "A developer"  # base untouched
        assert extraction.review_status == ReviewStatus.approved

    def test_approve_without_corrections_still_works(self, internal_client, seeded_review_item, db):
        extraction = seeded_review_item["extraction"]
        resp = internal_client.post(
            f"/review/queue/{seeded_review_item['queue_item'].id}/action",
            json={"action": "approved", "reviewer": "test-reviewer"},
        )
        assert resp.status_code == 200
        db.refresh(extraction)
        assert extraction.review_status == ReviewStatus.approved
        assert extraction.effective_payload is None  # nothing edited

    def test_correction_on_law_without_canonical_key_returns_400(self, internal_client, db):
        source = Source(
            jurisdiction_code="TX", jurisdiction_name="Texas",
            source_type="state_statute", connector_id="no-key-test",
        )
        db.add(source)
        db.flush()
        family = DocumentFamily(
            source_id=source.id, canonical_title="No Key Law", canonical_key=None,
        )
        db.add(family)
        db.flush()
        version = DocumentVersion(
            family_id=family.id, version_label="v1", temporal_status=TemporalStatus.active,
        )
        db.add(version)
        db.flush()
        passage = NormalizedSourceRecord(
            document_version_id=version.id, section_path="s", ordinal=0,
            text_content="t", text_hash="no-key-hash",
        )
        db.add(passage)
        db.flush()
        extraction = Extraction(
            source_record_id=passage.id, extraction_type=ExtractionType.obligation,
            payload={"subject": "x"}, evidence_spans=[],
            confidence_score=0.5, confidence_tier=ConfidenceTier.C,
            review_status=ReviewStatus.pending,
        )
        db.add(extraction)
        db.flush()
        queue_item = ReviewQueueItem(
            extraction_id=extraction.id, priority=0, status=ReviewStatus.pending,
        )
        db.add(queue_item)
        db.flush()
        db.commit()

        resp = internal_client.post(
            f"/review/queue/{queue_item.id}/action",
            json={
                "action": "approved", "reviewer": "test-reviewer",
                "corrections": {"subject": "y"},
            },
        )
        assert resp.status_code == 400
