"""Unit tests for src/scripts/reground_spans.py (EA2-2 backfill support).

The script re-runs verify_evidence_spans() against stored passages and
writes back updated evidence_spans. Historically it only persisted a
row when a span flipped from unverified to verified. EA2-2 added
match_tier/loose_match/raw-offset provenance to every verified span, so
_reground_batch must also detect and persist "already verified, but
predates the new provenance fields" rows — otherwise the --backfill-
provenance CLI flag would broaden which rows are FETCHED without ever
actually writing anything for rows that were already fully verified.

No live database is used: session is a MagicMock, and the real
verify_evidence_spans() is used against in-memory passage/span dicts
so this test exercises the actual matcher, not a stub.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.scripts.reground_spans import _reground_batch


def _row(extraction_id: int, text_content: str, evidence_spans: list[dict], extraction_type: str = "obligation") -> dict:
    return {
        "id": extraction_id,
        "extraction_type": extraction_type,
        "evidence_spans": evidence_spans,
        "text_content": text_content,
    }


class TestFlippedUnverifiedSpans:
    def test_span_that_now_verifies_is_written(self):
        # Old data: span text matches the passage but was previously marked
        # unverified (e.g. from before a matcher improvement).
        row = _row(
            1,
            "A developer shall conduct an annual audit.",
            [{"field_name": "action", "text": "shall conduct an annual audit", "verified": False}],
        )
        session = MagicMock()
        stats = _reground_batch(session, [row], dry_run=False)
        assert stats["processed"] == 1
        assert stats["updated"] == 1
        assert stats["spans_flipped"] == 1
        assert stats["spans_provenance_added"] == 0
        session.execute.assert_called_once()

    def test_span_that_stays_unverified_is_not_written(self):
        row = _row(
            2,
            "A developer shall conduct an annual audit.",
            [{"field_name": "action", "text": "this text is nowhere in the passage", "verified": False}],
        )
        session = MagicMock()
        stats = _reground_batch(session, [row], dry_run=False)
        assert stats["processed"] == 1
        assert stats["updated"] == 0
        assert stats["spans_flipped"] == 0
        session.execute.assert_not_called()


class TestProvenanceBackfill:
    def test_already_verified_span_missing_match_tier_is_written(self):
        # Simulates a pre-EA2-2 row: verified=True but no match_tier key —
        # this must be detected and persisted even though "verified" never
        # flips from False to True.
        row = _row(
            3,
            "A developer shall conduct an annual audit.",
            [{"field_name": "action", "text": "shall conduct an annual audit", "verified": True}],
        )
        session = MagicMock()
        stats = _reground_batch(session, [row], dry_run=False)
        assert stats["updated"] == 1
        assert stats["spans_flipped"] == 0
        assert stats["spans_provenance_added"] == 1
        session.execute.assert_called_once()

    def test_already_verified_span_with_match_tier_is_not_rewritten(self):
        # Simulates a post-EA2-2 row that's already fully up to date —
        # must not be needlessly rewritten on every backfill run.
        row = _row(
            4,
            "A developer shall conduct an annual audit.",
            [{
                "field_name": "action",
                "text": "shall conduct an annual audit",
                "verified": True,
                "match_tier": 1,
                "loose_match": False,
                "char_start": 12,
                "char_end": 42,
            }],
        )
        session = MagicMock()
        stats = _reground_batch(session, [row], dry_run=False)
        assert stats["updated"] == 0
        assert stats["spans_flipped"] == 0
        assert stats["spans_provenance_added"] == 0
        session.execute.assert_not_called()


class TestDryRun:
    def test_dry_run_computes_stats_without_writing(self):
        row = _row(
            5,
            "A developer shall conduct an annual audit.",
            [{"field_name": "action", "text": "shall conduct an annual audit", "verified": True}],
        )
        session = MagicMock()
        stats = _reground_batch(session, [row], dry_run=True)
        assert stats["spans_provenance_added"] == 1
        session.execute.assert_not_called()
        session.commit.assert_not_called()


class TestSkippedRows:
    def test_no_spans_is_skipped(self):
        row = _row(6, "Some passage text.", [])
        session = MagicMock()
        stats = _reground_batch(session, [row], dry_run=False)
        assert stats["processed"] == 0
        assert stats["updated"] == 0

    def test_no_passage_text_is_skipped(self):
        row = _row(7, "", [{"field_name": "action", "text": "shall comply", "verified": False}])
        session = MagicMock()
        stats = _reground_batch(session, [row], dry_run=False)
        assert stats["processed"] == 0


class TestMixedBatch:
    def test_batch_with_multiple_rows_aggregates_correctly(self):
        rows = [
            _row(8, "A developer shall comply with this section.",
                 [{"field_name": "action", "text": "shall comply with this section", "verified": False}]),
            _row(9, "A developer shall comply with this section.",
                 [{"field_name": "action", "text": "shall comply with this section", "verified": True}]),
            _row(10, "A developer shall comply with this section.",
                 [{
                     "field_name": "action", "text": "shall comply with this section",
                     "verified": True, "match_tier": 1, "loose_match": False,
                     "char_start": 12, "char_end": 43,
                 }]),
        ]
        session = MagicMock()
        stats = _reground_batch(session, rows, dry_run=False)
        assert stats["processed"] == 3
        assert stats["spans_flipped"] == 1       # row 8
        assert stats["spans_provenance_added"] == 1  # row 9
        assert stats["updated"] == 2             # rows 8 and 9; row 10 untouched
        assert session.execute.call_count == 2
