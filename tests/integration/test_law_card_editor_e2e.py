"""End-to-end tests for the LC-3a field editor: the HTMX fragment routes in
law_card_routes.py (edit/view/check/save/revert) that let an analyst correct
an extracted field from the law card page. Exercises the exact same paths
htmx would call client-side, using a real Postgres + real TestClient against
the actual app instance (matches test_law_card_pages_e2e.py's convention).
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.core.config import settings
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
    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    original = settings.law_cards_enabled
    settings.law_cards_enabled = True
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        settings.law_cards_enabled = original
        app.dependency_overrides.pop(get_db, None)


def _make_family(db, *, jurisdiction, title_suffix):
    unique_key = f"US-{jurisdiction}-LCEDIT-{uuid.uuid4().hex[:8]}"
    source = Source(
        jurisdiction_code=jurisdiction, jurisdiction_name=jurisdiction,
        source_type="state_statute", connector_id=f"lc-editor-test-{uuid.uuid4().hex[:6]}",
    )
    db.add(source)
    db.flush()
    family = DocumentFamily(
        source_id=source.id, canonical_title=f"LC Editor Test {title_suffix}",
        canonical_key=unique_key,
    )
    db.add(family)
    db.flush()
    version = DocumentVersion(
        family_id=family.id, version_label="v1", temporal_status=TemporalStatus.active,
        effective_date=date(2026, 1, 1),
    )
    db.add(version)
    db.flush()
    return family, version


def _csrf(client, canonical_key: str, extraction_id: int, field_path: str = "subject") -> str:
    """Seed the double-submit CSRF cookie (set on any GET editor fragment)
    and return its value for inclusion in a subsequent mutating POST."""
    client.get(f"/laws/{canonical_key}/extractions/{extraction_id}/fields/{field_path}/edit")
    return client.cookies.get("lc_csrf_token")


@pytest.fixture
def editable_extraction(db):
    """One obligation extraction with a top-level scalar field (subject) and
    a NESTED enforcement field carrying a material leaf (max_civil_penalty_usd)."""
    family, version = _make_family(db, jurisdiction="CO", title_suffix="Editable")
    passage = NormalizedSourceRecord(
        document_version_id=version.id, section_path="Section 1", ordinal=0,
        text_content="A developer shall comply. Civil penalty not to exceed $20,000.",
        text_hash=f"h-{uuid.uuid4().hex[:8]}",
    )
    db.add(passage)
    db.flush()
    extraction = Extraction(
        source_record_id=passage.id, extraction_type=ExtractionType.obligation,
        agent_name="obligation",
        payload={
            "subject": "A developer", "modality": "shall", "action": "comply",
            "enforcement": {"enforcing_body": "Attorney General", "max_civil_penalty_usd": 20000},
        },
        evidence_spans=[
            {"field_name": "subject", "text": "A developer", "verified": True, "match_tier": 1},
        ],
        confidence_score=0.8, confidence_tier=ConfidenceTier.B, review_status=ReviewStatus.pending,
    )
    db.add(extraction)
    db.flush()
    db.commit()
    return family.canonical_key, extraction.id


class TestFieldEditFragmentGating:
    def test_edit_form_404_when_law_cards_disabled(self, client, editable_extraction):
        canonical_key, extraction_id = editable_extraction
        settings.law_cards_enabled = False
        resp = client.get(f"/laws/{canonical_key}/extractions/{extraction_id}/fields/subject/edit")
        assert resp.status_code == 404

    def test_readonly_field_rejects_edit_mode(self, client, db):
        family, version = _make_family(db, jurisdiction="NY", title_suffix="Readonly")
        passage = NormalizedSourceRecord(
            document_version_id=version.id, section_path="s", ordinal=0,
            text_content="t", text_hash=f"h-{uuid.uuid4().hex[:8]}",
        )
        db.add(passage)
        db.flush()
        extraction = Extraction(
            source_record_id=passage.id, extraction_type=ExtractionType.obligation,
            agent_name="obligation",
            payload={"subject": "x", "timeline": {"date_parse_status": "parsed"}},
            evidence_spans=[], confidence_score=0.5, confidence_tier=ConfidenceTier.C,
            review_status=ReviewStatus.pending,
        )
        db.add(extraction)
        db.flush()
        db.commit()
        resp = client.get(
            f"/laws/{family.canonical_key}/extractions/{extraction.id}"
            "/fields/timeline.date_parse_status/edit"
        )
        assert resp.status_code == 400


class TestFieldEditFlow:
    def test_edit_form_renders_widget_reason_editor(self, client, editable_extraction):
        canonical_key, extraction_id = editable_extraction
        resp = client.get(f"/laws/{canonical_key}/extractions/{extraction_id}/fields/subject/edit")
        assert resp.status_code == 200
        assert 'name="value"' in resp.text
        assert 'name="reason"' in resp.text
        assert 'name="editor"' in resp.text

    def test_check_is_dry_run_and_reports_no_errors_for_valid_value(
        self, client, editable_extraction, db,
    ):
        canonical_key, extraction_id = editable_extraction
        resp = client.post(
            f"/laws/{canonical_key}/extractions/{extraction_id}/fields/subject/check",
            data={"value": "A regulated developer"},
        )
        assert resp.status_code == 200
        assert "lc-edit-error" not in resp.text
        ext = db.get(Extraction, extraction_id)
        assert ext.current_payload["subject"] == "A developer"  # untouched by check

    def test_check_reports_invalid_number(self, client, editable_extraction):
        canonical_key, extraction_id = editable_extraction
        resp = client.post(
            f"/laws/{canonical_key}/extractions/{extraction_id}"
            "/fields/enforcement.max_civil_penalty_usd/check",
            data={"value": "not-a-number"},
        )
        assert resp.status_code == 200
        assert "lc-edit-error" in resp.text

    def test_save_applies_edit_and_preserves_base_payload(self, client, editable_extraction, db):
        canonical_key, extraction_id = editable_extraction
        token = _csrf(client, canonical_key, extraction_id)
        resp = client.post(
            f"/laws/{canonical_key}/extractions/{extraction_id}/fields/subject/save",
            data={
                "value": "A regulated developer", "reason": "Clarify", "editor": "tester",
                "csrf_token": token,
            },
        )
        assert resp.status_code == 200
        assert "A regulated developer" in resp.text
        assert "EDITED" in resp.text

        db.refresh(db.get(Extraction, extraction_id))
        ext = db.get(Extraction, extraction_id)
        assert ext.current_payload["subject"] == "A regulated developer"
        assert ext.payload["subject"] == "A developer"  # G-1 fix: base untouched

    def test_save_on_nested_leaf_field(self, client, editable_extraction, db):
        canonical_key, extraction_id = editable_extraction
        token = _csrf(client, canonical_key, extraction_id, "enforcement.max_civil_penalty_usd")
        resp = client.post(
            f"/laws/{canonical_key}/extractions/{extraction_id}"
            "/fields/enforcement.max_civil_penalty_usd/save",
            data={
                "value": "25000", "reason": "Amendment", "editor": "tester",
                "csrf_token": token,
            },
        )
        assert resp.status_code == 200
        ext = db.get(Extraction, extraction_id)
        assert ext.current_payload["enforcement"]["max_civil_penalty_usd"] == 25000
        assert ext.payload["enforcement"]["max_civil_penalty_usd"] == 20000

    def test_save_missing_reason_returns_edit_form_with_error(
        self, client, editable_extraction, db,
    ):
        canonical_key, extraction_id = editable_extraction
        token = _csrf(client, canonical_key, extraction_id)
        resp = client.post(
            f"/laws/{canonical_key}/extractions/{extraction_id}/fields/subject/save",
            data={"value": "x", "reason": "", "editor": "tester", "csrf_token": token},
        )
        assert resp.status_code == 200
        assert "lc-edit-error" in resp.text
        assert 'name="value"' in resp.text  # stayed in edit mode, not display mode
        ext = db.get(Extraction, extraction_id)
        assert ext.current_payload["subject"] == "A developer"  # nothing persisted

    def test_revert_restores_original_and_is_scoped_to_one_field(
        self, client, editable_extraction, db,
    ):
        canonical_key, extraction_id = editable_extraction
        token = _csrf(client, canonical_key, extraction_id)
        client.post(
            f"/laws/{canonical_key}/extractions/{extraction_id}/fields/subject/save",
            data={
                "value": "A regulated developer", "reason": "Clarify", "editor": "tester",
                "csrf_token": token,
            },
        )
        client.post(
            f"/laws/{canonical_key}/extractions/{extraction_id}"
            "/fields/enforcement.max_civil_penalty_usd/save",
            data={
                "value": "25000", "reason": "Amendment", "editor": "tester",
                "csrf_token": token,
            },
        )
        ext = db.get(Extraction, extraction_id)
        subject_edit = next(
            e for e in ext.field_edits if e.field_path == "subject" and e.status.value == "applied"
        )

        resp = client.post(
            f"/laws/{canonical_key}/extractions/{extraction_id}"
            f"/fields/subject/edits/{subject_edit.id}/revert",
            data={"csrf_token": token},
        )
        assert resp.status_code == 200
        assert "A developer" in resp.text
        assert "EDITED" not in resp.text

        db.refresh(ext)
        assert ext.current_payload["subject"] == "A developer"
        assert ext.current_payload["enforcement"]["max_civil_penalty_usd"] == 25000

    def test_revert_unknown_edit_returns_400(self, client, editable_extraction):
        canonical_key, extraction_id = editable_extraction
        token = _csrf(client, canonical_key, extraction_id)
        resp = client.post(
            f"/laws/{canonical_key}/extractions/{extraction_id}"
            "/fields/subject/edits/999999999/revert",
            data={"csrf_token": token},
        )
        assert resp.status_code == 400

    def test_save_without_csrf_token_is_rejected(self, client, editable_extraction, db):
        canonical_key, extraction_id = editable_extraction
        _csrf(client, canonical_key, extraction_id)  # seed cookie, but omit it from the POST
        resp = client.post(
            f"/laws/{canonical_key}/extractions/{extraction_id}/fields/subject/save",
            data={"value": "x", "reason": "r", "editor": "tester"},
        )
        assert resp.status_code == 403
        ext = db.get(Extraction, extraction_id)
        assert ext.current_payload["subject"] == "A developer"

    def test_save_rejects_stale_known_edit_id(self, client, editable_extraction, db):
        """Optimistic lock: if another edit lands on this exact field between
        when the form was opened and Save, reject rather than silently
        overwrite the intervening change."""
        canonical_key, extraction_id = editable_extraction
        token = _csrf(client, canonical_key, extraction_id)
        client.post(
            f"/laws/{canonical_key}/extractions/{extraction_id}/fields/subject/save",
            data={
                "value": "First edit", "reason": "r1", "editor": "a",
                "csrf_token": token, "known_edit_id": "",
            },
        )
        # A second save claiming the same stale known_edit_id ("") should be rejected.
        resp = client.post(
            f"/laws/{canonical_key}/extractions/{extraction_id}/fields/subject/save",
            data={
                "value": "Conflicting edit", "reason": "r2", "editor": "b",
                "csrf_token": token, "known_edit_id": "",
            },
        )
        assert resp.status_code == 200
        assert "Someone else changed this field" in resp.text
        ext = db.get(Extraction, extraction_id)
        assert ext.current_payload["subject"] == "First edit"
