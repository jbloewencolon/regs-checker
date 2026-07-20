"""LC-4a-lite: DocumentFamily.excluded_from_extraction must actually keep
excluded laws' passages out of run_triage()/run_extraction() — the whole
point of the checkbox is skipping re-work, so this proves the pipeline
functions honor it, not just that the flag persists.

run_triage() is exercised end-to-end against real Postgres with only its
one genuinely-external dependency (the LLM triage call) mocked — this is
the same "real data flow, mock LLM responses" convention documented in
test_pipeline_e2e.py's own module docstring. run_extraction()'s query is
tested directly via the shared _excluded_document_version_ids() helper
(the same helper both functions use), since exercising the full extraction
loop would require mocking the entire agent battery for no added
assurance beyond what the shared-helper test already gives.
"""
from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.db.engine import SessionLocal
from src.db.models import (
    DocumentFamily,
    DocumentVersion,
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
    SectionTriageResult,
    Source,
    TemporalStatus,
    TriageDecision,
)
from src.ingestion.extractor import _excluded_document_version_ids, run_triage


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _make_family_with_passage(db, *, jurisdiction, title_suffix, excluded):
    unique_key = f"US-{jurisdiction}-EXCLTEST-{uuid.uuid4().hex[:8]}"
    source = Source(
        jurisdiction_code=jurisdiction, jurisdiction_name=jurisdiction,
        source_type="state_statute", connector_id=f"excl-test-{uuid.uuid4().hex[:6]}",
    )
    db.add(source)
    db.flush()
    family = DocumentFamily(
        source_id=source.id, canonical_title=f"Exclusion Filter Test {title_suffix}",
        canonical_key=unique_key, excluded_from_extraction=excluded,
    )
    db.add(family)
    db.flush()
    version = DocumentVersion(
        family_id=family.id, version_label="v1", temporal_status=TemporalStatus.active,
        effective_date=date(2026, 1, 1),
    )
    db.add(version)
    db.flush()
    db.add(IngestionJob(document_version_id=version.id, status=IngestionStatus.completed))
    # Unique text per invocation (not just per fixture call within one test):
    # this repo's shared scratch DB accumulates committed rows across
    # repeated test runs within a session (documented elsewhere in this
    # session's test suite), and run_triage() dedupes passages sharing an
    # identical (text, ai_scope, key_requirements) key — an unchanging
    # boilerplate string would let a leftover untriaged row from an earlier
    # run of THIS test dedupe-merge with the current run's, corrupting
    # summary["total"] without any real exclusion-filter bug involved.
    passage = NormalizedSourceRecord(
        document_version_id=version.id, section_path="Section 1", ordinal=0,
        text_content=(
            f"A developer shall comply with reasonable care obligations under "
            f"this act. Reference {unique_key}. " * 3
        ),
        text_hash=f"h-{uuid.uuid4().hex[:8]}",
    )
    db.add(passage)
    db.flush()
    db.commit()
    return family, version, passage


class TestExcludedDocumentVersionIdsHelper:
    def test_helper_returns_only_excluded_families_versions(self, db):
        _excl_family, excl_version, _p1 = _make_family_with_passage(
            db, jurisdiction="CO", title_suffix="Excluded", excluded=True,
        )
        _ctrl_family, ctrl_version, _p2 = _make_family_with_passage(
            db, jurisdiction="CO", title_suffix="Control", excluded=False,
        )
        excluded_ids = set(db.scalars(_excluded_document_version_ids()).all())
        assert excl_version.id in excluded_ids
        assert ctrl_version.id not in excluded_ids

    def test_run_extraction_style_query_skips_excluded_passages(self, db):
        """Mirrors the exact WHERE-clause shape run_extraction() uses
        (NormalizedSourceRecord.document_version_id.notin_(...)) against a
        real triaged-relevant passage set, without needing to run the full
        agent battery."""
        excl_family, excl_version, excl_passage = _make_family_with_passage(
            db, jurisdiction="NY", title_suffix="ExtractExcluded", excluded=True,
        )
        _ctrl_family, ctrl_version, ctrl_passage = _make_family_with_passage(
            db, jurisdiction="NY", title_suffix="ExtractControl", excluded=False,
        )
        for passage in (excl_passage, ctrl_passage):
            db.add(SectionTriageResult(
                source_record_id=passage.id, decision=TriageDecision.relevant,
                method="keyword",
            ))
        db.commit()

        triaged_relevant_ids = (
            select(SectionTriageResult.source_record_id)
            .where(SectionTriageResult.decision.in_(
                [TriageDecision.relevant, TriageDecision.uncertain],
            ))
        )
        query = (
            select(NormalizedSourceRecord)
            .where(
                NormalizedSourceRecord.id.in_(triaged_relevant_ids),
                NormalizedSourceRecord.document_version_id.notin_(_excluded_document_version_ids()),
            )
            .distinct()
        )
        result_ids = {r.id for r in db.scalars(query).all()}
        assert ctrl_passage.id in result_ids
        assert excl_passage.id not in result_ids


class TestRunTriageRespectsExclusion:
    def test_excluded_laws_passage_never_triaged_control_law_is(self, db):
        excl_family, excl_version, excl_passage = _make_family_with_passage(
            db, jurisdiction="TX", title_suffix="TriageExcluded", excluded=True,
        )
        _ctrl_family, ctrl_version, ctrl_passage = _make_family_with_passage(
            db, jurisdiction="TX", title_suffix="TriageControl", excluded=False,
        )

        from src.agents.section_triage import TriageResult

        fake_result = TriageResult(
            decision="relevant", method="keyword", confidence=1.0,
            matched_keywords=["artificial intelligence"],
        )
        with patch(
            "src.agents.section_triage.triage_passage", return_value=fake_result,
        ):
            run_triage(db)
        db.commit()

        # Assert on these two specific rows, not summary["total"]: this
        # repo's shared scratch DB can carry other untriaged rows left by
        # earlier test runs (documented pollution pattern elsewhere in this
        # suite), so an aggregate count is not a reliable signal here — the
        # real question is only "were these two specific rows handled
        # correctly."
        excl_triaged = db.scalar(
            select(SectionTriageResult)
            .where(SectionTriageResult.source_record_id == excl_passage.id)
        )
        ctrl_triaged = db.scalar(
            select(SectionTriageResult)
            .where(SectionTriageResult.source_record_id == ctrl_passage.id)
        )
        assert excl_triaged is None, "excluded law's passage must never be triaged"
        assert ctrl_triaged is not None, "control law's passage should be triaged normally"
