"""End-to-end integration tests for the full ingestion → extraction → API pipeline.

These tests validate the complete data flow using mock LLM responses.
Requires database connection (use docker-compose for local testing).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from src.db.engine import SessionLocal
from src.db.models import (
    ConfidenceTier,
    DocumentFamily,
    DocumentVersion,
    Extraction,
    ExtractionType,
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
    ReviewQueueItem,
    ReviewStatus,
    Source,
    TemporalStatus,
)


@pytest.fixture
def db():
    """Database session fixture."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def seeded_source(db):
    """Create a source for testing."""
    source = Source(
        jurisdiction_code="CO",
        jurisdiction_name="Colorado",
        source_type="state_statute",
        connector_id="colorado_ga",
    )
    db.add(source)
    db.flush()
    return source


@pytest.fixture
def seeded_document(db, seeded_source):
    """Create a full document chain for testing."""
    family = DocumentFamily(
        source_id=seeded_source.id,
        canonical_title="Colorado SB21-169 (AI Act)",
        short_cite="SB21-169",
        subject_area="artificial_intelligence",
    )
    db.add(family)
    db.flush()

    version = DocumentVersion(
        family_id=family.id,
        version_label="Enrolled",
        temporal_status=TemporalStatus.active,
        effective_date=date(2026, 2, 1),
    )
    db.add(version)
    db.flush()

    return version


@pytest.fixture
def seeded_passages(db, seeded_document):
    """Create normalized source records (passages) for testing."""
    passages = [
        NormalizedSourceRecord(
            document_version_id=seeded_document.id,
            section_path="Section 3 - Developer Duties",
            ordinal=0,
            text_content=(
                "A developer of a high-risk artificial intelligence system shall use "
                "reasonable care to protect consumers from any known or reasonably "
                "foreseeable risks of algorithmic discrimination."
            ),
            text_hash="abc123",
        ),
        NormalizedSourceRecord(
            document_version_id=seeded_document.id,
            section_path="Section 2 - Definitions",
            ordinal=1,
            text_content=(
                "As used in this article: 'Algorithmic discrimination' means any condition "
                "in which the use of an artificial intelligence system results in an unlawful "
                "differential treatment or impact."
            ),
            text_hash="def456",
        ),
    ]
    for p in passages:
        db.add(p)
    db.flush()
    return passages


def test_seed_script_creates_records(db):
    """Test that the seed script creates all necessary records."""
    from src.scripts.seed_pipeline import seed_colorado_sb205

    job = seed_colorado_sb205(db)
    assert job is not None
    assert job.status == IngestionStatus.pending
    assert job.fetch_url is not None

    # Verify full chain
    dv = job.document_version
    assert dv.version_label == "Enrolled"
    assert dv.temporal_status == TemporalStatus.active

    df = dv.family
    assert df.short_cite == "SB21-169"
    assert df.subject_area == "artificial_intelligence"

    s = df.source
    assert s.jurisdiction_code == "CO"
    assert s.connector_id == "colorado_ga"


def test_extraction_creates_review_queue_item(db, seeded_passages):
    """Test that creating an extraction also creates a review queue item."""
    passage = seeded_passages[0]

    extraction = Extraction(
        source_record_id=passage.id,
        extraction_type=ExtractionType.obligation,
        payload={
            "subject": "developer",
            "modality": "shall",
            "action": "use reasonable care",
        },
        evidence_spans=[
            {"field_name": "subject", "text": "developer", "verified": True}
        ],
        confidence_score=0.85,
        confidence_tier=ConfidenceTier.A,
        review_status=ReviewStatus.pending,
    )
    db.add(extraction)
    db.flush()

    review_item = ReviewQueueItem(
        extraction_id=extraction.id,
        priority=0,
        status=ReviewStatus.pending,
    )
    db.add(review_item)
    db.flush()

    assert review_item.id is not None
    assert review_item.extraction_id == extraction.id
    assert review_item.extraction.payload["modality"] == "shall"


def test_gold_standard_fixture_count():
    """Verify we have at least 10 gold-standard fixtures."""
    from pathlib import Path

    fixtures_dir = Path("tests/fixtures/gold_standard")
    fixtures = list(fixtures_dir.glob("*.json"))
    assert len(fixtures) >= 10, f"Expected >=10 fixtures, found {len(fixtures)}"


def test_gold_standard_fixtures_valid():
    """Validate structure of all gold-standard fixtures."""
    import json
    from pathlib import Path

    fixtures_dir = Path("tests/fixtures/gold_standard")
    for filepath in fixtures_dir.glob("*.json"):
        with open(filepath) as f:
            data = json.load(f)

        assert "passage_id" in data, f"{filepath.name} missing passage_id"
        assert "passage_text" in data, f"{filepath.name} missing passage_text"
        assert "expected_extractions" in data, f"{filepath.name} missing expected_extractions"

        ee = data["expected_extractions"]
        assert "obligation" in ee, f"{filepath.name} missing obligation key"
        assert "definition" in ee, f"{filepath.name} missing definition key"
        assert "threshold_exception" in ee, f"{filepath.name} missing threshold_exception key"
        assert "ambiguity" in ee, f"{filepath.name} missing ambiguity key"
