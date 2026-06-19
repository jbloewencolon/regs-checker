"""Add run_id to failed_extraction_attempts for per-run grouping.

Revision ID: y1z7a3b5c026
Revises: x0y6z2a4b025
Create Date: 2026-06-19

Links each failed attempt to the ExtractionRun that produced it so
retry runs can be grouped by originating run and the dashboard can
show per-run failure stats. Nullable: rows from before this migration
and failures where run tracking is unavailable stay NULL.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "y1z7a3b5c026"
down_revision = "x0y6z2a4b025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "failed_extraction_attempts",
        sa.Column(
            "run_id",
            sa.Integer,
            sa.ForeignKey("extraction_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_failed_attempts_run_id",
        "failed_extraction_attempts",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_failed_attempts_run_id", table_name="failed_extraction_attempts")
    op.drop_column("failed_extraction_attempts", "run_id")
