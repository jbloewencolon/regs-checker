"""SQL definitions for materialized views — Recommendations #5 and #7.

These replace the multi-stage materialization pipelines with simple SQL views
refreshed by Postgres triggers on review_actions.
"""

CURRENT_ACTIVE_OBLIGATIONS_VIEW = """
CREATE MATERIALIZED VIEW IF NOT EXISTS current_active_obligations AS
SELECT
    e.id AS extraction_id,
    e.extraction_type,
    e.payload,
    e.evidence_spans,
    e.confidence_score,
    e.confidence_tier,
    nsr.section_path,
    nsr.text_content AS source_text,
    dv.version_label,
    dv.effective_date,
    dv.sunset_date,
    dv.temporal_status,
    df.canonical_title AS document_title,
    df.short_cite,
    s.jurisdiction_code,
    s.jurisdiction_name
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
"""

SERVED_OBLIGATIONS_VIEW = """
CREATE MATERIALIZED VIEW IF NOT EXISTS served_obligations AS
SELECT
    e.id AS extraction_id,
    e.extraction_type,
    e.payload,
    e.evidence_spans,
    e.confidence_score,
    e.confidence_tier,
    nsr.section_path,
    nsr.text_content AS source_text,
    dv.id AS document_version_id,
    dv.version_label,
    dv.effective_date,
    dv.sunset_date,
    dv.temporal_status,
    df.canonical_title AS document_title,
    df.short_cite,
    df.subject_area,
    s.jurisdiction_code,
    s.jurisdiction_name,
    s.source_type
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
"""

SERVED_MATRIX_CELLS_VIEW = """
CREATE MATERIALIZED VIEW IF NOT EXISTS served_matrix_cells AS
SELECT
    s.jurisdiction_code,
    s.jurisdiction_name,
    df.subject_area,
    e.extraction_type,
    e.payload->>'modality' AS modality,
    e.payload->>'subject_normalized' AS subject_normalized,
    COUNT(*) AS obligation_count,
    AVG(e.confidence_score) AS avg_confidence
FROM extractions e
JOIN normalized_source_records nsr ON nsr.id = e.source_record_id
JOIN document_versions dv ON dv.id = nsr.document_version_id
JOIN document_families df ON df.id = dv.family_id
JOIN sources s ON s.id = df.source_id
WHERE e.review_status = 'approved'
  AND e.extraction_type = 'obligation'
  AND dv.temporal_status IN ('active', 'future_effective')
GROUP BY
    s.jurisdiction_code,
    s.jurisdiction_name,
    df.subject_area,
    e.extraction_type,
    e.payload->>'modality',
    e.payload->>'subject_normalized'
WITH DATA;
"""

# Tracks the last successful refresh of each served view so /health can report
# freshness without Postgres's own catalogs (which don't record matview refresh time).
VIEW_REFRESH_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS view_refresh_log (
    view_name TEXT PRIMARY KEY,
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO view_refresh_log (view_name, refreshed_at) VALUES
    ('current_active_obligations', now()),
    ('served_obligations', now()),
    ('served_matrix_cells', now())
ON CONFLICT (view_name) DO NOTHING;
"""

# Trigger function to refresh materialized views when reviews are completed
REFRESH_TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION refresh_served_views()
RETURNS TRIGGER AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY served_obligations;
    REFRESH MATERIALIZED VIEW CONCURRENTLY current_active_obligations;
    -- Matrix cells refresh is heavier; do it less frequently via scheduled job
    -- (not yet implemented — see phase2_completion_log.md P2-7 known gap).
    INSERT INTO view_refresh_log (view_name, refreshed_at) VALUES
        ('served_obligations', now()),
        ('current_active_obligations', now())
    ON CONFLICT (view_name) DO UPDATE SET refreshed_at = EXCLUDED.refreshed_at;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
SET search_path = public, pg_catalog;

REVOKE EXECUTE ON FUNCTION public.refresh_served_views() FROM PUBLIC;
"""

REFRESH_TRIGGER = """
DROP TRIGGER IF EXISTS trg_refresh_on_review ON review_actions;
CREATE TRIGGER trg_refresh_on_review
    AFTER INSERT ON review_actions
    FOR EACH STATEMENT
    EXECUTE FUNCTION refresh_served_views();
"""

# Recursive CTE for dependency traversal (Recommendation #4)
DEPENDENCY_TREE_QUERY = """
WITH RECURSIVE dep_tree AS (
    SELECT
        od.parent_extraction_id,
        od.child_extraction_id,
        od.dependency_type,
        1 AS depth
    FROM obligation_dependencies od
    WHERE od.parent_extraction_id = :extraction_id

    UNION ALL

    SELECT
        od.parent_extraction_id,
        od.child_extraction_id,
        od.dependency_type,
        dt.depth + 1
    FROM obligation_dependencies od
    JOIN dep_tree dt ON od.parent_extraction_id = dt.child_extraction_id
    WHERE dt.depth < :max_depth
)
SELECT
    dt.*,
    e.extraction_type,
    e.payload,
    e.confidence_tier
FROM dep_tree dt
JOIN extractions e ON e.id = dt.child_extraction_id
ORDER BY dt.depth, dt.dependency_type;
"""

ALL_VIEW_DEFINITIONS = [
    CURRENT_ACTIVE_OBLIGATIONS_VIEW,
    SERVED_OBLIGATIONS_VIEW,
    SERVED_MATRIX_CELLS_VIEW,
    VIEW_REFRESH_LOG_TABLE,
    REFRESH_TRIGGER_FUNCTION,
    REFRESH_TRIGGER,
]
