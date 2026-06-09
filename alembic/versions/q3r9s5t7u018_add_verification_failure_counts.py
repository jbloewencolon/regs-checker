"""Add cv_passages_failed / gd_passages_failed to verification_run_summaries.

Revision ID: q3r9s5t7u018
Revises: p2q8r4s6t017
Create Date: 2026-06-09

Phase 0 (trust fix): cross-validation and gap detection now fail closed. A
failed verification call is tracked separately instead of being folded in as a
neutral accuracy / clean "no gaps" result. These two counters make the failure
count visible in the per-document run summary so a silently broken verification
layer can no longer masquerade as a pass.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "q3r9s5t7u018"
down_revision = "p2q8r4s6t017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "verification_run_summaries",
        sa.Column(
            "cv_passages_failed",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "verification_run_summaries",
        sa.Column(
            "gd_passages_failed",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("verification_run_summaries", "gd_passages_failed")
    op.drop_column("verification_run_summaries", "cv_passages_failed")
