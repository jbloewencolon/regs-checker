"""Add preemption_signal extraction type.

Revision ID: j6g2h8i0e511
Revises: i5f1g7h9d410
Create Date: 2026-04-02
"""

from alembic import op

revision = "j6g2h8i0e511"
down_revision = "i5f1g7h9d410"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE extractiontype ADD VALUE IF NOT EXISTS 'preemption_signal'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; no-op
    pass
