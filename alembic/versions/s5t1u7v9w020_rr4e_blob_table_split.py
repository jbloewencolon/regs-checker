"""RR4e: split RawArtifact into ContentBlob + RawArtifact link table.

Revision ID: s5t1u7v9w020
Revises: r4s0t6u8v019
Create Date: 2026-06-09

Problem: sha256_hash was globally UNIQUE on raw_artifacts, preventing two
document versions from referencing the same PDF content (e.g. re-fetched laws,
version bumps with identical text).

Fix:
  1. Create content_blobs table — one row per unique sha256_hash.
  2. Backfill: insert one content_blobs row per distinct sha256_hash currently
     in raw_artifacts (preserving s3_key / content_type / size_bytes).
  3. Add content_blob_id FK column to raw_artifacts.
  4. Backfill content_blob_id for every existing raw_artifacts row.
  5. Drop the UNIQUE constraint on raw_artifacts.sha256_hash (replaced by
     the unique constraint on content_blobs.sha256_hash).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "s5t1u7v9w020"
down_revision = "r4s0t6u8v019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create content_blobs table
    op.create_table(
        "content_blobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sha256_hash", sa.String(64), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_content_blobs_sha256", "content_blobs", ["sha256_hash"], unique=True)

    # 2. Backfill content_blobs from distinct sha256_hash values in raw_artifacts
    op.execute(sa.text("""
        INSERT INTO content_blobs (sha256_hash, s3_key, content_type, size_bytes, created_at)
        SELECT DISTINCT ON (sha256_hash)
            sha256_hash, s3_key, content_type, size_bytes, created_at
        FROM raw_artifacts
        ORDER BY sha256_hash, id
        ON CONFLICT (sha256_hash) DO NOTHING
    """))

    # 3. Add content_blob_id FK column to raw_artifacts
    op.add_column(
        "raw_artifacts",
        sa.Column("content_blob_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_raw_artifacts_content_blob",
        "raw_artifacts",
        "content_blobs",
        ["content_blob_id"],
        ["id"],
    )
    op.create_index("ix_raw_artifacts_content_blob_id", "raw_artifacts", ["content_blob_id"])

    # 4. Backfill content_blob_id for all existing rows
    op.execute(sa.text("""
        UPDATE raw_artifacts ra
        SET content_blob_id = cb.id
        FROM content_blobs cb
        WHERE ra.sha256_hash = cb.sha256_hash
    """))

    # 5. Drop the global UNIQUE constraint on raw_artifacts.sha256_hash
    #    The constraint name is typically uq_raw_artifacts_sha256_hash or similar.
    #    Use DROP INDEX to handle both named and unnamed unique constraints.
    op.execute(sa.text("""
        DO $$
        DECLARE
            _con TEXT;
        BEGIN
            SELECT constraint_name INTO _con
            FROM information_schema.table_constraints
            WHERE table_name = 'raw_artifacts'
              AND constraint_type = 'UNIQUE'
              AND constraint_name NOT LIKE 'ix_%';
            IF _con IS NOT NULL THEN
                EXECUTE format('ALTER TABLE raw_artifacts DROP CONSTRAINT %I', _con);
            END IF;
        END $$
    """))

    # Add a plain index on raw_artifacts.sha256_hash (for lookups)
    op.create_index("ix_raw_artifacts_sha256", "raw_artifacts", ["sha256_hash"])


def downgrade() -> None:
    # Re-add global unique constraint (only if all sha256_hash values are distinct)
    op.execute(sa.text(
        "ALTER TABLE raw_artifacts ADD CONSTRAINT uq_raw_artifacts_sha256_hash "
        "UNIQUE (sha256_hash)"
    ))
    op.drop_index("ix_raw_artifacts_sha256", table_name="raw_artifacts")
    op.drop_index("ix_raw_artifacts_content_blob_id", table_name="raw_artifacts")
    op.drop_constraint("fk_raw_artifacts_content_blob", "raw_artifacts", type_="foreignkey")
    op.drop_column("raw_artifacts", "content_blob_id")
    op.drop_index("ix_content_blobs_sha256", table_name="content_blobs")
    op.drop_table("content_blobs")
