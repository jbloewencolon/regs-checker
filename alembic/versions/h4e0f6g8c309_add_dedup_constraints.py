"""Add deduplication constraints across pipeline stages.

- Extraction: add payload_hash column + unique index on
  (source_record_id, extraction_type, payload_hash)
- NormalizedSourceRecord: unique index on (document_version_id, ordinal)
- IngestionJob: partial unique index on (document_version_id, fetch_url)
  WHERE fetch_url IS NOT NULL

Revision ID: h4e0f6g8c309
Revises: g3d9e5f7b208
Create Date: 2026-03-23 00:00:00.000000
"""
import hashlib
import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "h4e0f6g8c309"
down_revision = "g3d9e5f7b208"
branch_labels = None
depends_on = None


def _payload_hash(payload: dict) -> str:
    """Must match src/ingestion/extractor._payload_hash exactly."""
    clean = {
        k: v for k, v in sorted(payload.items())
        if not k.startswith("_") and k != "evidence_spans"
    }
    return hashlib.sha256(
        json.dumps(clean, sort_keys=True, default=str).encode()
    ).hexdigest()


def upgrade() -> None:
    # -- Extraction: payload_hash column --
    op.add_column(
        "extractions",
        sa.Column("payload_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_extractions_payload_hash", "extractions", ["payload_hash"]
    )

    # Back-fill payload_hash for existing rows using the same Python function
    # that the application uses, to ensure hash consistency.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, payload FROM extractions WHERE payload_hash IS NULL")
    ).fetchall()
    if rows:
        for row_id, payload in rows:
            h = _payload_hash(payload if isinstance(payload, dict) else json.loads(payload))
            conn.execute(
                sa.text("UPDATE extractions SET payload_hash = :h WHERE id = :id"),
                {"h": h, "id": row_id},
            )

    # Unique dedup index on extractions
    op.create_index(
        "uq_extractions_dedup",
        "extractions",
        ["source_record_id", "extraction_type", "payload_hash"],
        unique=True,
    )

    # -- NormalizedSourceRecord: unique on (document_version_id, ordinal) --
    # Drop the existing non-unique index first, then create unique one
    op.drop_index("ix_nsr_version_ordinal", table_name="normalized_source_records")
    op.create_index(
        "uq_nsr_version_ordinal",
        "normalized_source_records",
        ["document_version_id", "ordinal"],
        unique=True,
    )

    # -- IngestionJob: partial unique on (document_version_id, fetch_url) --
    op.create_index(
        "uq_ingestion_job_version_url",
        "ingestion_jobs",
        ["document_version_id", "fetch_url"],
        unique=True,
        postgresql_where=sa.text("fetch_url IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_ingestion_job_version_url", table_name="ingestion_jobs")

    op.drop_index("uq_nsr_version_ordinal", table_name="normalized_source_records")
    op.create_index(
        "ix_nsr_version_ordinal",
        "normalized_source_records",
        ["document_version_id", "ordinal"],
    )

    op.drop_index("uq_extractions_dedup", table_name="extractions")
    op.drop_index("ix_extractions_payload_hash", table_name="extractions")
    op.drop_column("extractions", "payload_hash")
