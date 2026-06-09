"""RR6a: durable pipeline_events table.

Revision ID: t6u2v8w0x021
Revises: s5t1u7v9w020
Create Date: 2026-06-09

Replaces the in-memory ring buffer in ExtractionMonitor with a DB-persisted
event table so run history survives server restarts.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "t6u2v8w0x021"
down_revision = "s5t1u7v9w020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("extraction_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_record_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=True),
        sa.Column("extraction_count", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("confidence_tier", sa.String(1), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_pipeline_events_run_type", "pipeline_events", ["run_id", "event_type"])
    op.create_index("ix_pipeline_events_created_at", "pipeline_events", ["created_at"])
    op.create_index("ix_pipeline_events_run_id", "pipeline_events", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_events_run_id", table_name="pipeline_events")
    op.drop_index("ix_pipeline_events_created_at", table_name="pipeline_events")
    op.drop_index("ix_pipeline_events_run_type", table_name="pipeline_events")
    op.drop_table("pipeline_events")
