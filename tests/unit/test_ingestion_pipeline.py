"""Unit tests for the ingestion pipeline (fetch → store → parse → chunk)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.db.models import IngestionJob, IngestionStatus, NormalizedSourceRecord, RawArtifact
from src.ingestion.pipeline import (
    compute_parse_quality,
    process_single_job,
    run_pending_ingestion,
)


class TestComputeParseQuality:
    def test_empty_records(self):
        assert compute_parse_quality([]) == 0.0

    def test_normal_records(self):
        records = [
            MagicMock(text_content="A" * 100),
            MagicMock(text_content="B" * 200),
        ]
        assert compute_parse_quality(records) == 1.0

    def test_short_records_penalized(self):
        records = [MagicMock(text_content="Hi")]
        score = compute_parse_quality(records)
        assert score == 0.5

    def test_long_records_penalized(self):
        records = [MagicMock(text_content="X" * 6000)]
        score = compute_parse_quality(records)
        assert score == 0.8

    def test_mixed_records(self):
        records = [
            MagicMock(text_content="A" * 100),  # 1.0
            MagicMock(text_content="B" * 5),     # 0.5
        ]
        assert compute_parse_quality(records) == 0.75


class TestProcessSingleJob:
    @patch("src.ingestion.pipeline.parse_and_normalize")
    @patch("src.ingestion.pipeline.fetch_document")
    def test_successful_ingestion(self, mock_fetch, mock_parse):
        """Full successful path: fetch → store → parse → chunk → completed."""
        db = MagicMock()
        job = MagicMock(spec=IngestionJob)
        job.id = 1
        job.fetch_url = "https://example.com/bill.pdf"
        job.document_version_id = 42

        mock_artifact = MagicMock(spec=RawArtifact)
        mock_artifact.content_type = "application/pdf"
        mock_artifact.size_bytes = 50000
        mock_artifact.sha256_hash = "abc123def456"
        mock_fetch.return_value = mock_artifact

        mock_records = [
            MagicMock(spec=NormalizedSourceRecord, text_content="A" * 100),
            MagicMock(spec=NormalizedSourceRecord, text_content="B" * 200),
        ]
        mock_parse.return_value = mock_records

        # Track status transitions
        statuses = []
        original_setattr = type(job).__setattr__

        def track_status(self, name, value):
            if name == "status":
                statuses.append(value)
            original_setattr(self, name, value)

        with patch.object(type(job), "__setattr__", track_status):
            count = process_single_job(db, job)

        assert count == 2
        mock_fetch.assert_called_once_with(db, job)
        mock_parse.assert_called_once_with(db, job, mock_artifact)

        # Verify status transitions happened in order
        assert IngestionStatus.fetching in statuses
        assert IngestionStatus.fetched in statuses
        assert IngestionStatus.parsing in statuses
        assert IngestionStatus.completed in statuses

    @patch("src.ingestion.pipeline.fetch_document")
    def test_fetch_failure_marks_failed(self, mock_fetch):
        """When fetch raises an exception, job should be marked failed."""
        db = MagicMock()
        job = MagicMock(spec=IngestionJob)
        job.id = 2
        job.fetch_url = "https://bad-url.example.com/404"
        job.document_version_id = 99

        mock_fetch.side_effect = Exception("Connection refused")

        count = process_single_job(db, job)

        assert count == 0
        assert job.status == IngestionStatus.failed
        assert "Connection refused" in job.error_message
        db.commit.assert_called()

    @patch("src.ingestion.pipeline.parse_and_normalize")
    @patch("src.ingestion.pipeline.fetch_document")
    def test_parse_failure_marks_failed(self, mock_fetch, mock_parse):
        """When parse raises, job should be marked failed after successful fetch."""
        db = MagicMock()
        job = MagicMock(spec=IngestionJob)
        job.id = 3
        job.fetch_url = "https://example.com/bill.html"

        mock_fetch.return_value = MagicMock(spec=RawArtifact)
        mock_parse.side_effect = Exception("PDF parsing failed: corrupted file")

        count = process_single_job(db, job)

        assert count == 0
        assert job.status == IngestionStatus.failed
        assert "PDF parsing failed" in job.error_message

    @patch("src.ingestion.pipeline.parse_and_normalize")
    @patch("src.ingestion.pipeline.fetch_document")
    def test_progress_callback(self, mock_fetch, mock_parse):
        """Verify on_progress callback is called with status messages."""
        db = MagicMock()
        job = MagicMock(spec=IngestionJob)
        job.id = 4
        job.fetch_url = "https://example.com/test.html"
        job.document_version_id = 10

        mock_artifact = MagicMock(spec=RawArtifact)
        mock_artifact.content_type = "text/html"
        mock_artifact.size_bytes = 1000
        mock_artifact.sha256_hash = "aabbccdd"
        mock_fetch.return_value = mock_artifact
        mock_parse.return_value = [MagicMock(text_content="X" * 50)]

        messages = []
        process_single_job(db, job, on_progress=messages.append)

        assert len(messages) >= 2
        assert any("Fetching" in m for m in messages)
        assert any("passages" in m for m in messages)

    @patch("src.ingestion.pipeline.fetch_document")
    def test_long_error_message_truncated(self, mock_fetch):
        """Error messages longer than 2000 chars should be truncated."""
        db = MagicMock()
        job = MagicMock(spec=IngestionJob)
        job.id = 5
        job.fetch_url = "https://example.com"

        mock_fetch.side_effect = Exception("X" * 5000)

        process_single_job(db, job)

        assert len(job.error_message) <= 2000


class TestRunPendingIngestion:
    @patch("src.ingestion.pipeline.process_single_job")
    def test_no_pending_jobs(self, mock_process):
        db = MagicMock()
        db.scalars.return_value.all.return_value = []

        messages = []
        summary = run_pending_ingestion(db, on_progress=messages.append)

        assert summary["total_pending"] == 0
        assert summary["completed"] == 0
        mock_process.assert_not_called()

    @patch("src.ingestion.pipeline.process_single_job")
    def test_processes_all_pending(self, mock_process):
        db = MagicMock()

        job1 = MagicMock(spec=IngestionJob)
        job1.id = 1
        job1.document_version = MagicMock()
        job1.document_version.family.source.jurisdiction_code = "CO"
        job1.document_version.family.short_cite = "SB 205"
        job2 = MagicMock(spec=IngestionJob)
        job2.id = 2
        job2.document_version = MagicMock()
        job2.document_version.family.source.jurisdiction_code = "CA"
        job2.document_version.family.short_cite = "AB 2885"

        db.scalars.return_value.all.return_value = [job1, job2]

        def side_effect(db, job, on_progress=None):
            job.status = IngestionStatus.completed
            return 10

        mock_process.side_effect = side_effect

        summary = run_pending_ingestion(db)

        assert summary["total_pending"] == 2
        assert summary["completed"] == 2
        assert summary["total_passages"] == 20
        assert mock_process.call_count == 2

    @patch("src.ingestion.pipeline.process_single_job")
    def test_counts_failures(self, mock_process):
        db = MagicMock()

        job1 = MagicMock(spec=IngestionJob)
        job1.id = 1
        job1.document_version = MagicMock()
        job1.document_version.family.source.jurisdiction_code = "CO"
        job1.document_version.family.short_cite = "SB 205"

        db.scalars.return_value.all.return_value = [job1]

        def side_effect(db, job, on_progress=None):
            job.status = IngestionStatus.failed
            job.error_message = "Timeout"
            return 0

        mock_process.side_effect = side_effect

        messages = []
        summary = run_pending_ingestion(db, on_progress=messages.append)

        assert summary["failed"] == 1
        assert summary["completed"] == 0
        assert any("FAILED" in m for m in messages)
