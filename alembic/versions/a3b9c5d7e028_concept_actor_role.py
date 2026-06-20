"""Add actor_role to compliance_concepts.

Phase 2 of the product-review remediation plan: classify each concept's
regulated_actor_family into a three-value actor_role
("government" / "regulated_entity" / "individual") at grouping time.
Enables actor-role facet filtering without a vocab-table join.

Revision ID: a3b9c5d7e028
Revises: z2a8b4c6d027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3b9c5d7e028"
down_revision = "z2a8b4c6d027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "compliance_concepts",
        sa.Column("actor_role", sa.String(30), nullable=True),
    )
    op.create_index(
        "ix_compliance_concept_actor_role", "compliance_concepts", ["actor_role"]
    )


def downgrade() -> None:
    op.drop_index("ix_compliance_concept_actor_role", table_name="compliance_concepts")
    op.drop_column("compliance_concepts", "actor_role")
