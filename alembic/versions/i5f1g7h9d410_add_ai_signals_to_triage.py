"""Add ai_signals column to section_triage_results.

Stores the LLM's explanation of what specific words, phrases, or concepts
in the passage suggest AI relevance, helping reviewers assess uncertain
triage decisions.

Revision ID: i5f1g7h9d410
Revises: h4e0f6g8c309
Create Date: 2026-03-23 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "i5f1g7h9d410"
down_revision = "h4e0f6g8c309"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "section_triage_results",
        sa.Column("ai_signals", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("section_triage_results", "ai_signals")
