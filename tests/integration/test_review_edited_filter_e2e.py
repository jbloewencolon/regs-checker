"""LC-3b: the review queue's "Edited" filter (review_routes.py's
`edited_only` query param) — an extraction with an applied
ExtractionFieldEdit should show up under the Edited tab and count, and
disappear from it once nothing is edited. Real Postgres + real TestClient.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.core.edit_service import apply_edit, extraction_identity_string, propose_edit
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
def client(db):
    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def pending_extraction_with_queue_item(db):
    unique_key = f"US-CO-EDITFILTER-{uuid.uuid4().hex[:8]}"
    source = Source(
        jurisdiction_code="CO", jurisdiction_name="Colorado",
        source_type="state_statute", connector_id=f"edit-filter-test-{uuid.uuid4().hex[:6]}",
    )
    db.add(source)
    db.flush()
    family = DocumentFamily(
        source_id=source.id, canonical_title="Edit Filter Test Law", canonical_key=unique_key,
    )
    db.add(family)
    db.flush()
    version = DocumentVersion(
        family_id=family.id, version_label="v1", temporal_status=TemporalStatus.active,
    )
    db.add(version)
    db.flush()
    passage = NormalizedSourceRecord(
        document_version_id=version.id, section_path="Section 1", ordinal=0,
        text_content="A developer shall comply.", text_hash=f"h-{uuid.uuid4().hex[:8]}",
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
    # High priority guarantees this row sorts onto page 1 regardless of how
    # many other pending/edited rows this session's scratch DB has
    # accumulated from earlier test runs (review_page orders by priority
    # desc, and hardcodes a 25-row page — same pagination-visibility class
    # of issue as the law-list pollution noted elsewhere in this session).
    queue_item = ReviewQueueItem(
        extraction_id=extraction.id, priority=999999, status=ReviewStatus.pending,
    )
    db.add(queue_item)
    db.flush()
    db.commit()
    return unique_key, extraction.id, queue_item.id


class TestEditedFilter:
    def test_unedited_extraction_not_in_edited_filter(
        self, client, pending_extraction_with_queue_item,
    ):
        _key, _extraction_id, queue_item_id = pending_extraction_with_queue_item
        resp = client.get("/dashboard/review", params={"status": "pending", "edited_only": "true"})
        assert resp.status_code == 200
        assert f'id="review-row-{queue_item_id}"' not in resp.text

    def test_edited_extraction_appears_in_edited_filter_and_count(
        self, client, pending_extraction_with_queue_item, db,
    ):
        canonical_key, extraction_id, queue_item_id = pending_extraction_with_queue_item
        extraction = db.get(Extraction, extraction_id)
        edit = propose_edit(
            db, extraction, canonical_key=canonical_key,
            extraction_identity=extraction_identity_string(extraction),
            field_path="subject", new_value="A regulated developer",
            reason="test", editor="tester",
        )
        result = apply_edit(db, edit.id, editor="tester")
        assert result.success
        db.commit()

        resp = client.get("/dashboard/review", params={"status": "pending"})
        assert resp.status_code == 200
        assert "edited_only=true" in resp.text  # the tab itself renders once edited_count > 0

        resp = client.get("/dashboard/review", params={"status": "pending", "edited_only": "true"})
        assert resp.status_code == 200
        assert f'id="review-row-{queue_item_id}"' in resp.text
        assert "EDITED" in resp.text
