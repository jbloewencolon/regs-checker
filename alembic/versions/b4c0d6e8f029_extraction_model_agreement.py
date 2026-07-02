"""Add model_agreement_count to extractions.

Phase 4.2 of the product-review remediation plan: when two models independently
produce the same payload_hash for the same (source_record, extraction_type),
the second result is deduplicated by the uq_extractions_dedup index but
previously discarded silently.  This column records how many additional models
agreed with the stored extraction so the signal can weight future confidence
recomputes.

Revision ID: b4c0d6e8f029
Revises: b4c0d6e8f030

P1-1: down_revision updated from a3b9c5d7e028 to b4c0d6e8f030 — the
concept_actor_role migration was re-keyed off that ID to resolve a
revision collision (see b4c0d6e8f030_concept_actor_role.py). This
migration's own content is unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4c0d6e8f029"
down_revision = "b4c0d6e8f030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extractions",
        sa.Column(
            "model_agreement_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("extractions", "model_agreement_count")
