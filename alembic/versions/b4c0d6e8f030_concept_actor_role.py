"""Add actor_role to compliance_concepts.

Phase 2 of the product-review remediation plan: classify each concept's
regulated_actor_family into a three-value actor_role
("government" / "regulated_entity" / "individual") at grouping time.
Enables actor-role facet filtering without a vocab-table join.

Revision ID: b4c0d6e8f030
Revises: a3b9c5d7e028

P1-1: this revision previously collided with a3b9c5d7e028_di1_canonical_key_
agent_name.py (both files declared revision = "a3b9c5d7e028"), which made
`alembic upgrade head` fail on any fresh database. The two migrations touch
disjoint tables (compliance_concepts vs document_families/extractions) so
there is no ordering dependency between them; this one was re-keyed to
b4c0d6e8f030 and chained after the DI-1 migration. b4c0d6e8f029_extraction_
model_agreement.py's down_revision was updated to point here instead of
directly to a3b9c5d7e028.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4c0d6e8f030"
down_revision = "a3b9c5d7e028"
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
