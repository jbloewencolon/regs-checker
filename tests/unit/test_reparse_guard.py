"""Unit tests for SFH-1h (audit SF-10) — reparse lineage guard.

Re-parsing a document version deletes its existing passages, which severs the
FK lineage of every extraction built on them. Within one static version that
was survivable; with live amended-bill versions it silently destroys the
history a compliance product exists to answer ("what changed and when").
The guard: a re-parse that would orphan linked extractions is blocked unless
force_reparse=True is passed explicitly, and the discarded count is logged.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.db.models import IngestionJob, IngestionStatus, RawArtifact
from src.ingestion.pipeline import process_single_job


def _make_db(old_passage_count: int, linked_extractions: int):
    db = MagicMock()
    old_records = [MagicMock(id=i + 1) for i in range(old_passage_count)]
    db.scalars.return_value.all.return_value = old_records
    db.scalar.return_value = linked_extractions
    return db


def _make_job():
    job = MagicMock(spec=IngestionJob)
    job.id = 7
    job.document_version_id = 42
    return job


@patch("src.ingestion.pipeline.fetch_document")
def test_reparse_with_linked_extractions_blocked(mock_fetch):
    mock_fetch.return_value = MagicMock(
        spec=RawArtifact, content_type="text/html", size_bytes=1024
    )
    db = _make_db(old_passage_count=46, linked_extractions=212)
    job = _make_job()

    count = process_single_job(db, job)

    assert count == 0
    assert job.status == IngestionStatus.failed
    assert "Re-parse blocked (SF-10)" in job.error_message
    assert "212" in job.error_message
    # Nothing was deleted.
    db.delete.assert_not_called()


@patch("src.ingestion.pipeline.parse_and_normalize")
@patch("src.ingestion.pipeline.fetch_document")
def test_force_reparse_proceeds_and_deletes(mock_fetch, mock_parse):
    mock_fetch.return_value = MagicMock(
        spec=RawArtifact, content_type="text/html", size_bytes=1024
    )
    mock_parse.return_value = [MagicMock(text_content="x" * 200)]
    db = _make_db(old_passage_count=3, linked_extractions=9)
    job = _make_job()

    count = process_single_job(db, job, force_reparse=True)

    assert count == 1
    # The old passages were deleted (explicitly opted in).
    assert db.delete.call_count == 3


@patch("src.ingestion.pipeline.parse_and_normalize")
@patch("src.ingestion.pipeline.fetch_document")
def test_reparse_without_extractions_needs_no_flag(mock_fetch, mock_parse):
    # Old passages with ZERO linked extractions are safe to clear — no
    # lineage to destroy, no flag required (the pre-SFH-1h behavior).
    mock_fetch.return_value = MagicMock(
        spec=RawArtifact, content_type="text/html", size_bytes=1024
    )
    mock_parse.return_value = [MagicMock(text_content="x" * 200)]
    db = _make_db(old_passage_count=5, linked_extractions=0)
    job = _make_job()

    count = process_single_job(db, job)

    assert count == 1
    assert db.delete.call_count == 5
