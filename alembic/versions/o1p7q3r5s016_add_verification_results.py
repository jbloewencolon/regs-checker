"""Add verification_run_summaries and extraction_verification_status tables.

Revision ID: o1p7q3r5s016
Revises: n0k6l2m4i915
Create Date: 2026-06-08

Phase 4a: persists what was ephemeral in extraction.metadata_ and the
in-memory VerificationResult dataclass from run_verification_pass().

verification_run_summaries — one row per document-version per verify run;
  stores CV/gap/citation aggregates + gap candidates / citation issues as JSONB.

extraction_verification_status — one row per extraction (upserted each run);
  stores CV score, confidence before/after recompute, Orrick grounding status,
  and iapp_status placeholder (populated by Phase 4b).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "o1p7q3r5s016"
down_revision = "n0k6l2m4i915"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # verification_run_summaries
    # -----------------------------------------------------------------
    op.create_table(
        "verification_run_summaries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_version_id",
            sa.Integer(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "run_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Cross-validation
        sa.Column("cv_passages_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cv_extractions_valid", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cv_extractions_flagged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cv_avg_accuracy", sa.Float(), nullable=True),
        # Gap detection
        sa.Column("gd_passages_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gd_gaps_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gd_high_confidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gap_candidates", JSONB(), nullable=True),
        # Citation verification
        sa.Column("citations_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("citations_verified", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("citations_unverified", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("citation_issues", JSONB(), nullable=True),
        # Token usage
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_vrs_document_version_id",
        "verification_run_summaries",
        ["document_version_id"],
    )
    op.create_index(
        "ix_vrs_run_at",
        "verification_run_summaries",
        ["run_at"],
    )

    # -----------------------------------------------------------------
    # extraction_verification_status
    # -----------------------------------------------------------------
    op.create_table(
        "extraction_verification_status",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "extraction_id",
            sa.Integer(),
            sa.ForeignKey("extractions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "verification_run_id",
            sa.Integer(),
            sa.ForeignKey("verification_run_summaries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        # Cross-validation
        sa.Column("cv_score", sa.Float(), nullable=True),
        sa.Column("cv_is_valid", sa.Boolean(), nullable=True),
        sa.Column("cv_flagged", sa.Boolean(), nullable=False, server_default="false"),
        # Confidence tracking
        sa.Column("confidence_before", sa.Float(), nullable=True),
        sa.Column("confidence_after", sa.Float(), nullable=True),
        sa.Column("tier_before", sa.String(1), nullable=True),
        sa.Column("tier_after", sa.String(1), nullable=True),
        sa.Column("tier_changed", sa.Boolean(), nullable=False, server_default="false"),
        # Orrick grounding
        sa.Column("orrick_status", sa.String(30), nullable=True),
        sa.Column("orrick_score", sa.Float(), nullable=True),
        sa.Column("orrick_gated", sa.Boolean(), nullable=False, server_default="false"),
        # IAPP grounding (Phase 4b)
        sa.Column("iapp_status", sa.String(30), nullable=True),
        # Combined status
        sa.Column(
            "grounding_status",
            sa.String(30),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_evs_extraction_id",
        "extraction_verification_status",
        ["extraction_id"],
    )
    op.create_index(
        "ix_evs_verification_run_id",
        "extraction_verification_status",
        ["verification_run_id"],
    )
    op.create_index(
        "ix_evs_document_version_id",
        "extraction_verification_status",
        ["document_version_id"],
    )
    op.create_index(
        "ix_evs_grounding_status",
        "extraction_verification_status",
        ["grounding_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_evs_grounding_status", "extraction_verification_status")
    op.drop_index("ix_evs_document_version_id", "extraction_verification_status")
    op.drop_index("ix_evs_verification_run_id", "extraction_verification_status")
    op.drop_index("ix_evs_extraction_id", "extraction_verification_status")
    op.drop_table("extraction_verification_status")

    op.drop_index("ix_vrs_run_at", "verification_run_summaries")
    op.drop_index("ix_vrs_document_version_id", "verification_run_summaries")
    op.drop_table("verification_run_summaries")
