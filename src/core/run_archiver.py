"""Extraction run archiver — creates dated output folders for each extraction run.

Each extraction run gets its own timestamped folder under output/extraction_runs/
containing a summary JSON and a CSV export of all extractions created in that run.

Folder structure:
    output/extraction_runs/
        2026-04-02_143022_extract/
            run_summary.json        — run metadata, counts, token usage, timing
            extractions.csv         — all extractions from this run
            agent_stats.json        — per-agent performance breakdown
        2026-04-02_160500_extract/
            ...

Usage in extractor.py:
    archiver = RunArchiver.start("extract")
    # ... run extraction ...
    archiver.finalize(db, summary)
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = structlog.get_logger()

RUNS_DIR = Path(__file__).resolve().parents[2] / "output" / "extraction_runs"


class RunArchiver:
    """Creates and manages a dated output folder for a single extraction run."""

    def __init__(self, run_dir: Path, run_type: str, started_at: datetime) -> None:
        self.run_dir = run_dir
        self.run_type = run_type
        self.started_at = started_at

    @classmethod
    def start(cls, run_type: str = "extract") -> RunArchiver:
        """Create a new dated run folder and return an archiver instance.

        Args:
            run_type: Label for the run (e.g., "extract", "re-extract", "triage").
        """
        now = datetime.now(timezone.utc)
        folder_name = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{run_type}"
        run_dir = RUNS_DIR / folder_name
        run_dir.mkdir(parents=True, exist_ok=True)

        logger.info("run_archiver_started", run_dir=str(run_dir))
        return cls(run_dir=run_dir, run_type=run_type, started_at=now)

    def finalize(
        self,
        db: Session,
        summary: dict[str, Any],
        extraction_job_ids: list[int] | None = None,
    ) -> Path:
        """Write run summary and export extractions to the run folder.

        Args:
            db: SQLAlchemy session for querying extractions.
            summary: The run summary dict from run_extraction().
            extraction_job_ids: Specific job IDs to export. If None, exports
                all extractions created after self.started_at.

        Returns:
            Path to the run folder.
        """
        finished_at = datetime.now(timezone.utc)

        # 1. Write run summary
        run_meta = {
            "run_type": self.run_type,
            "started_at": self.started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round((finished_at - self.started_at).total_seconds(), 1),
            "folder": str(self.run_dir),
            **summary,
        }
        summary_path = self.run_dir / "run_summary.json"
        with open(summary_path, "w") as f:
            json.dump(run_meta, f, indent=2, default=str)

        # 2. Export extractions created during this run
        extractions_path = self._export_extractions(db, extraction_job_ids)

        # 3. Write agent stats if available from the monitor
        self._export_agent_stats()

        # Add folder path to the summary so callers can reference it
        summary["folder"] = str(self.run_dir)

        logger.info(
            "run_archiver_finalized",
            run_dir=str(self.run_dir),
            summary_path=str(summary_path),
            extractions_path=str(extractions_path),
            duration_seconds=run_meta["duration_seconds"],
        )

        return self.run_dir

    def _export_extractions(
        self,
        db: Session,
        extraction_job_ids: list[int] | None,
    ) -> Path:
        """Export all extractions from this run to CSV."""
        from src.db.models import (
            DocumentFamily,
            DocumentVersion,
            Extraction,
            ExtractionJob,
            NormalizedSourceRecord,
            Source,
        )

        # Build query for extractions created during this run
        if extraction_job_ids:
            query = (
                select(
                    Extraction.id,
                    Extraction.extraction_type,
                    Extraction.confidence_score,
                    Extraction.confidence_tier,
                    Extraction.model_id,
                    Extraction.payload,
                    Extraction.evidence_spans,
                    Extraction.created_at,
                    NormalizedSourceRecord.section_path,
                    NormalizedSourceRecord.text_content,
                    Source.jurisdiction_code,
                    DocumentFamily.short_cite,
                    DocumentFamily.canonical_title,
                )
                .join(NormalizedSourceRecord, Extraction.source_record_id == NormalizedSourceRecord.id)
                .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
                .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
                .join(Source, DocumentFamily.source_id == Source.id)
                .where(Extraction.extraction_job_id.in_(extraction_job_ids))
                .order_by(Source.jurisdiction_code, Extraction.id)
            )
        else:
            query = (
                select(
                    Extraction.id,
                    Extraction.extraction_type,
                    Extraction.confidence_score,
                    Extraction.confidence_tier,
                    Extraction.model_id,
                    Extraction.payload,
                    Extraction.evidence_spans,
                    Extraction.created_at,
                    NormalizedSourceRecord.section_path,
                    NormalizedSourceRecord.text_content,
                    Source.jurisdiction_code,
                    DocumentFamily.short_cite,
                    DocumentFamily.canonical_title,
                )
                .join(NormalizedSourceRecord, Extraction.source_record_id == NormalizedSourceRecord.id)
                .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
                .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
                .join(Source, DocumentFamily.source_id == Source.id)
                .where(Extraction.created_at >= self.started_at)
                .order_by(Source.jurisdiction_code, Extraction.id)
            )

        rows = db.execute(query).all()

        csv_path = self.run_dir / "extractions.csv"
        fieldnames = [
            "extraction_id", "jurisdiction", "law", "title", "section",
            "extraction_type", "confidence_score", "confidence_tier",
            "model_id", "payload_json", "evidence_spans_json", "created_at",
        ]

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "extraction_id": row[0],
                    "jurisdiction": row[10],
                    "law": row[11],
                    "title": row[12],
                    "section": row[8],
                    "extraction_type": row[1].value if hasattr(row[1], "value") else str(row[1]),
                    "confidence_score": round(float(row[2]), 4) if row[2] else "",
                    "confidence_tier": row[3].value if hasattr(row[3], "value") else str(row[3]),
                    "model_id": row[4] or "",
                    "payload_json": json.dumps(row[5], default=str) if row[5] else "",
                    "evidence_spans_json": json.dumps(row[6], default=str) if row[6] else "",
                    "created_at": row[7].isoformat() if row[7] else "",
                })

        logger.info("run_archiver_exported_csv", path=str(csv_path), row_count=len(rows))
        return csv_path

    def _export_agent_stats(self) -> Path | None:
        """Export agent performance stats from the extraction monitor."""
        try:
            from src.core.extraction_monitor import get_monitor
            monitor = get_monitor()
            snapshot = monitor.snapshot(recent_count=0)
            stats_dict = snapshot.to_dict()

            stats_path = self.run_dir / "agent_stats.json"
            with open(stats_path, "w") as f:
                json.dump(stats_dict, f, indent=2, default=str)
            return stats_path
        except Exception as e:
            logger.warning("run_archiver_agent_stats_failed", error=str(e))
            return None
