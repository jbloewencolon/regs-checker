"""CSV export/import for pipeline discovery and fetch/parse data.

Provides a human-editable CSV interchange format for:
  1. Discovery results (document families with metadata)
  2. Fetch/parse status (ingestion jobs with URLs and statuses)

This allows users to:
  - Export current pipeline state to CSV
  - Manually correct URLs, titles, statuses in a spreadsheet
  - Re-import corrections back into the database

Usage:
    python -m src.scripts.seed_pipeline --mode export-csv
    python -m src.scripts.seed_pipeline --mode import-csv --input pipeline_data.csv
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    DocumentFamily,
    DocumentVersion,
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
    Source,
    TemporalStatus,
)

logger = structlog.get_logger()

EXPORT_DIR = Path("export")

# CSV column definitions
DISCOVERY_COLUMNS = [
    "family_id",
    "jurisdiction_code",
    "jurisdiction_name",
    "canonical_title",
    "short_cite",
    "subject_area",
    "primary_source_url",
    "orrick_reference_url",
    "iapp_reference_url",
    "bill_number",
    "ai_scope",
    "key_requirements",
    "enforcement",
    "temporal_status",
    "effective_date",
    "version_id",
    "ingestion_job_id",
    "ingestion_status",
    "fetch_url",
    "error_message",
    "passage_count",
    "parse_quality_score",
]


def export_discovery_csv(db: Session, output_path: str | None = None) -> str:
    """Export all document families with ingestion status to CSV.

    Produces one row per document family + latest version + latest ingestion job.
    Includes metadata fields from Orrick/IAPP trackers.

    Args:
        db: SQLAlchemy session.
        output_path: Optional output file path (default: export/pipeline_discovery.csv).

    Returns:
        Path to the written CSV file.
    """
    EXPORT_DIR.mkdir(exist_ok=True)
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(EXPORT_DIR / f"pipeline_discovery_{timestamp}.csv")

    families = db.scalars(
        select(DocumentFamily).order_by(DocumentFamily.id)
    ).all()

    rows: list[dict[str, Any]] = []

    for family in families:
        source = family.source
        # Get latest version
        version = (
            db.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.family_id == family.id)
                .order_by(DocumentVersion.id.desc())
                .limit(1)
            ).first()
        )

        # Get latest ingestion job for this version
        job = None
        if version:
            job = db.scalars(
                select(IngestionJob)
                .where(IngestionJob.document_version_id == version.id)
                .order_by(IngestionJob.id.desc())
                .limit(1)
            ).first()

        # Count passages
        passage_count = 0
        if version:
            passage_count = db.scalar(
                select(func_count())
                .select_from(NormalizedSourceRecord)
                .where(NormalizedSourceRecord.document_version_id == version.id)
            ) or 0

        meta = family.metadata_ or {}

        row = {
            "family_id": family.id,
            "jurisdiction_code": source.jurisdiction_code if source else "",
            "jurisdiction_name": source.jurisdiction_name if source else "",
            "canonical_title": family.canonical_title,
            "short_cite": family.short_cite or "",
            "subject_area": family.subject_area or "",
            "primary_source_url": family.primary_source_url or "",
            "orrick_reference_url": family.orrick_reference_url or "",
            "iapp_reference_url": family.iapp_reference_url or "",
            "bill_number": meta.get("bill_number", meta.get("bill_id", "")),
            "ai_scope": meta.get("ai_scope", ""),
            "key_requirements": meta.get("key_requirements", ""),
            "enforcement": meta.get("enforcement", ""),
            "temporal_status": (
                version.temporal_status.value if version and version.temporal_status else ""
            ),
            "effective_date": str(version.effective_date) if version and version.effective_date else "",
            "version_id": version.id if version else "",
            "ingestion_job_id": job.id if job else "",
            "ingestion_status": job.status.value if job else "",
            "fetch_url": job.fetch_url if job else "",
            "error_message": job.error_message or "" if job else "",
            "passage_count": passage_count,
            "parse_quality_score": (
                f"{job.parse_quality_score:.3f}" if job and job.parse_quality_score else ""
            ),
        }
        rows.append(row)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DISCOVERY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    logger.info("csv_exported", path=output_path, rows=len(rows))
    print(f"Exported {len(rows)} document families to {output_path}")
    return output_path


def func_count():
    """SQLAlchemy count function import helper."""
    from sqlalchemy import func
    return func.count()


def import_discovery_csv(db: Session, input_path: str) -> dict[str, int]:
    """Import corrections from an edited CSV back into the database.

    Supports updating:
      - fetch_url on IngestionJob (and resets status to pending if URL changed)
      - primary_source_url, orrick_reference_url, iapp_reference_url on DocumentFamily
      - canonical_title, short_cite, subject_area on DocumentFamily
      - temporal_status on DocumentVersion
      - error_message clearing (set to empty to clear)

    Args:
        db: SQLAlchemy session.
        input_path: Path to the edited CSV file.

    Returns:
        Summary dict with counts of updates.
    """
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {input_path}")

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    updates = {"families_updated": 0, "urls_updated": 0, "statuses_updated": 0, "total_rows": len(rows)}

    for row in rows:
        family_id = row.get("family_id", "").strip()
        if not family_id:
            continue

        family = db.get(DocumentFamily, int(family_id))
        if not family:
            logger.warning("csv_import_family_not_found", family_id=family_id)
            continue

        family_changed = False

        # Update family fields
        for field_name in ("canonical_title", "short_cite", "subject_area",
                           "primary_source_url", "orrick_reference_url", "iapp_reference_url"):
            csv_val = row.get(field_name, "").strip()
            current_val = getattr(family, field_name, None) or ""
            if csv_val and csv_val != current_val:
                setattr(family, field_name, csv_val if csv_val else None)
                family_changed = True

        if family_changed:
            updates["families_updated"] += 1

        # Update version temporal_status
        version_id = row.get("version_id", "").strip()
        if version_id:
            version = db.get(DocumentVersion, int(version_id))
            if version:
                csv_status = row.get("temporal_status", "").strip()
                if csv_status:
                    try:
                        new_status = TemporalStatus(csv_status)
                        if version.temporal_status != new_status:
                            version.temporal_status = new_status
                            updates["statuses_updated"] += 1
                    except ValueError:
                        logger.warning(
                            "csv_import_invalid_status",
                            version_id=version_id,
                            status=csv_status,
                        )

        # Update ingestion job URL
        job_id = row.get("ingestion_job_id", "").strip()
        if job_id:
            job = db.get(IngestionJob, int(job_id))
            if job:
                csv_url = row.get("fetch_url", "").strip()
                if csv_url and csv_url != (job.fetch_url or ""):
                    job.fetch_url = csv_url
                    # Reset to pending so it gets re-fetched
                    if job.status in (IngestionStatus.failed, IngestionStatus.requires_manual_review):
                        job.status = IngestionStatus.pending
                        job.error_message = None
                    updates["urls_updated"] += 1

                # Allow clearing error messages
                csv_error = row.get("error_message", "").strip()
                if not csv_error and job.error_message:
                    job.error_message = None

    db.commit()

    print(f"CSV import complete:")
    print(f"  Rows processed:     {updates['total_rows']}")
    print(f"  Families updated:   {updates['families_updated']}")
    print(f"  URLs updated:       {updates['urls_updated']}")
    print(f"  Statuses updated:   {updates['statuses_updated']}")

    return updates


def export_fetch_status_csv(db: Session, output_path: str | None = None) -> str:
    """Export ingestion job statuses to a focused CSV for fetch/parse tracking.

    Simpler than the full discovery CSV — just job ID, status, URL, error, passage count.

    Args:
        db: SQLAlchemy session.
        output_path: Optional output path.

    Returns:
        Path to the written CSV file.
    """
    EXPORT_DIR.mkdir(exist_ok=True)
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(EXPORT_DIR / f"fetch_status_{timestamp}.csv")

    columns = [
        "job_id", "jurisdiction", "short_cite", "status", "fetch_url",
        "ai_suggested_url", "error_message", "passage_count", "parse_quality",
    ]

    jobs = db.scalars(select(IngestionJob).order_by(IngestionJob.id)).all()

    rows: list[dict[str, Any]] = []
    for job in jobs:
        dv = job.document_version
        family = dv.family if dv else None
        source = family.source if family else None

        passage_count = 0
        if dv:
            passage_count = db.scalar(
                select(func_count())
                .select_from(NormalizedSourceRecord)
                .where(NormalizedSourceRecord.document_version_id == dv.id)
            ) or 0

        rows.append({
            "job_id": job.id,
            "jurisdiction": source.jurisdiction_code if source else "",
            "short_cite": family.short_cite if family else "",
            "status": job.status.value,
            "fetch_url": job.fetch_url or "",
            "ai_suggested_url": job.ai_suggested_url or "",
            "error_message": job.error_message or "",
            "passage_count": passage_count,
            "parse_quality": (
                f"{job.parse_quality_score:.3f}" if job.parse_quality_score else ""
            ),
        })

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Exported {len(rows)} ingestion jobs to {output_path}")
    return output_path
