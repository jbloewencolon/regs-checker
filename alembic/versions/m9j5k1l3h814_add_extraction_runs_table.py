"""Add extraction_runs table and run_id FKs for Phase 1b run versioning.

Revision ID: m9j5k1l3h814
Revises: l8i4j0k2g713
Create Date: 2026-06-08

Adds the extraction_runs table (one row per logical extraction run) and
nullable run_id FK columns to extractions and bill_level_extractions.

Every new extraction run records its git SHA, prompt versions, and model
config so extractions can be traced back to the exact code that produced
them.  is_serving=True marks the run whose data powers live queries.

run_id is nullable on both FK columns so existing rows (before this
migration) remain valid and don't require a backfill.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "m9j5k1l3h814"
down_revision = "l8i4j0k2g713"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_type", sa.String(50), nullable=False, server_default="extract"),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("is_serving", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("git_sha", sa.String(40), nullable=True),
        sa.Column("model_config", JSONB(), nullable=True),
        sa.Column("prompt_versions", JSONB(), nullable=True),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=True),
        sa.Column("law_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("passage_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("extraction_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("summary", JSONB(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_extraction_runs_is_serving",
        "extraction_runs",
        ["is_serving"],
    )

    op.add_column(
        "extractions",
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("extraction_runs.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_extractions_run_id", "extractions", ["run_id"])

    op.add_column(
        "bill_level_extractions",
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("extraction_runs.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_bill_level_extractions_run_id", "bill_level_extractions", ["run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_bill_level_extractions_run_id", "bill_level_extractions")
    op.drop_column("bill_level_extractions", "run_id")
    op.drop_index("ix_extractions_run_id", "extractions")
    op.drop_column("extractions", "run_id")
    op.drop_index("ix_extraction_runs_is_serving", "extraction_runs")
    op.drop_table("extraction_runs")
