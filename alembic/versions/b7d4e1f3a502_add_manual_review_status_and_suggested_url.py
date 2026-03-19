"""Add requires_manual_review status and ai_suggested_url to ingestion_jobs

Revision ID: b7d4e1f3a502
Revises: a3f7b2c8d901
Create Date: 2026-03-19 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "b7d4e1f3a502"
down_revision: Union[str, None] = "a3f7b2c8d901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the new enum value to ingestion_status
    op.execute(
        "ALTER TYPE ingestionstatus ADD VALUE IF NOT EXISTS 'requires_manual_review'"
    )

    # Add ai_suggested_url column to ingestion_jobs
    op.add_column(
        "ingestion_jobs",
        sa.Column("ai_suggested_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "ai_suggested_url")
    # Note: PostgreSQL does not support removing enum values.
    # The 'requires_manual_review' value will remain in the enum type.
