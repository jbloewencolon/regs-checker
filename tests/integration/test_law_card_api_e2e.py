"""End-to-end integration tests for the Law Card JSON API (LC-1d) — real
Postgres + a real FastAPI TestClient hitting src/api/routes/law_card_api.py,
following this repo's test_pipeline_e2e.py convention.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.law_card_api import router
from src.db.engine import SessionLocal, get_db
from src.db.models import (
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
def client(db):
    app = FastAPI()
    app.include_router(router)

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def seeded_law(db):
    # Unique per invocation: the routes under test call db.commit()
    # internally (correct production behavior — matches review_routes.py),
    # so committed rows from one test run persist in the DB; a fixed key
    # would collide with a prior run's row on the unique canonical_key index.
    unique_key = f"US-CO-SB24-205-APITEST-{uuid.uuid4().hex[:8]}"

    source = Source(
        jurisdiction_code="CO", jurisdiction_name="Colorado",
        source_type="state_statute", connector_id="colorado_ga",
    )
    db.add(source)
    db.flush()

    family = DocumentFamily(
        source_id=source.id, canonical_title="Colorado AI Act — SB 24-205 (API test)",
        short_cite="SB 24-205", canonical_key=unique_key,
    )
    db.add(family)
    db.flush()

    version = DocumentVersion(
        family_id=family.id, version_label="Enrolled",
        temporal_status=TemporalStatus.active, effective_date=date(2026, 6, 30),
    )
    db.add(version)
    db.flush()

    passage = NormalizedSourceRecord(
        document_version_id=version.id, section_path="Section 7", ordinal=0,
        text_content="A civil penalty not to exceed $20,000 per violation applies.",
        text_hash="api-e2e-hash",
    )
    db.add(passage)
    db.flush()

    obligation = Extraction(
        source_record_id=passage.id,
        extraction_type=ExtractionType.obligation,
        agent_name="obligation",
        payload={
            "subject": "The attorney general",
            "modality": "shall",
            "action": "enforce this article",
            "enforcement": {"max_civil_penalty_usd": 20000},
        },
        evidence_spans=[],
        confidence_score=0.9,
        confidence_tier=ConfidenceTier.A,
        review_status=ReviewStatus.pending,
    )
    db.add(obligation)
    db.flush()

    db.commit()  # TestClient runs in a separate "request", needs committed data visible
    return {"family": family, "obligation": obligation, "canonical_key": unique_key}


class TestListLaws:
    def test_returns_seeded_law(self, client, seeded_law):
        resp = client.get("/api/laws", params={"q": "SB 24-205 (API test)", "per_page": 100})
        assert resp.status_code == 200
        keys = [item["canonical_key"] for item in resp.json()["items"]]
        assert seeded_law["canonical_key"] in keys


class TestGetCard:
    def test_known_law_returns_card(self, client, seeded_law):
        resp = client.get(f"/api/laws/{seeded_law['canonical_key']}/card")
        assert resp.status_code == 200
        card = resp.json()
        assert card["law"]["canonical_key"] == seeded_law["canonical_key"]
        assert len(card["extractions"]) == 1

    def test_unknown_law_returns_404(self, client):
        resp = client.get("/api/laws/US-XX-NOPE/card")
        assert resp.status_code == 404


class TestValidateEndpoint:
    def test_valid_edit_check_returns_no_errors(self, client, seeded_law):
        obligation = seeded_law["obligation"]
        resp = client.post(
            f"/api/laws/{seeded_law['canonical_key']}/extractions/{obligation.id}/validate",
            json={"field_path": "enforcement.max_civil_penalty_usd", "new_value": "25000"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["normalized_value"] == 25000

    def test_invalid_number_returns_errors_but_200(self, client, seeded_law):
        # "Check" never mutates state and never 4xxs on a bad *value* — it
        # reports the problem so the UI can show it inline.
        obligation = seeded_law["obligation"]
        resp = client.post(
            f"/api/laws/{seeded_law['canonical_key']}/extractions/{obligation.id}/validate",
            json={"field_path": "enforcement.max_civil_penalty_usd", "new_value": "a lot"},
        )
        assert resp.status_code == 200
        assert resp.json()["valid"] is False

    def test_bad_field_path_returns_400(self, client, seeded_law):
        obligation = seeded_law["obligation"]
        resp = client.post(
            f"/api/laws/{seeded_law['canonical_key']}/extractions/{obligation.id}/validate",
            json={"field_path": "not_a_real_field", "new_value": "x"},
        )
        assert resp.status_code == 400

    def test_extraction_from_other_law_returns_400(self, client, seeded_law, db):
        # A different law's extraction id passed against this law's key.
        other_source = Source(
            jurisdiction_code="NY", jurisdiction_name="New York",
            source_type="state_statute", connector_id="ny",
        )
        db.add(other_source)
        db.flush()
        other_family = DocumentFamily(
            source_id=other_source.id, canonical_title="Other Law",
            canonical_key=f"US-NY-OTHER-APITEST-{uuid.uuid4().hex[:8]}",
        )
        db.add(other_family)
        db.flush()
        other_version = DocumentVersion(
            family_id=other_family.id, version_label="v1", temporal_status=TemporalStatus.active,
        )
        db.add(other_version)
        db.flush()
        other_passage = NormalizedSourceRecord(
            document_version_id=other_version.id, section_path="s", ordinal=0,
            text_content="t", text_hash="other-hash",
        )
        db.add(other_passage)
        db.flush()
        other_extraction = Extraction(
            source_record_id=other_passage.id, extraction_type=ExtractionType.obligation,
            payload={"subject": "x"}, evidence_spans=[],
            confidence_score=0.5, confidence_tier=ConfidenceTier.C,
            review_status=ReviewStatus.pending,
        )
        db.add(other_extraction)
        db.flush()
        db.commit()

        resp = client.post(
            f"/api/laws/{seeded_law['canonical_key']}/extractions/{other_extraction.id}/validate",
            json={"field_path": "subject", "new_value": "y"},
        )
        assert resp.status_code == 400


class TestSaveEditEndpoint:
    def test_save_applies_and_returns_edit_id(self, client, seeded_law, db):
        obligation = seeded_law["obligation"]
        resp = client.post(
            f"/api/laws/{seeded_law['canonical_key']}/extractions/{obligation.id}/edits",
            json={
                "field_path": "enforcement.max_civil_penalty_usd",
                "new_value": "30000",
                "reason": "Corrected per amendment",
                "editor": "test-analyst",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "applied"
        assert body["new_value"] == 30000

        db.refresh(obligation)
        assert obligation.current_payload["enforcement"]["max_civil_penalty_usd"] == 30000
        assert obligation.payload["enforcement"]["max_civil_penalty_usd"] == 20000  # base untouched

    def test_save_missing_reason_returns_422(self, client, seeded_law):
        obligation = seeded_law["obligation"]
        resp = client.post(
            f"/api/laws/{seeded_law['canonical_key']}/extractions/{obligation.id}/edits",
            json={"field_path": "subject", "new_value": "x", "reason": "", "editor": "a"},
        )
        assert resp.status_code == 422  # Pydantic min_length=1 on reason

    def test_save_invalid_value_returns_422_and_leaves_payload_untouched(
        self, client, seeded_law, db,
    ):
        obligation = seeded_law["obligation"]
        resp = client.post(
            f"/api/laws/{seeded_law['canonical_key']}/extractions/{obligation.id}/edits",
            json={
                "field_path": "enforcement.max_civil_penalty_usd",
                "new_value": "not a number",
                "reason": "bad edit attempt",
                "editor": "a",
            },
        )
        assert resp.status_code == 422
        db.refresh(obligation)
        assert obligation.effective_payload is None


class TestRevertEndpoint:
    def test_revert_restores_original_value(self, client, seeded_law, db):
        obligation = seeded_law["obligation"]
        save_resp = client.post(
            f"/api/laws/{seeded_law['canonical_key']}/extractions/{obligation.id}/edits",
            json={
                "field_path": "subject", "new_value": "Corrected subject",
                "reason": "r", "editor": "a",
            },
        )
        edit_id = save_resp.json()["edit_id"]

        revert_resp = client.post(f"/api/edits/{edit_id}/revert")
        assert revert_resp.status_code == 200
        assert revert_resp.json()["reverted"] is True

        db.refresh(obligation)
        assert obligation.current_payload["subject"] == "The attorney general"

    def test_revert_unknown_edit_returns_400(self, client):
        resp = client.post("/api/edits/999999999/revert")
        assert resp.status_code == 400
