"""RR6e: sync_cursors table for durable per-table sync position.

Revision ID: u7v3w9x1y022
Revises: t6u2v8w0x021
Create Date: 2026-06-09

Enables ID-window pagination in sync_to_supabase.py so incremental syncs
skip already-synced rows rather than full-table re-POSTing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "u7v3w9x1y022"
down_revision = "t6u2v8w0x021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_cursors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("table_name", sa.String(100), nullable=False),
        sa.Column("destination", sa.String(50), nullable=False, server_default="supabase"),
        sa.Column("last_synced_id", sa.Integer(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("rows_synced", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_sync_cursor_table_dest",
        "sync_cursors",
        ["table_name", "destination"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_sync_cursor_table_dest", table_name="sync_cursors")
    op.drop_table("sync_cursors")
