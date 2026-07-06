"""Create failed_extraction_attempts table.

Revision ID: bf74ef19697d
Revises: x0y6z2a4b025

P1-1: this table (backing src.db.models.FailedExtractionAttempt) was never
created by an Alembic migration — it only ever existed via the
`_ensure_failed_attempts_table()` raw-SQL fallback in
src/ingestion/extractor.py, which ran `CREATE TABLE IF NOT EXISTS` at
extraction start. That meant `alembic upgrade head` failed on any fresh
database as soon as it reached y1z7a3b5c026_failed_attempts_run_id.py,
which ALTERs this table without anything ever having created it first.

This migration creates the table in the same shape the raw-SQL fallback
used (matching src.db.models.FailedExtractionAttempt, minus run_id — that
column is added immediately afterward by the existing
y1z7a3b5c026_failed_attempts_run_id.py migration, unchanged). Existing
databases where the raw-SQL fallback already created this table will be
reconciled onto the Alembic history via `alembic stamp` per P1-5; this
migration is what a fresh database now runs instead of relying on the hack.

Update (RC4-1): the `_ensure_*` raw-SQL fallbacks in extractor.py referenced
above have since been retired — this migration (plus its peers) is now the
sole creator of the table. The reference is kept for historical context only.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "bf74ef19697d"
down_revision = "x0y6z2a4b025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "failed_extraction_attempts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "source_record_id",
            sa.Integer,
            sa.ForeignKey("normalized_source_records.id"),
            nullable=False,
        ),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("error_type", sa.String(50), nullable=False),
        sa.Column("error_message", sa.Text, nullable=False),
        sa.Column(
            "extraction_job_id",
            sa.Integer,
            sa.ForeignKey("extraction_jobs.id"),
            nullable=True,
        ),
        sa.Column("retried", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("retry_succeeded", sa.Boolean, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_failed_attempts_source",
        "failed_extraction_attempts",
        ["source_record_id"],
    )
    op.create_index(
        "ix_failed_attempts_retry",
        "failed_extraction_attempts",
        ["retried", "agent_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_failed_attempts_retry", table_name="failed_extraction_attempts")
    op.drop_index("ix_failed_attempts_source", table_name="failed_extraction_attempts")
    op.drop_table("failed_extraction_attempts")
