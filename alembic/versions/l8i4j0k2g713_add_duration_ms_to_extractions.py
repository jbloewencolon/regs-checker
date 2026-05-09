"""Add duration_ms and token columns to extractions table.

Revision ID: l8i4j0k2g713
Revises: k7h3i9j1f612
Create Date: 2026-05-09

Adds three columns to the extractions table:
  - duration_ms (INTEGER, nullable): wall-clock time of the LLM call in
    milliseconds, recorded via time.perf_counter() around each agent call.
    Enables per-agent latency analysis and bottleneck identification.
  - input_tokens (INTEGER, default 0): prompt token count for this extraction.
  - output_tokens (INTEGER, default 0): completion token count.

input_tokens/output_tokens were already tracked in-memory and logged to
structlog but not persisted to the DB — now they are.  Existing rows get
default 0 for all three columns.
"""

from alembic import op
import sqlalchemy as sa

revision = "l8i4j0k2g713"
down_revision = "k7h3i9j1f612"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extractions",
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "extractions",
        sa.Column("input_tokens", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "extractions",
        sa.Column("output_tokens", sa.Integer(), nullable=True, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("extractions", "duration_ms")
    op.drop_column("extractions", "input_tokens")
    op.drop_column("extractions", "output_tokens")
