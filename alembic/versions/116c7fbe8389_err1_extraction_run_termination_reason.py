"""ERR-1: ExtractionRun.termination_reason — why a run ended.

Fixes an audit finding (2026-07-20): ExtractionRun.status only ever
transitioned to "completed" in one place in the whole codebase, on a code
path unreachable from a graceful cancel, a circuit-breaker abort, or any
unhandled exception — every one of those left the row stuck at "running"
forever with no record of what actually happened. This column (plus the
new "cancelled"/"failed"/"interrupted"/"no_work" values now written to the
existing `status` column — a plain String, no enum/CHECK constraint to
migrate) lets the extraction log distinguish "finished cleanly" from "died
partway through and here's why."

Revision ID: 116c7fbe8389
Revises: 195d64f44ff2
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "116c7fbe8389"
down_revision = "195d64f44ff2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_runs",
        sa.Column("termination_reason", sa.String(30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_runs", "termination_reason")
