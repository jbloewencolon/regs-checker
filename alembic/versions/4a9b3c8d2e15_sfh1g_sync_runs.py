"""SFH-1g (audit SF-09): create sync_runs table.

Sync/rollup outcomes existed only as console prints — under cron, a failed
or empty sync leaves no durable record and the product database quietly
stops receiving updates. One row per leg per CLI invocation; sync_monitor
reads the tail for freshness alerting.

Revision ID: 4a9b3c8d2e15
Revises: 3f8a2b9c1d04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "4a9b3c8d2e15"
down_revision = "3f8a2b9c1d04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("leg", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("rows_synced", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rows_skipped", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_sync_runs_leg_started", "sync_runs", ["leg", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_sync_runs_leg_started", table_name="sync_runs")
    op.drop_table("sync_runs")
