"""DI-1 + Phase A: canonical_key on document_families; agent_name on extractions.

DI-1: Promotes canonical_law_id from JSONB metadata to a first-class indexed
column with UNIQUE constraint so document_family identity survives DB wipe/re-seed
and the downstream consumer can join on a stable key.

Phase A: Adds an indexed agent_name column to extractions so per-agent filtering,
export, selective sync, re-runs, and accuracy metrics are queryable without
reversing through the extraction_type → agent_name map.

Revision ID: a3b9c5d7e028
Revises: z2a8b4c6d027

P1-1: this ID previously collided with another migration (concept_actor_role,
also declared as a3b9c5d7e028), which broke `alembic upgrade head` on any
fresh database. This file's revision ID is unchanged; the other migration was
re-keyed to b4c0d6e8f030 and now chains after this one instead.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3b9c5d7e028"
down_revision = "z2a8b4c6d027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # DI-1: canonical_key on document_families
    # -----------------------------------------------------------------
    # Add as nullable first so the backfill can run before we enforce NOT NULL.
    op.add_column(
        "document_families",
        sa.Column("canonical_key", sa.String(200), nullable=True),
    )

    # Backfill from the existing JSONB metadata_ column.
    op.execute(
        """
        UPDATE document_families
        SET canonical_key = metadata->>'canonical_law_id'
        WHERE metadata->>'canonical_law_id' IS NOT NULL
        """
    )

    # Now enforce NOT NULL + UNIQUE (rows without a canonical_law_id stay NULL
    # until ingest re-seeds them — do NOT alter column to NOT NULL here because
    # legacy rows from before local_ingest.py canonicalization may exist).
    op.create_index(
        "uq_document_families_canonical_key",
        "document_families",
        ["canonical_key"],
        unique=True,
        postgresql_where=sa.text("canonical_key IS NOT NULL"),
    )
    # Plain index for non-unique lookups (NULL rows).
    op.create_index(
        "ix_document_families_canonical_key",
        "document_families",
        ["canonical_key"],
    )

    # -----------------------------------------------------------------
    # Phase A: agent_name on extractions
    # -----------------------------------------------------------------
    op.add_column(
        "extractions",
        sa.Column("agent_name", sa.String(100), nullable=True),
    )

    # Backfill from the deterministic extraction_type → agent_name reverse map
    # (matches AGENT_EXTRACTION_TYPES in extractor.py).
    op.execute(
        """
        UPDATE extractions
        SET agent_name = CASE extraction_type::text
            WHEN 'obligation'            THEN 'obligation'
            WHEN 'timeline'              THEN 'obligation'
            WHEN 'enforcement'           THEN 'obligation'
            WHEN 'definition'            THEN 'definition_actor'
            WHEN 'actor_mapping'         THEN 'definition_actor'
            WHEN 'framework_ref'         THEN 'definition_actor'
            WHEN 'threshold'             THEN 'threshold_exception'
            WHEN 'exception'             THEN 'threshold_exception'
            WHEN 'rights_protection'     THEN 'rights_protection'
            WHEN 'compliance_mechanism'  THEN 'compliance_mechanism'
            WHEN 'preemption_signal'     THEN 'preemption'
            ELSE NULL
        END
        WHERE agent_name IS NULL
        """
    )

    # Cross-check with ExtractionAttempt where available to fix any
    # ambiguous rows (e.g. if a type can theoretically come from two agents).
    op.execute(
        """
        UPDATE extractions e
        SET agent_name = ea.agent_name
        FROM extraction_attempts ea
        WHERE ea.source_record_id = e.source_record_id
          AND ea.status = 'succeeded'
          AND e.agent_name IS NULL
          AND ea.agent_name IS NOT NULL
        """
    )

    op.create_index(
        "ix_extractions_agent_name",
        "extractions",
        ["agent_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_extractions_agent_name", table_name="extractions")
    op.drop_column("extractions", "agent_name")

    op.drop_index("ix_document_families_canonical_key", table_name="document_families")
    op.drop_index("uq_document_families_canonical_key", table_name="document_families")
    op.drop_column("document_families", "canonical_key")
