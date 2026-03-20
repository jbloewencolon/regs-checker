"""Add prompt_hash and template_version columns to extractions

Revision ID: e4b7c2f1d305
Revises: c9e2a4d5b703
Create Date: 2026-03-20 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "e4b7c2f1d305"
down_revision: Union[str, None] = "c9e2a4d5b703"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "extractions",
        sa.Column("prompt_hash", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "extractions",
        sa.Column("template_version", sa.String(length=50), nullable=True),
    )
    # Composite index for finding stale extractions by model+prompt
    op.create_index(
        "ix_extractions_model_prompt",
        "extractions",
        ["model_id", "prompt_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_extractions_model_prompt", table_name="extractions")
    op.drop_column("extractions", "template_version")
    op.drop_column("extractions", "prompt_hash")
