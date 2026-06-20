"""Add currentness fields to compliance_concepts.

Phase 1 of the product-review remediation plan: stamp each concept with the
law's lifecycle status, effective date, a human-readable amendment-status
rollup, and the date it was last grouped.  Allows a card to answer "is this
law still in force?" without a join.

Revision ID: z2a8b4c6d027
Revises: y1z7a3b5c026
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "z2a8b4c6d027"
down_revision = "y1z7a3b5c026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "compliance_concepts",
        sa.Column("law_status", sa.String(30), nullable=True),
    )
    op.add_column(
        "compliance_concepts",
        sa.Column("law_effective_date", sa.Date, nullable=True),
    )
    op.add_column(
        "compliance_concepts",
        sa.Column("amendment_status", sa.Text, nullable=True),
    )
    op.add_column(
        "compliance_concepts",
        sa.Column("as_of_date", sa.Date, nullable=True),
    )
    op.create_index(
        "ix_compliance_concept_law_status", "compliance_concepts", ["law_status"]
    )


def downgrade() -> None:
    op.drop_index("ix_compliance_concept_law_status", table_name="compliance_concepts")
    op.drop_column("compliance_concepts", "as_of_date")
    op.drop_column("compliance_concepts", "amendment_status")
    op.drop_column("compliance_concepts", "law_effective_date")
    op.drop_column("compliance_concepts", "law_status")
