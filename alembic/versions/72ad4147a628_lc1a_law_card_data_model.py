"""LC-1a: Law Card data model — extraction_field_edits, law_card_states,
extractions.effective_payload / human_review_state.

Fixes the pre-existing G-1 destructive-edit defect (POST /api/review/{id}/edit
mutated Extraction.payload in place, destroying the model's original output on
first edit). Extraction.payload becomes write-once after this migration;
ExtractionFieldEdit rows record proposed/applied/reverted human corrections,
and their applied state is materialized onto the new effective_payload column
(NULL = no edits, read payload unchanged). See docs/law_card_dashboard_plan.md
and docs/law_card_decisions.md (D-3, D-4, D-5) for the full design.

Revision ID: 72ad4147a628
Revises: 4a9b3c8d2e15
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "72ad4147a628"
down_revision = "4a9b3c8d2e15"
branch_labels = None
depends_on = None

_FIELD_EDIT_STATUS_VALUES = ("proposed", "applied", "reverted", "superseded", "orphaned")


def upgrade() -> None:
    # --- extractions: edit-overlay + human review state ---
    op.add_column(
        "extractions",
        sa.Column("effective_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "extractions",
        sa.Column(
            "human_review_state", sa.String(20), nullable=False,
            server_default="unedited",
        ),
    )

    # --- extraction_field_edits ---
    # Create the enum type explicitly first (checkfirst=True), then reference
    # it in create_table with create_type=False — letting create_table also
    # try to create it double-creates within the same DDL emission and fails
    # with DuplicateObject (SQLAlchemy postgres-ENUM gotcha; verified against
    # a live Postgres 16 instance while authoring this migration).
    field_edit_status = postgresql.ENUM(
        *_FIELD_EDIT_STATUS_VALUES, name="fieldeditstatus",
    )
    field_edit_status.create(op.get_bind(), checkfirst=True)
    field_edit_status_col = postgresql.ENUM(
        *_FIELD_EDIT_STATUS_VALUES, name="fieldeditstatus", create_type=False,
    )

    op.create_table(
        "extraction_field_edits",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "extraction_id", sa.Integer,
            sa.ForeignKey("extractions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("canonical_key", sa.String(200), nullable=False),
        sa.Column("extraction_identity", sa.String(120), nullable=False),
        sa.Column("field_path", sa.String(200), nullable=False),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "status", field_edit_status_col, nullable=False, server_default="proposed",
        ),
        sa.Column(
            "validation_report", postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}", nullable=True,
        ),
        sa.Column("editor", sa.String(200), nullable=False),
        sa.Column("lock_token", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("applied_at", sa.DateTime, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime, server_default=sa.func.now(),
            onupdate=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_extraction_field_edits_extraction_id",
        "extraction_field_edits", ["extraction_id"],
    )
    op.create_index(
        "ix_field_edits_extraction_status",
        "extraction_field_edits", ["extraction_id", "status"],
    )
    op.create_index(
        "ix_field_edits_canonical_key", "extraction_field_edits", ["canonical_key"],
    )
    op.create_index(
        "uq_field_edits_active_field",
        "extraction_field_edits", ["extraction_id", "field_path"],
        unique=True,
        postgresql_where=sa.text("status IN ('proposed', 'applied')"),
    )

    # --- law_card_states ---
    op.create_table(
        "law_card_states",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("canonical_key", sa.String(200), nullable=False),
        sa.Column(
            "run_id", sa.Integer, sa.ForeignKey("extraction_runs.id"), nullable=True,
        ),
        sa.Column("extraction_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("edited_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "tier_counts", postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}", nullable=True,
        ),
        sa.Column(
            "human_review_state", sa.String(20), nullable=False, server_default="none",
        ),
        sa.Column("card_cache", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime, server_default=sa.func.now(),
            onupdate=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_law_card_states_canonical_key", "law_card_states", ["canonical_key"],
    )
    op.create_index(
        "ix_law_card_states_run_id", "law_card_states", ["run_id"],
    )
    op.create_index(
        "uq_law_card_states_key_run", "law_card_states", ["canonical_key", "run_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("law_card_states")
    op.drop_table("extraction_field_edits")
    postgresql.ENUM(name="fieldeditstatus").drop(op.get_bind(), checkfirst=True)
    op.drop_column("extractions", "human_review_state")
    op.drop_column("extractions", "effective_payload")
