"""Add rights_protection and compliance_mechanism extraction types

New extraction agents for individual rights/protections (DeepSeek) and
procedural compliance mechanisms (GPT).

Revision ID: f2c8d4e6a107
Revises: e4b7c2f1d305
Create Date: 2026-03-21 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers
revision: str = "f2c8d4e6a107"
down_revision: Union[str, None] = "e4b7c2f1d305"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for value in ["rights_protection", "compliance_mechanism"]:
        op.execute(
            f"ALTER TYPE extractiontype ADD VALUE IF NOT EXISTS '{value}'"
        )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values.
    pass
