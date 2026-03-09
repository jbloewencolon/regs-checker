"""add materialized views

Revision ID: a3f7b2c8d901
Revises: 14c51c9b2e02
Create Date: 2026-03-09 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

from src.db.views import ALL_VIEW_DEFINITIONS

# revision identifiers
revision: str = 'a3f7b2c8d901'
down_revision: Union[str, None] = '14c51c9b2e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create materialized views, trigger function, and trigger
    for sql in ALL_VIEW_DEFINITIONS:
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_refresh_on_review ON review_actions")
    op.execute("DROP FUNCTION IF EXISTS refresh_served_views()")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS served_matrix_cells")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS served_obligations")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_active_obligations")
