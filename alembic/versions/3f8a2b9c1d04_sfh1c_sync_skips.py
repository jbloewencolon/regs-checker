"""SFH-1c (audit SF-03): create sync_skips table.

The id-cursor sync leg (sync_extractions.py) advances its MAX(id) watermark
past extractions whose law has no law_document_bridge entry, making them
permanently unreachable by that leg — the skip count was printed to stdout
and lost, the specific extraction ids recorded nowhere. This table persists
every skip so `--resync-skips` can replay them after the bridge is
backfilled; resolved_at is stamped when a replay succeeds.

Revision ID: 3f8a2b9c1d04
Revises: 25cffe678fbc
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3f8a2b9c1d04"
down_revision = "25cffe678fbc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_skips",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("extraction_id", sa.Integer, nullable=False),
        sa.Column("doc_family_id", sa.Integer, nullable=True),
        sa.Column("reason", sa.String(50), nullable=False, server_default="no_bridge"),
        sa.Column(
            "destination",
            sa.String(50),
            nullable=False,
            server_default="policy_navigator",
        ),
        sa.Column("skipped_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
    )
    op.create_index(
        "uq_sync_skips_extraction_dest",
        "sync_skips",
        ["extraction_id", "destination"],
        unique=True,
    )
    op.create_index("ix_sync_skips_unresolved", "sync_skips", ["resolved_at"])


def downgrade() -> None:
    op.drop_index("ix_sync_skips_unresolved", table_name="sync_skips")
    op.drop_index("uq_sync_skips_extraction_dest", table_name="sync_skips")
    op.drop_table("sync_skips")
