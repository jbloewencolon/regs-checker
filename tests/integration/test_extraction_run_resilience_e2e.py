"""ERR-1: ExtractionRun finalization on every exit path — real Postgres,
following this repo's established convention. Exercises _finalize_extraction_run
directly (its four call sites in extractor.py all funnel through it, so this
is the one place that needs thorough coverage) and run_extraction()'s thin
wrapper, which must finalize the run as failed before re-raising any
exception that escapes _run_extraction_impl — the gap the audit found:
a crash used to leave ExtractionRun.status stuck at "running" forever.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import update as sa_update

from src.db.engine import SessionLocal
from src.db.models import ExtractionRun
from src.ingestion import extractor


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def running_run(db):
    run = ExtractionRun(run_type="extract", status="running", is_serving=False)
    db.add(run)
    db.flush()
    db.commit()
    return run.id


class TestFinalizeExtractionRun:
    def test_completed_marks_serving_and_clears_termination_reason(self, db, running_run):
        extractor._finalize_extraction_run(
            db, running_run, "completed", "completed",
            {"total_extractions": 5, "records_processed": 3},
        )
        db.expire_all()
        run = db.get(ExtractionRun, running_run)
        assert run.status == "completed"
        assert run.termination_reason == "completed"
        assert run.is_serving is True
        assert run.completed_at is not None
        assert run.extraction_count == 5
        assert run.passage_count == 3

    def test_completed_demotes_previous_serving_run(self, db, running_run):
        # This shared scratch DB is never reset between test runs, so a prior
        # run may have already left an is_serving=True row behind; the DB's
        # partial unique index (uq_extraction_runs_serving) allows only one,
        # so this test's own setup must not assume a clean slate.
        db.execute(sa_update(ExtractionRun).where(ExtractionRun.is_serving.is_(True)).values(is_serving=False))
        db.commit()

        other = ExtractionRun(run_type="extract", status="completed", is_serving=True)
        db.add(other)
        db.flush()
        db.commit()

        extractor._finalize_extraction_run(db, running_run, "completed", "completed", {})
        db.expire_all()
        assert db.get(ExtractionRun, running_run).is_serving is True
        assert db.get(ExtractionRun, other.id).is_serving is False

    def test_cancelled_does_not_become_serving(self, db, running_run):
        extractor._finalize_extraction_run(
            db, running_run, "cancelled", "cancelled", {"records_processed": 2},
        )
        db.expire_all()
        run = db.get(ExtractionRun, running_run)
        assert run.status == "cancelled"
        assert run.termination_reason == "cancelled"
        assert run.is_serving is False

    def test_failed_does_not_become_serving(self, db, running_run):
        extractor._finalize_extraction_run(db, running_run, "failed", "circuit_breaker", {})
        db.expire_all()
        run = db.get(ExtractionRun, running_run)
        assert run.status == "failed"
        assert run.termination_reason == "circuit_breaker"
        assert run.is_serving is False

    def test_none_run_id_is_a_safe_noop(self, db):
        extractor._finalize_extraction_run(db, None, "completed", "completed", {})  # must not raise

    def test_unknown_run_id_is_a_safe_noop(self, db):
        extractor._finalize_extraction_run(db, 999999999, "completed", "completed", {})  # must not raise

    def test_callable_twice_overwrites_with_latest_state(self, db, running_run):
        extractor._finalize_extraction_run(db, running_run, "failed", "exception", {})
        extractor._finalize_extraction_run(db, running_run, "completed", "completed", {})
        db.expire_all()
        run = db.get(ExtractionRun, running_run)
        assert run.status == "completed"
        assert run.termination_reason == "completed"


class TestRunExtractionWrapperFinalizesOnException:
    def test_unhandled_exception_marks_run_failed_and_reraises(self, db):
        created_run_ids: list[int] = []

        def _fake_impl(db, limit=None, on_progress=None, batch_mode=False,
                        purge=False, _run_id_sink=None):
            run = ExtractionRun(run_type="extract", status="running", is_serving=False)
            db.add(run)
            db.flush()
            db.commit()
            created_run_ids.append(run.id)
            if _run_id_sink is not None:
                _run_id_sink["run_id"] = run.id
            raise RuntimeError("simulated crash mid-run")

        with patch.object(extractor, "_run_extraction_impl", side_effect=_fake_impl):
            with pytest.raises(RuntimeError, match="simulated crash mid-run"):
                extractor.run_extraction(db)

        assert created_run_ids, "fake impl should have created a run before raising"
        db.expire_all()
        run = db.get(ExtractionRun, created_run_ids[0])
        assert run.status == "failed"
        assert run.termination_reason == "exception"
        assert run.is_serving is False
        assert run.completed_at is not None

    def test_exception_before_run_created_still_propagates_cleanly(self, db):
        """If the crash happens before ExtractionRun even exists (_run_id_sink
        never populated), the wrapper must not itself raise a second,
        confusing error — just propagate the original one."""
        def _fake_impl(db, limit=None, on_progress=None, batch_mode=False,
                        purge=False, _run_id_sink=None):
            raise RuntimeError("crashed before run row existed")

        with patch.object(extractor, "_run_extraction_impl", side_effect=_fake_impl):
            with pytest.raises(RuntimeError, match="crashed before run row existed"):
                extractor.run_extraction(db)

    def test_successful_run_returns_summary_unchanged(self, db):
        sentinel_summary = {"total_extractions": 7, "records_processed": 4}

        def _fake_impl(db, limit=None, on_progress=None, batch_mode=False,
                        purge=False, _run_id_sink=None):
            return sentinel_summary

        with patch.object(extractor, "_run_extraction_impl", side_effect=_fake_impl):
            result = extractor.run_extraction(db)

        assert result is sentinel_summary
