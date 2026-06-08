"""Add vocab_review_queue table (B4 normalization pass infrastructure).

Revision ID: n0k6l2m4i915
Revises: m9j5k1l3h814
Create Date: 2026-06-08

Tracks raw extraction field values that did not match any canonical code
in the ratified vocabulary artifacts.  Populated by vocab_loader.normalize()
via flush_unrecognized(); consumed by the RPR/LKA review workflow.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "n0k6l2m4i915"
down_revision = "m9j5k1l3h814"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vocab_review_queue",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dimension", sa.String(50), nullable=False),
        sa.Column("raw_term", sa.String(500), nullable=False),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column(
            "extraction_id",
            sa.Integer(),
            sa.ForeignKey("extractions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("law_id", sa.Integer(), nullable=True),
        sa.Column("provisional_code", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolution", sa.String(50), nullable=True),
    )
    op.create_index(
        "ix_vocab_review_dimension_term",
        "vocab_review_queue",
        ["dimension", "raw_term"],
    )
    op.create_index(
        "ix_vocab_review_unresolved",
        "vocab_review_queue",
        ["resolved_at"],
    )
    op.create_index(
        "ix_vocab_review_extraction",
        "vocab_review_queue",
        ["extraction_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_vocab_review_extraction", "vocab_review_queue")
    op.drop_index("ix_vocab_review_unresolved", "vocab_review_queue")
    op.drop_index("ix_vocab_review_dimension_term", "vocab_review_queue")
    op.drop_table("vocab_review_queue")
