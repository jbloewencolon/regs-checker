"""Add bill_level_extractions table.

Revision ID: k7h3i9j1f612
Revises: j6g2h8i0e511
Create Date: 2026-05-08

Adds a new table for bill-level agents that run once per DocumentVersion
(law) rather than per passage.  These agents see the full bill text and
produce one structured record per law — solving the cross-section reference
problem where penalty amounts are defined in a different section from the
obligation that references them.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "k7h3i9j1f612"
down_revision = "j6g2h8i0e511"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bill_level_extractions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column(
            "review_status",
            sa.Enum(
                "pending", "approved", "rejected", "needs_revision",
                name="reviewstatus",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("model_id", sa.String(100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("truncated", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("metadata", postgresql.JSONB(), nullable=True, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["document_versions.id"], name="fk_ble_document_version"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bill_level_extractions_dv",
        "bill_level_extractions",
        ["document_version_id"],
    )
    op.create_unique_constraint(
        "uq_bill_level_extractions",
        "bill_level_extractions",
        ["document_version_id", "agent_name"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_bill_level_extractions", "bill_level_extractions", type_="unique")
    op.drop_index("ix_bill_level_extractions_dv", table_name="bill_level_extractions")
    op.drop_table("bill_level_extractions")
