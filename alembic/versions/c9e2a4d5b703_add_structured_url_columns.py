"""Add structured URL columns to document_families

Revision ID: c9e2a4d5b703
Revises: b7d4e1f3a502
Create Date: 2026-03-19 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "c9e2a4d5b703"
down_revision: Union[str, None] = "b7d4e1f3a502"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_families",
        sa.Column("primary_source_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "document_families",
        sa.Column("orrick_reference_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "document_families",
        sa.Column("iapp_reference_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_families", "iapp_reference_url")
    op.drop_column("document_families", "orrick_reference_url")
    op.drop_column("document_families", "primary_source_url")
