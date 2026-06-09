"""Add extraction_attempts table for per-agent run-state tracking (RR1c).

Revision ID: r4s0t6u8v019
Revises: q3r9s5t7u018
Create Date: 2026-06-09

Phase RR1c: tracks the full lifecycle of each agent call (running →
succeeded | failed | skipped) so interrupted runs can be detected and
resumed without re-running agents that already completed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r4s0t6u8v019"
down_revision = "q3r9s5t7u018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "source_record_id",
            sa.Integer(),
            sa.ForeignKey("normalized_source_records.id"),
            nullable=False,
        ),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("extraction_runs.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("extractions_produced", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_extraction_attempts_record_id",
        "extraction_attempts",
        ["source_record_id"],
    )
    op.create_index(
        "ix_extraction_attempts_record_agent",
        "extraction_attempts",
        ["source_record_id", "agent_name"],
    )
    op.create_index(
        "ix_extraction_attempts_run_status",
        "extraction_attempts",
        ["run_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_attempts_run_status", table_name="extraction_attempts")
    op.drop_index("ix_extraction_attempts_record_agent", table_name="extraction_attempts")
    op.drop_index("ix_extraction_attempts_record_id", table_name="extraction_attempts")
    op.drop_table("extraction_attempts")
