"""Tests for the Claude Code manual extraction export/import workflow."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.scripts.manual_extraction import (
    AGENT_EXTRACTION_TYPES,
    EXTRACTION_SYSTEM_PROMPT,
    SCHEMA_REFERENCE,
    _verify_spans,
    export_passages,
    import_extractions,
)


class TestVerifySpans:
    def test_verified_span(self):
        passage = "The developer shall conduct an impact assessment before deployment."
        spans = [{"field_name": "action", "text": "conduct an impact assessment"}]
        result = _verify_spans(spans, passage)
        assert len(result) == 1
        assert result[0]["verified"] is True
        assert result[0]["char_start"] == passage.index("conduct an impact assessment")

    def test_unverified_span(self):
        passage = "The developer shall conduct an impact assessment."
        spans = [{"field_name": "action", "text": "this text is not in the passage"}]
        result = _verify_spans(spans, passage)
        assert len(result) == 1
        assert result[0]["verified"] is False
        assert "char_start" not in result[0]

    def test_empty_spans(self):
        result = _verify_spans([], "some text")
        assert result == []

    def test_multiple_spans(self):
        passage = "Developer shall notify consumers within 30 days."
        spans = [
            {"field_name": "subject", "text": "Developer"},
            {"field_name": "action", "text": "notify consumers"},
            {"field_name": "timeline", "text": "not present"},
        ]
        result = _verify_spans(spans, passage)
        assert result[0]["verified"] is True
        assert result[1]["verified"] is True
        assert result[2]["verified"] is False


class TestAgentExtractionTypes:
    def test_all_agents_mapped(self):
        assert "obligation" in AGENT_EXTRACTION_TYPES
        assert "definition_actor" in AGENT_EXTRACTION_TYPES
        assert "threshold_exception" in AGENT_EXTRACTION_TYPES
        # ambiguity agent retired — findings embedded as interpretation_risks on obligation/rights payloads
        assert "ambiguity" not in AGENT_EXTRACTION_TYPES

    def test_types_are_extraction_type_enum(self):
        from src.db.models import ExtractionType
        for agent, ext_type in AGENT_EXTRACTION_TYPES.items():
            assert isinstance(ext_type, ExtractionType), f"{agent} has wrong type"


class TestExportConstants:
    def test_system_prompt_has_instructions(self):
        assert "VERBATIM" in EXTRACTION_SYSTEM_PROMPT
        assert "passage_id" in EXTRACTION_SYSTEM_PROMPT

    def test_schema_reference_has_all_types(self):
        assert "Obligation schema" in SCHEMA_REFERENCE
        assert "Definition schema" in SCHEMA_REFERENCE
        assert "Threshold/Exception schema" in SCHEMA_REFERENCE
        # Ambiguity schema replaced by interpretation_risks embedded on obligation/rights
        assert "Interpretation risks" in SCHEMA_REFERENCE
        assert "Ambiguity schema" not in SCHEMA_REFERENCE


class TestExportPassages:
    def test_no_passages_returns_empty(self):
        db = MagicMock()
        db.scalars.return_value.all.return_value = []
        result = export_passages(db)
        assert result["total_passages"] == 0
        assert result["batches"] == 0

    @patch("src.scripts.manual_extraction.EXPORT_DIR")
    def test_filters_short_passages(self, mock_dir):
        mock_dir.mkdir = MagicMock()
        mock_dir.glob = MagicMock(return_value=[])
        mock_dir.__truediv__ = lambda self, x: Path(tempfile.mkdtemp()) / x

        short_record = MagicMock()
        short_record.text_content = "Too short."
        short_record.id = 1

        long_record = MagicMock()
        long_record.text_content = "x" * 200
        long_record.id = 2
        long_record.document_version = None

        db = MagicMock()
        db.scalars.return_value.all.return_value = [short_record, long_record]

        result = export_passages(db)
        assert result["skipped_short"] == 1
        assert result["total_passages"] == 1


class TestImportExtractions:
    def test_no_files_returns_empty(self):
        db = MagicMock()
        with patch("src.scripts.manual_extraction.EXPORT_DIR") as mock_dir:
            mock_dir.glob.return_value = []
            result = import_extractions(db)
            assert result["files_processed"] == 0

    def test_import_from_specific_file(self):
        """Test importing a valid extraction result file."""
        db = MagicMock()

        # Mock the NormalizedSourceRecord lookup
        mock_record = MagicMock()
        mock_record.id = 42
        mock_record.text_content = "The developer shall conduct an impact assessment before deployment."
        mock_record.document_version = MagicMock()
        mock_record.document_version.ingestion_jobs = []
        db.get.return_value = mock_record

        # Mock no existing duplicates
        db.scalars.return_value.first.return_value = None

        # Create a temp result file
        results = [
            {
                "passage_id": 42,
                "extractions": [
                    {
                        "agent": "obligation",
                        "items": [
                            {
                                "subject": "developer",
                                "modality": "shall",
                                "action": "conduct an impact assessment",
                                "condition": "before deployment",
                                "evidence_spans": [
                                    {"field_name": "action", "text": "conduct an impact assessment"}
                                ],
                            }
                        ],
                    }
                ],
            }
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(results, f)
            temp_path = f.name

        try:
            result = import_extractions(db, input_path=temp_path)
            assert result["extractions_created"] == 1
            assert result["errors"] == 0
            # Verify db.add was called (Extraction + ReviewQueueItem)
            assert db.add.call_count >= 2
        finally:
            # Clean up the .done file
            done_path = Path(temp_path).with_suffix(".json.done")
            if done_path.exists():
                done_path.unlink()
            if Path(temp_path).exists():
                Path(temp_path).unlink()

    def test_import_abstention(self):
        """Abstention entries should be skipped gracefully."""
        db = MagicMock()

        mock_record = MagicMock()
        mock_record.id = 10
        db.get.return_value = mock_record

        results = [
            {
                "passage_id": 10,
                "extractions": [],
                "abstention_reason": "No extractable content",
            }
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(results, f)
            temp_path = f.name

        try:
            result = import_extractions(db, input_path=temp_path)
            assert result["extractions_created"] == 0
            assert result["errors"] == 0
        finally:
            done_path = Path(temp_path).with_suffix(".json.done")
            if done_path.exists():
                done_path.unlink()
            if Path(temp_path).exists():
                Path(temp_path).unlink()

    def test_import_unknown_agent(self):
        """Unknown agent names should be logged as errors."""
        db = MagicMock()

        mock_record = MagicMock()
        mock_record.id = 5
        db.get.return_value = mock_record

        results = [
            {
                "passage_id": 5,
                "extractions": [
                    {"agent": "nonexistent_agent", "items": [{"foo": "bar"}]}
                ],
            }
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(results, f)
            temp_path = f.name

        try:
            result = import_extractions(db, input_path=temp_path)
            assert result["errors"] >= 1
        finally:
            done_path = Path(temp_path).with_suffix(".json.done")
            if done_path.exists():
                done_path.unlink()
            if Path(temp_path).exists():
                Path(temp_path).unlink()
