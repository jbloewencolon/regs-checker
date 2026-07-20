"""LC-4a-lite: the re-extraction exclusion checkbox — HTMX fragment route
(law_card_routes.py), JSON API route (law_card_api.py), and the assembler/
list-summary plumbing that surfaces it. Real Postgres + real TestClient
against the actual app instance, following this session's established
convention for the Law Card dashboard's test suite.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.core.config import settings
from src.db.engine import SessionLocal, get_db
from src.db.models import DocumentFamily, DocumentVersion, Source, TemporalStatus


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


@pytest.fixture
def law(db):
    # Both canonical_key AND canonical_title carry the uuid suffix — the
    # shared scratch DB this session runs against accumulates committed
    # rows across repeated test invocations (documented pollution pattern
    # elsewhere in this suite), and list_law_summaries' q= search matches
    # on title. A fixed title would let an earlier run's already-excluded
    # row leak into "before toggle" assertions here.
    suffix = uuid.uuid4().hex[:8]
    unique_key = f"US-CO-EXCLE2E-{suffix}"
    source = Source(
        jurisdiction_code="CO", jurisdiction_name="Colorado",
        source_type="state_statute", connector_id=f"excl-e2e-{uuid.uuid4().hex[:6]}",
    )
    db.add(source)
    db.flush()
    family = DocumentFamily(
        source_id=source.id, canonical_title=f"Exclusion E2E Test Act {suffix}",
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
    db.commit()
    return unique_key


def _csrf(client, canonical_key: str) -> str:
    client.get(f"/laws/{canonical_key}")
    return client.cookies.get("lc_csrf_token")


def _title_query(canonical_key: str) -> str:
    """Reconstruct this law's unique title from its canonical_key's uuid
    suffix, for scoping list-page search to just this test's own row."""
    suffix = canonical_key.rsplit("-", 1)[-1]
    return f"Exclusion E2E Test Act {suffix}"


class TestExclusionFragmentRoute:
    def test_checkbox_renders_unchecked_by_default(self, client, law):
        resp = client.get(f"/laws/{law}")
        assert resp.status_code == 200
        assert 'name="excluded"' in resp.text
        checkbox_html = resp.text.split('name="excluded"')[1][:80]
        assert "checked" not in checkbox_html

    def test_toggle_on_persists_reason_editor_timestamp(self, client, law, db):
        token = _csrf(client, law)
        resp = client.post(
            f"/laws/{law}/exclusion",
            data={
                "excluded": "true", "reason": "Verified manually",
                "editor": "tester", "csrf_token": token,
            },
        )
        assert resp.status_code == 200
        assert "checked" in resp.text
        assert "Excluded by tester" in resp.text
        assert "Verified manually" in resp.text

        family = db.query(DocumentFamily).filter_by(canonical_key=law).first()
        assert family.excluded_from_extraction is True
        assert family.excluded_reason == "Verified manually"
        assert family.excluded_by == "tester"
        assert family.excluded_at is not None

    def test_untoggle_clears_reason_editor_timestamp(self, client, law, db):
        token = _csrf(client, law)
        client.post(
            f"/laws/{law}/exclusion",
            data={"excluded": "true", "reason": "r", "editor": "a", "csrf_token": token},
        )
        resp = client.post(
            f"/laws/{law}/exclusion",
            data={"editor": "a", "csrf_token": token},  # excluded omitted = unchecked
        )
        assert resp.status_code == 200
        assert "Excluded by" not in resp.text

        db.expire_all()
        family = db.query(DocumentFamily).filter_by(canonical_key=law).first()
        assert family.excluded_from_extraction is False
        assert family.excluded_reason is None
        assert family.excluded_by is None
        assert family.excluded_at is None

    def test_missing_csrf_rejected(self, client, law, db):
        _csrf(client, law)  # seed cookie but omit from POST
        resp = client.post(f"/laws/{law}/exclusion", data={"excluded": "true", "editor": "x"})
        assert resp.status_code == 403
        family = db.query(DocumentFamily).filter_by(canonical_key=law).first()
        assert family.excluded_from_extraction is False

    def test_unknown_law_404s(self, client, law):
        # CSRF cookie must come from a real law's page — a nonexistent
        # canonical_key's page itself 404s before ever setting the cookie.
        token = _csrf(client, law)
        resp = client.post(
            "/laws/US-XX-DOES-NOT-EXIST/exclusion",
            data={"excluded": "true", "editor": "x", "csrf_token": token},
        )
        assert resp.status_code == 404

    def test_gated_404_when_law_cards_disabled(self, client, law):
        settings.law_cards_enabled = False
        resp = client.post(f"/laws/{law}/exclusion", data={"excluded": "true", "editor": "x"})
        assert resp.status_code == 404


class TestExclusionListAndCardVisibility:
    def test_list_shows_excluded_badge_after_toggle(self, client, law):
        token = _csrf(client, law)
        client.post(
            f"/laws/{law}/exclusion",
            data={"excluded": "true", "editor": "a", "csrf_token": token},
        )
        resp = client.get("/laws", params={"q": _title_query(law)})
        assert "EXCLUDED" in resp.text

    def test_list_shows_no_badge_before_toggle(self, client, law):
        resp = client.get("/laws", params={"q": _title_query(law)})
        assert "EXCLUDED" not in resp.text


class TestExclusionJsonApi:
    def test_set_exclusion_via_json_api(self, client, law, db):
        resp = client.post(
            f"/api/laws/{law}/exclusion",
            json={"excluded": True, "reason": "done reviewing", "editor": "api-tester"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["excluded_from_extraction"] is True
        assert body["excluded_reason"] == "done reviewing"
        assert body["excluded_by"] == "api-tester"
        assert body["excluded_at"] is not None

    def test_json_api_unaffected_by_disabled_ui_flag(self, client, law):
        settings.law_cards_enabled = False
        resp = client.post(
            f"/api/laws/{law}/exclusion", json={"excluded": True, "editor": "api-tester"},
        )
        assert resp.status_code == 200

    def test_json_api_requires_editor(self, client, law):
        resp = client.post(f"/api/laws/{law}/exclusion", json={"excluded": True, "editor": ""})
        assert resp.status_code == 422

    def test_json_api_unknown_law_404s(self, client):
        resp = client.post(
            "/api/laws/US-XX-NOPE/exclusion", json={"excluded": True, "editor": "x"},
        )
        assert resp.status_code == 404

    def test_card_and_list_json_expose_exclusion_state(self, client, law):
        client.post(f"/api/laws/{law}/exclusion", json={"excluded": True, "editor": "x"})

        card = client.get(f"/api/laws/{law}/card").json()
        assert card["law"]["excluded_from_extraction"] is True

        items = client.get("/api/laws", params={"q": _title_query(law)}).json()["items"]
        assert items[0]["excluded_from_extraction"] is True
