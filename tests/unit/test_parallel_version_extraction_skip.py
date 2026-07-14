"""Tests for QA-8's extraction-time skip: non-representative parallel-version
restatements never reach the agent battery.

`parse_and_normalize` (see test_parallel_version_grouping.py) tags every
passage in a parallel-version group with parallel_version_representative.
`_check_parallel_version` reads that flag; `extract_single_record` returns
sentinel -2 (distinct from the jurisdiction skip's -1) so the run summary and
conservation ledger can track it separately from a real failure or a
legitimate zero-extraction passage.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.db.models import NormalizedSourceRecord
from src.ingestion.extractor import MergedPassage, _check_parallel_version, extract_single_record


def _record(metadata: dict | None = None, record_id: int = 1) -> NormalizedSourceRecord:
    r = NormalizedSourceRecord(
        id=record_id,
        document_version_id=1,
        section_path="Section 647",
        ordinal=0,
        text_content="Section 647 of the Penal Code is amended to read: 647. Except...",
        text_hash="deadbeef",
        metadata_=metadata or {},
    )
    return r


class TestCheckParallelVersion:
    def test_no_metadata_proceeds(self):
        assert _check_parallel_version(_record({})) is True

    def test_representative_true_proceeds(self):
        meta = {
            "parallel_version_group": "penal code:647",
            "parallel_version_representative": True,
            "parallel_version_count": 8,
        }
        assert _check_parallel_version(_record(meta)) is True

    def test_representative_false_skips(self):
        meta = {
            "parallel_version_group": "penal code:647",
            "parallel_version_representative": False,
            "parallel_version_count": 8,
        }
        assert _check_parallel_version(_record(meta)) is False

    def test_unrelated_metadata_proceeds(self):
        meta = {"amendment_markup_detected": True}
        assert _check_parallel_version(_record(meta)) is True

    def test_null_metadata_column_proceeds(self):
        # metadata_ defaults to a dict, but guard against a raw None too
        # (e.g. a row written before the metadata_ default existed).
        r = _record({})
        r.metadata_ = None
        assert _check_parallel_version(r) is True


class TestExtractSingleRecordSkipsNonRepresentative:
    def test_non_representative_returns_sentinel_without_agent_calls(self):
        record = _record(
            {
                "parallel_version_group": "penal code:647",
                "parallel_version_representative": False,
                "parallel_version_count": 8,
            }
        )
        # document_version is None on this bare record — _check_jurisdiction
        # fails open (returns True) when there's no family/source to check.
        passage = MergedPassage(text=record.text_content, source_records=[record])
        agent = MagicMock()

        with patch("src.core.extraction_monitor.get_monitor") as mock_monitor:
            mock_monitor.return_value = MagicMock()
            count = extract_single_record(
                db=MagicMock(),
                passage=passage,
                agents={"obligation": agent},
                succeeded_attempts=None,
            )

        assert count == -2
        agent.extract.assert_not_called()

    def test_representative_passage_is_not_short_circuited(self):
        # Sanity check on the fixture itself: a representative (or unflagged)
        # record must NOT hit the -2 path, so the parallel-version check
        # isn't accidentally inverted.
        record = _record(
            {
                "parallel_version_group": "penal code:647",
                "parallel_version_representative": True,
                "parallel_version_count": 8,
            }
        )
        assert _check_parallel_version(record) is True
