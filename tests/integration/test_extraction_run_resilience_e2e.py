"""ERR-1/ERR-2: ExtractionRun finalization on every exit path, plus startup
recovery for runs a hard kill never gave a chance to finalize — real
Postgres, following this repo's established convention.

ERR-1 exercises _finalize_extraction_run directly (its four call sites in
extractor.py all funnel through it, so this is the one place that needs
thorough coverage) and run_extraction()'s thin wrapper, which must finalize
the run as failed before re-raising any exception that escapes
_run_extraction_impl — the gap the audit found: a crash used to leave
ExtractionRun.status stuck at "running" forever.

ERR-2 exercises src.api.app._recover_stale_jobs(), which ERR-1 alone can't
cover: a killed process (SIGKILL, OOM, container eviction) never runs any
Python exception handler, so the only chance to fix up the row is the next
process's startup.
"""
from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy import update as sa_update

from src.db.engine import SessionLocal
from src.db.models import (
    DocumentFamily,
    DocumentVersion,
    ExtractionAttempt,
    ExtractionRun,
    NormalizedSourceRecord,
    PipelineEvent,
    Source,
    TemporalStatus,
)
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

    def test_mutates_summary_dict_in_place_for_run_summary_json(self, db, running_run):
        """RunArchiver.finalize() writes run_summary.json (the file-based
        "extraction log") as `{**summary, ...}` — the only way it can show
        why a run ended is if _finalize_extraction_run mutates the same
        summary dict object the caller then hands to archiver.finalize()
        (call order was swapped at all 4 extractor.py call sites for this).
        """
        summary = {"total_extractions": 5, "records_processed": 3}
        extractor._finalize_extraction_run(db, running_run, "failed", "circuit_breaker", summary)
        assert summary["run_status"] == "failed"
        assert summary["termination_reason"] == "circuit_breaker"
        # Original keys survive the mutation.
        assert summary["total_extractions"] == 5
        assert summary["records_processed"] == 3

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


@pytest.fixture
def passage(db):
    unique_key = f"US-CO-ERR2TEST-{uuid.uuid4().hex[:8]}"
    source = Source(
        jurisdiction_code="CO", jurisdiction_name="Colorado",
        source_type="state_statute", connector_id="err2-test",
    )
    db.add(source)
    db.flush()
    family = DocumentFamily(
        source_id=source.id, canonical_title="ERR-2 Recovery Test Law", canonical_key=unique_key,
    )
    db.add(family)
    db.flush()
    version = DocumentVersion(
        family_id=family.id, version_label="v1",
        temporal_status=TemporalStatus.active, effective_date=date(2026, 1, 1),
    )
    db.add(version)
    db.flush()
    rec = NormalizedSourceRecord(
        document_version_id=version.id, section_path="Section 1", ordinal=0,
        text_content="A developer shall comply.", text_hash=f"err2-hash-{uuid.uuid4().hex[:8]}",
    )
    db.add(rec)
    db.flush()
    db.commit()
    return rec.id


class TestRecoverStaleExtractionRuns:
    """_recover_stale_jobs() (src/api/app.py) is the only chance a killed
    process (SIGKILL, OOM, container eviction) gets to fix up a run its own
    exception handlers never ran for — ERR-1's wrapper only covers exits
    from *this* process. Runs its own SessionLocal() internally (separate
    from the `db` fixture's session), matching production's startup-recovery
    call path, so assertions re-read via db.expire_all() after calling it.
    """

    def test_recovers_stuck_run_with_progress_from_attempts(self, db, passage):
        from src.api.app import _recover_stale_jobs

        run = ExtractionRun(run_type="extract", status="running", is_serving=False)
        db.add(run)
        db.flush()
        # Two agents succeeded on the same passage before the process died —
        # passages_processed should count the passage once (distinct), while
        # extractions_produced sums across both attempts.
        db.add(ExtractionAttempt(
            source_record_id=passage, agent_name="obligation", run_id=run.id,
            status="succeeded", extractions_produced=3,
        ))
        db.add(ExtractionAttempt(
            source_record_id=passage, agent_name="definition_actor", run_id=run.id,
            status="succeeded", extractions_produced=1,
        ))
        # A failed attempt must not inflate the "produced" count.
        db.add(ExtractionAttempt(
            source_record_id=passage, agent_name="preemption", run_id=run.id,
            status="failed", extractions_produced=0, error_message="boom",
        ))
        db.commit()
        run_id = run.id

        _recover_stale_jobs()

        db.expire_all()
        recovered = db.get(ExtractionRun, run_id)
        assert recovered.status == "interrupted"
        assert recovered.termination_reason == "crash_recovered"
        assert recovered.completed_at is not None
        assert recovered.passage_count == 1
        assert recovered.extraction_count == 4

        event = db.scalar(
            select(PipelineEvent).where(
                PipelineEvent.run_id == run_id,
                PipelineEvent.event_type == "run_interrupted",
            )
        )
        assert event is not None
        assert event.details["passages_processed"] == 1
        assert event.details["extractions_produced"] == 4
        assert event.details["recovered_at_startup"] is True

    def test_leaves_non_running_run_untouched(self, db):
        from src.api.app import _recover_stale_jobs

        run = ExtractionRun(
            run_type="extract", status="completed", termination_reason="completed",
            is_serving=False,
        )
        db.add(run)
        db.flush()
        db.commit()
        run_id = run.id

        _recover_stale_jobs()

        db.expire_all()
        untouched = db.get(ExtractionRun, run_id)
        assert untouched.status == "completed"
        assert untouched.termination_reason == "completed"

        event = db.scalar(
            select(PipelineEvent).where(
                PipelineEvent.run_id == run_id,
                PipelineEvent.event_type == "run_interrupted",
            )
        )
        assert event is None

    def test_noop_when_no_attempts_recorded(self, db):
        """A run that died before any ExtractionAttempt was even created
        (crashed during setup) must still recover cleanly with zero counts,
        not raise on the aggregate query."""
        from src.api.app import _recover_stale_jobs

        run = ExtractionRun(run_type="extract", status="running", is_serving=False)
        db.add(run)
        db.flush()
        db.commit()
        run_id = run.id

        _recover_stale_jobs()

        db.expire_all()
        recovered = db.get(ExtractionRun, run_id)
        assert recovered.status == "interrupted"
        assert recovered.termination_reason == "crash_recovered"
        assert recovered.passage_count == 0
        assert recovered.extraction_count == 0
