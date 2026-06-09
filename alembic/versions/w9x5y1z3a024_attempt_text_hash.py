"""Add input_text_hash to extraction_attempts for attempt-state deduplication.

Revision ID: w9x5y1z3a024
Revises: v8w4x0y2z023
Create Date: 2026-06-09

Enables the attempt-state dedup model (replacing existing_hashes set):
- input_text_hash stores sha256[:24] of the passage text at attempt time
- Allows cross-run skip when (source_record_id, agent_name, text_hash) has
  a prior succeeded attempt, even when the agent produced 0 extractions
- Partial index on succeeded rows accelerates the preload query
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "w9x5y1z3a024"
down_revision = "v8w4x0y2z023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_attempts",
        sa.Column("input_text_hash", sa.String(24), nullable=True),
    )
    op.create_index(
        "ix_extraction_attempts_succeeded",
        "extraction_attempts",
        ["source_record_id", "agent_name", "input_text_hash"],
        postgresql_where=sa.text("status = 'succeeded'"),
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_attempts_succeeded", table_name="extraction_attempts")
    op.drop_column("extraction_attempts", "input_text_hash")
