"""Add section_triage_results table for AI-relevance filtering.

Section triage sits between parse and extraction — each passage gets a
relevance decision (relevant / not_relevant / uncertain) before the full
6-agent extraction battery runs.  This saves ~50% of LLM calls on typical
bills while keeping every decision auditable and reviewable.

Revision ID: g3d9e5f7b208
Revises: f2c8d4e6a107
Create Date: 2026-03-23 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "g3d9e5f7b208"
down_revision = "f2c8d4e6a107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enums first, then reference them in the table with
    # create_type=False so SQLAlchemy doesn't try to create them again.
    triage_decision = sa.Enum(
        "relevant", "not_relevant", "uncertain",
        name="triagedecision",
        create_type=False,
    )
    triage_method = sa.Enum(
        "keyword", "orrick_cross_check", "llm_generic", "quality_fail", "passthrough",
        name="triagemethod",
        create_type=False,
    )

    # Explicitly create the types (idempotent via checkfirst)
    sa.Enum(
        "relevant", "not_relevant", "uncertain",
        name="triagedecision",
    ).create(op.get_bind(), checkfirst=True)
    sa.Enum(
        "keyword", "orrick_cross_check", "llm_generic", "quality_fail", "passthrough",
        name="triagemethod",
    ).create(op.get_bind(), checkfirst=True)

    op.create_table(
        "section_triage_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "source_record_id", sa.Integer(),
            sa.ForeignKey("normalized_source_records.id"),
            nullable=False, unique=True,
        ),
        sa.Column("decision", triage_decision, nullable=False),
        sa.Column("method", triage_method, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("matched_keywords", JSONB, server_default="[]"),
        sa.Column("orrick_terms_checked", JSONB, server_default="[]"),
        sa.Column("llm_reasoning", sa.Text(), nullable=True),
        sa.Column("pdf_quality_score", sa.Float(), nullable=True),
        sa.Column("quality_flags", JSONB, server_default="[]"),
        sa.Column("model_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_index("ix_triage_source_record", "section_triage_results", ["source_record_id"])
    op.create_index("ix_triage_decision", "section_triage_results", ["decision"])


def downgrade() -> None:
    op.drop_table("section_triage_results")
    op.execute("DROP TYPE IF EXISTS triagedecision")
    op.execute("DROP TYPE IF EXISTS triagemethod")
