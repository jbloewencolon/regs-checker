"""Add compliance_concepts, concept_extraction_links, concept_tracker_links.

Revision ID: p2q8r4s6t017
Revises: o1p7q3r5s016
Create Date: 2026-06-09

Phase 5a: the compliance-concept layer (§7 of the unified plan).  Concepts are
the product unit — a business-facing requirement that groups several normalized
extraction fragments (obligation + deadline + exceptions + enforcement + tracker
refs + evidence).  Grouping is deterministic, keyed on
(document_version_id, concept_type, regulated_actor_family).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "p2q8r4s6t017"
down_revision = "o1p7q3r5s016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # compliance_concepts
    # -----------------------------------------------------------------
    op.create_table(
        "compliance_concepts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_version_id",
            sa.Integer(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        # Grouping key
        sa.Column("concept_type", sa.String(80), nullable=False),
        sa.Column("regulated_actor_family", sa.String(50), nullable=True),
        sa.Column("right_holder_family", sa.String(50), nullable=True),
        sa.Column("covered_system_type", sa.String(80), nullable=True),
        # Human-facing
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("trigger_condition", sa.Text(), nullable=True),
        sa.Column("required_action", sa.Text(), nullable=True),
        sa.Column("deadline", sa.Text(), nullable=True),
        # Structured aggregates
        sa.Column("exceptions", JSONB(), nullable=True),
        sa.Column("enforcement_refs", JSONB(), nullable=True),
        sa.Column("source_extraction_ids", JSONB(), nullable=True),
        sa.Column("tracker_ref_ids", JSONB(), nullable=True),
        # Scoring + review
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("confidence_tier", sa.String(1), nullable=True),
        sa.Column(
            "grounding_status", sa.String(30), nullable=False,
            server_default="ungrounded",
        ),
        sa.Column(
            "review_status",
            sa.Enum(
                "pending", "approved", "flagged", "rejected",
                name="conceptreviewstatus",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "run_id", sa.Integer(),
            sa.ForeignKey("extraction_runs.id"), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_index(
        "ix_compliance_concepts_document_version_id",
        "compliance_concepts", ["document_version_id"],
    )
    op.create_index(
        "ix_compliance_concepts_concept_type",
        "compliance_concepts", ["concept_type"],
    )
    op.create_index(
        "ix_compliance_concepts_regulated_actor_family",
        "compliance_concepts", ["regulated_actor_family"],
    )
    op.create_index(
        "ix_compliance_concepts_run_id",
        "compliance_concepts", ["run_id"],
    )
    op.create_index(
        "uq_compliance_concept_key",
        "compliance_concepts",
        ["document_version_id", "concept_type", "regulated_actor_family"],
        unique=True,
    )
    op.create_index(
        "ix_compliance_concept_review", "compliance_concepts", ["review_status"],
    )
    op.create_index(
        "ix_compliance_concept_grounding", "compliance_concepts", ["grounding_status"],
    )

    # -----------------------------------------------------------------
    # concept_extraction_links
    # -----------------------------------------------------------------
    op.create_table(
        "concept_extraction_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "concept_id", sa.Integer(),
            sa.ForeignKey("compliance_concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "extraction_id", sa.Integer(),
            sa.ForeignKey("extractions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False, server_default="anchor"),
        sa.Column(
            "created_at", sa.DateTime(),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_index(
        "ix_concept_extraction_links_concept_id",
        "concept_extraction_links", ["concept_id"],
    )
    op.create_index(
        "ix_concept_extraction_links_extraction_id",
        "concept_extraction_links", ["extraction_id"],
    )
    op.create_index(
        "uq_concept_extraction_link",
        "concept_extraction_links", ["concept_id", "extraction_id"],
        unique=True,
    )

    # -----------------------------------------------------------------
    # concept_tracker_links
    # -----------------------------------------------------------------
    op.create_table(
        "concept_tracker_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "concept_id", sa.Integer(),
            sa.ForeignKey("compliance_concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tracker_source", sa.String(20), nullable=False),
        sa.Column("tracker_ref", sa.String(120), nullable=False),
        sa.Column(
            "match_status", sa.String(30), nullable=False,
            server_default="tracker_grounded",
        ),
        sa.Column(
            "created_at", sa.DateTime(),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_index(
        "ix_concept_tracker_links_concept_id",
        "concept_tracker_links", ["concept_id"],
    )
    op.create_index(
        "uq_concept_tracker_link",
        "concept_tracker_links", ["concept_id", "tracker_source", "tracker_ref"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_concept_tracker_link", "concept_tracker_links")
    op.drop_index("ix_concept_tracker_links_concept_id", "concept_tracker_links")
    op.drop_table("concept_tracker_links")

    op.drop_index("uq_concept_extraction_link", "concept_extraction_links")
    op.drop_index("ix_concept_extraction_links_extraction_id", "concept_extraction_links")
    op.drop_index("ix_concept_extraction_links_concept_id", "concept_extraction_links")
    op.drop_table("concept_extraction_links")

    op.drop_index("ix_compliance_concept_grounding", "compliance_concepts")
    op.drop_index("ix_compliance_concept_review", "compliance_concepts")
    op.drop_index("uq_compliance_concept_key", "compliance_concepts")
    op.drop_index("ix_compliance_concepts_run_id", "compliance_concepts")
    op.drop_index("ix_compliance_concepts_regulated_actor_family", "compliance_concepts")
    op.drop_index("ix_compliance_concepts_concept_type", "compliance_concepts")
    op.drop_index("ix_compliance_concepts_document_version_id", "compliance_concepts")
    op.drop_table("compliance_concepts")

    sa.Enum(name="conceptreviewstatus").drop(op.get_bind(), checkfirst=True)
