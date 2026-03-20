"""Add missing enum values for temporalstatus and legaleventtype

The models added pre-enactment and terminal statuses (introduced, pending,
passed_one_chamber, vetoed, dead, withdrawn) and new legal event types
(introduction, passage_one_chamber, veto, death, withdrawal, status_check)
that were not in the initial migration.

Revision ID: d1a5f3e7b904
Revises: c9e2a4d5b703
Create Date: 2026-03-20 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers
revision: str = "d1a5f3e7b904"
down_revision: Union[str, None] = "c9e2a4d5b703"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add pre-enactment and terminal statuses to temporalstatus enum
    for value in [
        "introduced",
        "pending",
        "passed_one_chamber",
        "vetoed",
        "dead",
        "withdrawn",
    ]:
        op.execute(
            f"ALTER TYPE temporalstatus ADD VALUE IF NOT EXISTS '{value}'"
        )

    # Add new legal event types
    for value in [
        "introduction",
        "passage_one_chamber",
        "veto",
        "death",
        "withdrawal",
        "status_check",
    ]:
        op.execute(
            f"ALTER TYPE legaleventtype ADD VALUE IF NOT EXISTS '{value}'"
        )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values.
    pass
