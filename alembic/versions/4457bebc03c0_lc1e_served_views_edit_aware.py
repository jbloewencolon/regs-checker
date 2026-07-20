"""LC-1e: served materialized views + dependency-tree query read
edit-aware payload (COALESCE(effective_payload, payload)).

current_active_obligations, served_obligations, and served_matrix_cells
(src/db/views.py) previously selected the raw Extraction.payload column
directly — after LC-1a introduced effective_payload as the edit overlay
(fixing the G-1 destructive-edit defect), these product-serving views would
have kept showing an extraction's original, un-corrected values even after
an analyst applied a validated correction through the Law Card editor.
Materialized views can't use CREATE OR REPLACE (Postgres restriction), so
picking up the corrected src/db/views.py definitions requires an explicit
drop + recreate, not just an app-code change.

Revision ID: 4457bebc03c0
Revises: 72ad4147a628
"""

from __future__ import annotations

from alembic import op

from src.db.views import (
    CURRENT_ACTIVE_OBLIGATIONS_VIEW,
    REFRESH_TRIGGER,
    REFRESH_TRIGGER_FUNCTION,
    SERVED_MATRIX_CELLS_VIEW,
    SERVED_OBLIGATIONS_VIEW,
)

revision = "4457bebc03c0"
down_revision = "72ad4147a628"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop trigger first (it references the function which references the
    # views) — same order the original downgrade() in a3f7b2c8d901 used.
    op.execute("DROP TRIGGER IF EXISTS trg_refresh_on_review ON review_actions")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS served_matrix_cells")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS served_obligations")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_active_obligations")

    # Recreate from the now-corrected source-of-truth definitions.
    op.execute(CURRENT_ACTIVE_OBLIGATIONS_VIEW)
    op.execute(SERVED_OBLIGATIONS_VIEW)
    op.execute(SERVED_MATRIX_CELLS_VIEW)
    op.execute(REFRESH_TRIGGER_FUNCTION)  # CREATE OR REPLACE — safe to re-run as-is
    op.execute(REFRESH_TRIGGER)


def downgrade() -> None:
    # Reverting means going back to the pre-LC-1 view definitions — restore
    # them directly (effective_payload didn't exist before LC-1a either, so
    # this is only reachable if LC-1a is also downgraded first).
    op.execute("DROP TRIGGER IF EXISTS trg_refresh_on_review ON review_actions")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS served_matrix_cells")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS served_obligations")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_active_obligations")

    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS current_active_obligations AS
        SELECT
            e.id AS extraction_id, e.extraction_type, e.payload, e.evidence_spans,
            e.confidence_score, e.confidence_tier, nsr.section_path,
            nsr.text_content AS source_text, dv.version_label, dv.effective_date,
            dv.sunset_date, dv.temporal_status, df.canonical_title AS document_title,
            df.short_cite, s.jurisdiction_code, s.jurisdiction_name
        FROM extractions e
        JOIN normalized_source_records nsr ON nsr.id = e.source_record_id
        JOIN document_versions dv ON dv.id = nsr.document_version_id
        JOIN document_families df ON df.id = dv.family_id
        JOIN sources s ON s.id = df.source_id
        WHERE e.review_status = 'approved'
          AND e.extraction_type = 'obligation'
          AND dv.temporal_status = 'active'
          AND (dv.sunset_date IS NULL OR dv.sunset_date > CURRENT_DATE)
        WITH DATA;
        CREATE UNIQUE INDEX IF NOT EXISTS ix_cao_extraction_id
            ON current_active_obligations (extraction_id);
    """)
    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS served_obligations AS
        SELECT
            e.id AS extraction_id, e.extraction_type, e.payload, e.evidence_spans,
            e.confidence_score, e.confidence_tier, nsr.section_path,
            nsr.text_content AS source_text, dv.id AS document_version_id,
            dv.version_label, dv.effective_date, dv.sunset_date, dv.temporal_status,
            df.canonical_title AS document_title, df.short_cite, df.subject_area,
            s.jurisdiction_code, s.jurisdiction_name, s.source_type
        FROM extractions e
        JOIN normalized_source_records nsr ON nsr.id = e.source_record_id
        JOIN document_versions dv ON dv.id = nsr.document_version_id
        JOIN document_families df ON df.id = dv.family_id
        JOIN sources s ON s.id = df.source_id
        WHERE e.review_status = 'approved'
        WITH DATA;
        CREATE UNIQUE INDEX IF NOT EXISTS ix_so_extraction_id
            ON served_obligations (extraction_id);
        CREATE INDEX IF NOT EXISTS ix_so_jurisdiction
            ON served_obligations (jurisdiction_code);
        CREATE INDEX IF NOT EXISTS ix_so_type
            ON served_obligations (extraction_type);
    """)
    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS served_matrix_cells AS
        SELECT
            s.jurisdiction_code, s.jurisdiction_name, df.subject_area, e.extraction_type,
            e.payload->>'modality' AS modality,
            e.payload->>'subject_normalized' AS subject_normalized,
            COUNT(*) AS obligation_count, AVG(e.confidence_score) AS avg_confidence
        FROM extractions e
        JOIN normalized_source_records nsr ON nsr.id = e.source_record_id
        JOIN document_versions dv ON dv.id = nsr.document_version_id
        JOIN document_families df ON df.id = dv.family_id
        JOIN sources s ON s.id = df.source_id
        WHERE e.review_status = 'approved'
          AND e.extraction_type = 'obligation'
          AND dv.temporal_status IN ('active', 'future_effective')
        GROUP BY
            s.jurisdiction_code, s.jurisdiction_name, df.subject_area, e.extraction_type,
            e.payload->>'modality', e.payload->>'subject_normalized'
        WITH DATA;
    """)
    op.execute(REFRESH_TRIGGER_FUNCTION)
    op.execute(REFRESH_TRIGGER)
