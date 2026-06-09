"""RR7b + RR7d: DocumentVersion source-provenance columns + is_serving uniqueness.

Revision ID: v8w4x0y2z023
Revises: u7v3w9x1y022
Create Date: 2026-06-09

RR7b — adds source-provenance columns to document_versions:
  session_year  — state legislative session year (e.g. 2024)
  bill_number   — bill identifier (e.g. "SB 205", "HB 1234")
  retrieved_at  — timestamp when this version was fetched
  source_hash   — SHA-256 of the source content (for change detection)

RR7d — adds a partial unique index on extraction_runs.is_serving so the
DB enforces that at most one run has is_serving=TRUE at a time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v8w4x0y2z023"
down_revision = "u7v3w9x1y022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # RR7b — source-provenance columns on document_versions
    op.add_column("document_versions", sa.Column("session_year", sa.Integer(), nullable=True))
    op.add_column("document_versions", sa.Column("bill_number", sa.String(50), nullable=True))
    op.add_column("document_versions", sa.Column("retrieved_at", sa.DateTime(), nullable=True))
    op.add_column("document_versions", sa.Column("source_hash", sa.String(64), nullable=True))

    op.create_index("ix_document_versions_session_year", "document_versions", ["session_year"])
    op.create_index("ix_document_versions_bill_number", "document_versions", ["bill_number"])

    # RR7d — partial unique index: at most one serving run at a time
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_extraction_runs_serving "
        "ON extraction_runs (is_serving) WHERE is_serving = TRUE"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_extraction_runs_serving"))
    op.drop_index("ix_document_versions_bill_number", table_name="document_versions")
    op.drop_index("ix_document_versions_session_year", table_name="document_versions")
    op.drop_column("document_versions", "source_hash")
    op.drop_column("document_versions", "retrieved_at")
    op.drop_column("document_versions", "bill_number")
    op.drop_column("document_versions", "session_year")
