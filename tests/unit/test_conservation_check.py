"""Unit tests for SFH-1b (audit SF-06) — passage-conservation check.

The historical failure: 660 passages selected, 647 accounted for, 13 lost with
zero failure records — discoverable only by manually diffing run_summary.json
against agent_stats.json. compute_conservation() is the run-end invariant that
makes that class of silent loss impossible to miss.
"""

from __future__ import annotations

from src.ingestion.extractor import compute_conservation


class TestComputeConservation:
    def test_perfectly_conserved_run(self):
        report = compute_conservation(
            selected_ids={1, 2, 3, 4, 5},
            outcomes={
                "processed": {1, 2, 3},
                "failed": {4},
                "skipped_jurisdiction": {5},
            },
        )
        assert report["conserved"] is True
        assert report["residual_count"] == 0
        assert report["residual_ids"] == []
        assert report["selected"] == 5
        assert report["processed"] == 3
        assert report["failed"] == 1
        assert report["skipped_jurisdiction"] == 1

    def test_residual_detected_with_ids(self):
        # The 660-vs-647 shape: selected records that ended in NO bucket.
        report = compute_conservation(
            selected_ids={1, 2, 3, 4, 5},
            outcomes={"processed": {1, 2}, "failed": {3}},
        )
        assert report["conserved"] is False
        assert report["residual_count"] == 2
        assert report["residual_ids"] == [4, 5]

    def test_double_counting_detected(self):
        # A record in two buckets is a different integrity bug (an outcome
        # path that fired twice) — also not conserved.
        report = compute_conservation(
            selected_ids={1, 2},
            outcomes={"processed": {1, 2}, "failed": {2}},
        )
        assert report["conserved"] is False
        assert report["double_counted_ids"] == [2]
        assert report["residual_count"] == 0

    def test_empty_run_is_conserved(self):
        report = compute_conservation(selected_ids=set(), outcomes={"processed": set()})
        assert report["conserved"] is True
        assert report["selected"] == 0

    def test_residual_ids_capped_at_100(self):
        # A catastrophic run must not bloat run_summary.json with thousands
        # of ids — the list is capped, the count is exact.
        selected = set(range(1, 251))
        report = compute_conservation(selected_ids=selected, outcomes={"processed": set()})
        assert report["residual_count"] == 250
        assert len(report["residual_ids"]) == 100
        assert report["residual_ids"][0] == 1  # sorted, deterministic

    def test_outcome_id_not_in_selected_is_not_residual(self):
        # Records that appear in an outcome but were never selected (e.g. a
        # retry touching an old record) must not corrupt the residual math.
        report = compute_conservation(
            selected_ids={1, 2},
            outcomes={"processed": {1, 2, 99}},
        )
        assert report["residual_count"] == 0
        assert report["conserved"] is True
