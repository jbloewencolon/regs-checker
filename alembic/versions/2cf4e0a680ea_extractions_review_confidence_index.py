"""Add composite index on extractions(review_status, confidence_tier).

Revision ID: 2cf4e0a680ea
Revises: b4c0d6e8f029

P1-6: the dashboard review queue and every publish-gating query filter on
this exact pair of columns together (see src/api/routes/review_routes.py,
dashboard.py), but only a single-column-adjacent index
(extraction_type, review_status) existed. Also mirrored in
src.db.models.Extraction.__table_args__.
"""

from __future__ import annotations

from alembic import op

revision = "2cf4e0a680ea"
down_revision = "b4c0d6e8f029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_extractions_review_status_confidence_tier",
        "extractions",
        ["review_status", "confidence_tier"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extractions_review_status_confidence_tier", table_name="extractions"
    )
