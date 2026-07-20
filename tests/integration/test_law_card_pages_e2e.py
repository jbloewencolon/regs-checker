"""End-to-end tests for the Law Card HTML pages (LC-2) — asserts the
design-rule behaviors from docs/law_card_design_rules.md against real
rendered HTML, using the actual app instance (src.api.app.app) so
templates/static state is wired the same way it is in production.

Real Postgres + real FastAPI TestClient, following test_pipeline_e2e.py's
convention. settings.law_cards_enabled is flipped on/off directly on the
imported Settings singleton and restored in a fixture teardown, since these
routes 404 when the flag is off.
"""
from __future__ import annotations

import re
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.core.config import settings
from src.db.engine import SessionLocal, get_db
from src.db.models import (
    BillLevelExtraction,
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


def _make_family(db, *, jurisdiction, temporal_status, title_suffix):
    unique_key = f"US-{jurisdiction}-LCPAGE-{uuid.uuid4().hex[:8]}"
    source = Source(
        jurisdiction_code=jurisdiction, jurisdiction_name=jurisdiction,
        source_type="state_statute", connector_id=f"lc-pages-test-{uuid.uuid4().hex[:6]}",
    )
    db.add(source)
    db.flush()
    family = DocumentFamily(
        source_id=source.id, canonical_title=f"LC Page Test {title_suffix}",
        canonical_key=unique_key,
    )
    db.add(family)
    db.flush()
    version = DocumentVersion(
        family_id=family.id, version_label="v1", temporal_status=temporal_status,
        effective_date=date(2026, 1, 1),
    )
    db.add(version)
    db.flush()
    return family, version


@pytest.fixture
def rich_law(db):
    """A law with one extraction exercising all three evidence tiers plus a
    null field, and a bill-level enforcement row with a TBD penalty amount."""
    family, version = _make_family(
        db, jurisdiction="CO", temporal_status=TemporalStatus.active, title_suffix="Rich",
    )
    passage = NormalizedSourceRecord(
        document_version_id=version.id, section_path="Section 1", ordinal=0,
        text_content="A developer shall use reasonable care.",
        text_hash=f"h-{uuid.uuid4().hex[:8]}",
    )
    db.add(passage)
    db.flush()
    extraction = Extraction(
        source_record_id=passage.id, extraction_type=ExtractionType.obligation,
        agent_name="obligation",
        payload={
            "subject": "A developer", "modality": "shall",
            "action": "use reasonable care", "conditions": None,
        },
        evidence_spans=[
            {
                "field_name": "subject", "text": "A developer", "verified": True,
                "match_tier": 1, "char_start": 0, "char_end": 12,
            },
            {
                "field_name": "action", "text": "use reasonable care",
                "verified": True, "match_tier": 3, "loose_match": True,
            },
            {"field_name": "modality", "text": "shall", "verified": False},
        ],
        confidence_score=0.82, confidence_tier=ConfidenceTier.B, review_status=ReviewStatus.pending,
    )
    db.add(extraction)
    db.flush()
    bill_enf = BillLevelExtraction(
        document_version_id=version.id, agent_name="enforcement_agent",
        payload={
            "enforcing_body": "Attorney General", "penalty_type": "civil_penalty",
            "max_civil_penalty_usd": None,
            "enforcement_text": "Penalty amount TBD pending rulemaking.",
        },
        confidence_score=0.7, model_id="test-model", truncated=False,
    )
    db.add(bill_enf)
    db.flush()
    db.commit()
    return family.canonical_key


@pytest.fixture
def withdrawn_law_with_enforcement_data(db):
    family, version = _make_family(
        db, jurisdiction="NM", temporal_status=TemporalStatus.withdrawn, title_suffix="Withdrawn",
    )
    bill_enf = BillLevelExtraction(
        document_version_id=version.id, agent_name="enforcement_agent",
        payload={
            "enforcing_body": "AG", "penalty_type": "civil_penalty",
            "max_civil_penalty_usd": 50000,
        },
        confidence_score=0.9, model_id="test-model", truncated=False,
    )
    db.add(bill_enf)
    db.flush()
    db.commit()
    return family.canonical_key


@pytest.fixture
def stub_law(db):
    family, _version = _make_family(
        db, jurisdiction="TX", temporal_status=TemporalStatus.active, title_suffix="Stub",
    )
    db.commit()
    return family.canonical_key


class TestListPage:
    def test_list_page_renders(self, client, rich_law):
        resp = client.get("/laws", params={"q": "LC Page Test Rich"})
        assert resp.status_code == 200
        assert "LC Page Test Rich" in resp.text

    def test_gated_404_when_disabled(self, client):
        settings.law_cards_enabled = False
        resp = client.get("/laws")
        assert resp.status_code == 404


class TestDesignRuleCompliance:
    def test_rule1_honest_unknown_null_field_renders_gap_badge(self, client, rich_law):
        resp = client.get(f"/laws/{rich_law}")
        assert resp.status_code == 200
        assert "gap-badge" in resp.text
        assert ">None<" not in resp.text
        assert ">null<" not in resp.text

    def test_rule2_enforcement_visible_with_tbd_gap_badge(self, client, rich_law):
        resp = client.get(f"/laws/{rich_law}")
        assert "<h3>Enforcement</h3>" in resp.text
        assert "Penalty amount not yet specified" in resp.text

    def test_rule2_enforcement_suppressed_for_withdrawn_law(
        self, client, withdrawn_law_with_enforcement_data,
    ):
        resp = client.get(f"/laws/{withdrawn_law_with_enforcement_data}")
        assert resp.status_code == 200
        assert "<h3>Enforcement</h3>" not in resp.text

    def test_rule3_evidence_tiers_rendered_distinctly(self, client, rich_law):
        resp = client.get(f"/laws/{rich_law}")
        html = resp.text
        assert "lc-evidence-quote" in html
        assert "lc-evidence-near-match" in html
        assert "lc-evidence-unverified" in html

    def test_rule3_unverified_span_never_in_quote_markup(self, client, rich_law):
        html = client.get(f"/laws/{rich_law}").text
        # The unverified "shall" span's own block must not carry the quote class.
        unverified_block = re.search(r'<div class="lc-evidence-unverified">.*?</div>', html, re.S)
        assert unverified_block is not None
        assert "lc-evidence-quote" not in unverified_block.group(0)

    def test_rule4_disclosure_buttons_are_real_buttons_with_aria(self, client, rich_law):
        html = client.get(f"/laws/{rich_law}").text
        assert '<button type="button" class="lc-disclosure-btn"' in html
        controls = set(re.findall(r'aria-controls="([^"]+)"', html))
        ids = set(re.findall(r' id="([^"]+)"', html))
        assert controls, "expected at least one disclosure toggle"
        assert controls <= ids, f"dangling aria-controls: {controls - ids}"

    def test_rule5_status_is_humanized_not_raw_enum(self, client, rich_law):
        html = client.get(f"/laws/{rich_law}").text
        assert "status-active" in html
        assert ">Active<" in html

    def test_rule7_zero_extraction_law_routes_to_stub(self, client, stub_law):
        resp = client.get(f"/laws/{stub_law}")
        assert resp.status_code == 200
        assert "lc-stub-card" in resp.text
        assert "No AI-relevant provisions extracted" in resp.text
        assert "<h3>Extractions" not in resp.text


class TestFlagScope:
    def test_json_api_unaffected_by_disabled_flag(self, client, rich_law):
        settings.law_cards_enabled = False
        resp = client.get(f"/api/laws/{rich_law}/card")
        assert resp.status_code == 200


class TestStatusLabelExhaustiveness:
    def test_every_temporal_status_has_a_label(self):
        from src.core.law_card_labels import STATUS_LABELS
        from src.db.models import TemporalStatus

        missing = [s.value for s in TemporalStatus if s.value not in STATUS_LABELS]
        assert not missing, f"TemporalStatus values missing from STATUS_LABELS: {missing}"
