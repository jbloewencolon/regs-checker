"""Add model_id to pipeline_events for provider/backend attribution.

Revision ID: x0y6z2a4b025
Revises: w9x5y1z3a024
Create Date: 2026-06-14

Lets the dashboard attribute pipeline events (especially agent_error events)
to the backend that produced them — e.g. "openai-gpt-oss-120b-nvidia" vs
"google-gemma-4-26b-a4b-local" — so mixed-provider runs are diagnosable.
Nullable: existing rows and any event whose provider lookup fails stay NULL.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "x0y6z2a4b025"
down_revision = "w9x5y1z3a024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_events",
        sa.Column("model_id", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipeline_events", "model_id")
