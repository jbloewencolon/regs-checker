"""Unit tests for the LC-1 Law Card data model additions to src/db/models.py.

No DB connection needed: SQLAlchemy Table/Column metadata is populated at
class-definition time, so structural assertions (columns, types, defaults,
indexes) are testable via Base.metadata introspection alone. The migration
itself (alembic/versions/72ad4147a628_lc1a_law_card_data_model.py) was
verified live against a real Postgres 16 instance during authoring
(upgrade / downgrade / re-upgrade round trip) — see the migration's
docstring; that verification is not repeated here since this sandbox has no
DB available to the test suite, matching the project-wide test convention
(mocked sessions, no live DB in CI's regular unit-test job — only the
dedicated "Alembic migrations (fresh DB)" CI job talks to a real Postgres).
"""
from __future__ import annotations

from src.db.models import (
    Extraction,
    ExtractionFieldEdit,
    FieldEditStatus,
    LawCardState,
)


class TestFieldEditStatus:
    def test_values(self):
        assert {e.value for e in FieldEditStatus} == {
            "proposed", "applied", "reverted", "superseded", "orphaned",
        }

    def test_is_str_enum(self):
        # Must be a str subclass so it round-trips through JSON/HTTP form
        # values without extra coercion in the API layer (LC-1d).
        assert isinstance(FieldEditStatus.proposed, str)
        assert FieldEditStatus.proposed == "proposed"


class TestExtractionFieldEditTable:
    def test_table_name(self):
        assert ExtractionFieldEdit.__tablename__ == "extraction_field_edits"

    def test_required_columns_present(self):
        cols = ExtractionFieldEdit.__table__.columns
        expected = {
            "id", "extraction_id", "canonical_key", "extraction_identity",
            "field_path", "old_value", "new_value", "reason", "status",
            "validation_report", "editor", "lock_token", "created_at",
            "applied_at", "updated_at",
        }
        assert expected.issubset(set(cols.keys()))

    def test_reason_is_not_nullable(self):
        # Product requirement: every edit must carry a reason (audit trail).
        assert ExtractionFieldEdit.__table__.columns["reason"].nullable is False

    def test_editor_is_not_nullable(self):
        # D-6: identity is required on every edit even in the interim
        # free-text-only scheme.
        assert ExtractionFieldEdit.__table__.columns["editor"].nullable is False

    def test_default_status_is_proposed(self):
        col = ExtractionFieldEdit.__table__.columns["status"]
        assert col.default.arg == FieldEditStatus.proposed

    def test_extraction_fk_cascades_on_delete(self):
        fk = next(iter(ExtractionFieldEdit.__table__.columns["extraction_id"].foreign_keys))
        assert fk.ondelete == "CASCADE"

    def test_active_field_partial_unique_index_exists(self):
        index_names = {ix.name for ix in ExtractionFieldEdit.__table__.indexes}
        assert "uq_field_edits_active_field" in index_names

    def test_active_field_index_is_scoped_to_proposed_and_applied(self):
        idx = next(
            ix for ix in ExtractionFieldEdit.__table__.indexes
            if ix.name == "uq_field_edits_active_field"
        )
        assert idx.unique is True
        where_clause = str(idx.dialect_options["postgresql"]["where"])
        assert "proposed" in where_clause
        assert "applied" in where_clause


class TestLawCardStateTable:
    def test_table_name(self):
        assert LawCardState.__tablename__ == "law_card_states"

    def test_required_columns_present(self):
        cols = LawCardState.__table__.columns
        expected = {
            "id", "canonical_key", "run_id", "extraction_count",
            "edited_count", "tier_counts", "human_review_state",
            "card_cache", "updated_at",
        }
        assert expected.issubset(set(cols.keys()))

    def test_default_human_review_state_is_none(self):
        col = LawCardState.__table__.columns["human_review_state"]
        assert col.default.arg == "none"

    def test_key_run_unique_index_exists(self):
        index_names = {ix.name for ix in LawCardState.__table__.indexes}
        assert "uq_law_card_states_key_run" in index_names

    def test_card_cache_is_nullable(self):
        # NULL is the "needs (re)assembly" sentinel, not an error state.
        assert LawCardState.__table__.columns["card_cache"].nullable is True


class TestExtractionCurrentPayload:
    """Extraction.current_payload — the read path every LC-1e consumer switches to."""

    def test_falls_back_to_payload_when_no_edits(self):
        ext = Extraction()
        ext.payload = {"subject": "developer", "action": "must comply"}
        ext.effective_payload = None
        assert ext.current_payload == {"subject": "developer", "action": "must comply"}

    def test_prefers_effective_payload_when_present(self):
        ext = Extraction()
        ext.payload = {"subject": "developer"}
        ext.effective_payload = {"subject": "deployer"}  # edited
        assert ext.current_payload == {"subject": "deployer"}

    def test_original_payload_untouched_by_current_payload_read(self):
        # Reading current_payload must never mutate the base payload — the
        # G-1 fix depends on payload staying write-once.
        ext = Extraction()
        ext.payload = {"subject": "developer"}
        ext.effective_payload = {"subject": "deployer"}
        _ = ext.current_payload
        assert ext.payload == {"subject": "developer"}

    def test_extraction_has_human_review_state_column_defaulting_unedited(self):
        col = Extraction.__table__.columns["human_review_state"]
        assert col.default.arg == "unedited"

    def test_extraction_has_effective_payload_column_nullable(self):
        assert Extraction.__table__.columns["effective_payload"].nullable is True
