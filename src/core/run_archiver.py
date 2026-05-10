"""Extraction run archiver — manages a single active session folder for extractions.

All batch runs accumulate into one active folder. Only a full reset archives the
active folder and starts a fresh one.

Folder structure:
    output/extraction_runs/
        active/                         ← current session (always up-to-date)
            run_summary.json
            extractions.csv             ← ALL extractions in DB (rebuilt on each batch)
            agent_stats.json
        2026-04-02_143022_extract/      ← archived previous session (after reset)
            run_summary.json
            extractions.csv
            agent_stats.json

Usage in extractor.py:
    archiver = RunArchiver.start("extract", is_fresh_run=True)   # resets active folder
    archiver = RunArchiver.start("extract", is_fresh_run=False)  # continues active folder
    archiver.finalize(db, summary)
"""

from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = structlog.get_logger()

RUNS_DIR = Path(__file__).resolve().parents[2] / "output" / "extraction_runs"
ACTIVE_DIR = RUNS_DIR / "active"


def archive_active_folder() -> Path | None:
    """Move the active folder to a timestamped archive and return the archive path.

    Called by run_extraction() on reset (full run that purges all data). Safe to
    call even if no active folder exists.
    """
    if not ACTIVE_DIR.exists():
        return None
    now = datetime.utcnow()
    archive_name = f"{now.strftime('%Y-%m-%d_%H%M%S')}_extract"
    archive_path = RUNS_DIR / archive_name
    shutil.move(str(ACTIVE_DIR), str(archive_path))
    logger.info("run_archiver_archived", archive_dir=str(archive_path))
    return archive_path


class RunArchiver:
    """Manages the active extraction session folder.

    Batch runs update the active folder in-place; only a full reset (is_fresh_run=True)
    archives the previous session first.
    """

    def __init__(self, run_dir: Path, run_type: str, started_at: datetime) -> None:
        self.run_dir = run_dir
        self.run_type = run_type
        self.started_at = started_at

    @classmethod
    def start(cls, run_type: str = "extract", is_fresh_run: bool = False) -> RunArchiver:
        """Open (or create) the active run folder.

        Args:
            run_type: Label for the run (e.g., "extract", "re-extract").
            is_fresh_run: When True, archive the current active folder first so
                the CSV reflects only the new run's extractions. Pass True on
                full resets; False on batch/partial runs.
        """
        # Use naive UTC datetime to match Postgres func.now() which returns
        # naive timestamps. Using timezone-aware datetimes here causes the
        # CSV export query (WHERE created_at >= started_at) to return 0 rows
        # because SQLAlchemy can't compare aware vs naive datetimes correctly.
        now = datetime.utcnow()

        if is_fresh_run and ACTIVE_DIR.exists():
            archive_active_folder()

        ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("run_archiver_started", run_dir=str(ACTIVE_DIR), fresh=is_fresh_run)
        return cls(run_dir=ACTIVE_DIR, run_type=run_type, started_at=now)

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
        finished_at = datetime.utcnow()

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

        # 3. Export low-confidence (Tier C/D) extractions for review
        self._export_low_confidence(db)

        # 4. Write agent stats if available from the monitor
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
        """Export extractions to the active folder CSV.

        Always exports ALL extractions currently in the DB so the CSV reflects
        the complete accumulated state after each batch run. When
        extraction_job_ids are supplied they are used as a fallback filter only
        if the ALL-extractions query would be empty (shouldn't happen in practice).
        """
        from src.db.models import (
            DocumentFamily,
            DocumentVersion,
            Extraction,
            ExtractionJob,
            NormalizedSourceRecord,
            Source,
        )

        _common_cols = (
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
        _joins = (
            lambda q: q
            .join(NormalizedSourceRecord, Extraction.source_record_id == NormalizedSourceRecord.id)
            .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
            .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
            .join(Source, DocumentFamily.source_id == Source.id)
        )

        # Always export the full DB state so batch runs accumulate correctly.
        query = _joins(select(*_common_cols)).order_by(Source.jurisdiction_code, Extraction.id)

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

    def _export_low_confidence(self, db: Session) -> tuple[Path, Path] | None:
        """Export Tier C and D extractions to CSV + JSONL for offline review.

        Written at the end of every run so the files survive an extraction
        reset (the DB rows are gone after reset but the files remain in the
        active session folder).

        Files written:
          active/low_confidence_extractions.csv   — spreadsheet-friendly
          active/low_confidence_extractions.jsonl — one JSON object per line
        """
        from src.db.models import (
            ConfidenceTier,
            DocumentFamily,
            DocumentVersion,
            Extraction,
            NormalizedSourceRecord,
            Source,
        )

        try:
            query = (
                select(
                    Extraction.id,
                    Extraction.extraction_type,
                    Extraction.confidence_score,
                    Extraction.confidence_tier,
                    Extraction.review_status,
                    Extraction.payload,
                    Extraction.evidence_spans,
                    Extraction.created_at,
                    NormalizedSourceRecord.text_content,
                    Source.jurisdiction_code,
                    DocumentFamily.canonical_title,
                    DocumentFamily.metadata_,
                )
                .join(NormalizedSourceRecord, Extraction.source_record_id == NormalizedSourceRecord.id)
                .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
                .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
                .join(Source, DocumentFamily.source_id == Source.id)
                .where(Extraction.confidence_tier.in_([ConfidenceTier.c, ConfidenceTier.d]))
                .order_by(Extraction.confidence_score.asc(), Extraction.id)
            )
            rows = db.execute(query).all()

            if not rows:
                return None

            csv_path = self.run_dir / "low_confidence_extractions.csv"
            jsonl_path = self.run_dir / "low_confidence_extractions.jsonl"

            csv_fieldnames = [
                "extraction_id", "jurisdiction", "law_title",
                "extraction_type", "confidence_score", "confidence_tier",
                "review_status", "passage_text", "evidence_spans",
                "payload_summary", "full_payload_json", "created_at",
            ]

            with (
                open(csv_path, "w", newline="", encoding="utf-8") as csv_f,
                open(jsonl_path, "w", encoding="utf-8") as jsonl_f,
            ):
                writer = csv.DictWriter(csv_f, fieldnames=csv_fieldnames)
                writer.writeheader()

                for row in rows:
                    (
                        ext_id, ext_type, conf_score, conf_tier, review_status,
                        payload, evidence_spans, created_at,
                        passage_text, jurisdiction, law_title, family_meta,
                    ) = row

                    payload_json = json.dumps(payload, default=str) if payload else ""
                    spans = evidence_spans or []
                    spans_str = "; ".join(
                        f"{s.get('text', '')[:60]}{'...' if len(s.get('text',''))>60 else ''}"
                        f" (verified={s.get('verified', False)})"
                        for s in spans
                    ) if spans else ""

                    writer.writerow({
                        "extraction_id": ext_id,
                        "jurisdiction": jurisdiction or "",
                        "law_title": law_title or "",
                        "extraction_type": ext_type.value if hasattr(ext_type, "value") else str(ext_type),
                        "confidence_score": round(float(conf_score), 4) if conf_score else "",
                        "confidence_tier": conf_tier.value if hasattr(conf_tier, "value") else str(conf_tier),
                        "review_status": review_status.value if hasattr(review_status, "value") else str(review_status),
                        "passage_text": (passage_text or "")[:500],
                        "evidence_spans": spans_str[:300],
                        "payload_summary": payload_json[:300] + ("..." if len(payload_json) > 300 else ""),
                        "full_payload_json": payload_json,
                        "created_at": created_at.isoformat() if created_at else "",
                    })

                    obj = {
                        "extraction_id": ext_id,
                        "jurisdiction": jurisdiction or "",
                        "law_title": law_title or "",
                        "bill_number": (family_meta or {}).get("bill_number", ""),
                        "extraction_type": ext_type.value if hasattr(ext_type, "value") else str(ext_type),
                        "confidence_score": round(float(conf_score), 4) if conf_score else None,
                        "confidence_tier": conf_tier.value if hasattr(conf_tier, "value") else str(conf_tier),
                        "review_status": review_status.value if hasattr(review_status, "value") else str(review_status),
                        "passage_text": passage_text or "",
                        "evidence_spans": spans,
                        "payload": payload or {},
                        "created_at": created_at.isoformat() if created_at else None,
                    }
                    jsonl_f.write(json.dumps(obj, default=str) + "\n")

            logger.info(
                "run_archiver_exported_low_confidence",
                csv_path=str(csv_path),
                jsonl_path=str(jsonl_path),
                row_count=len(rows),
            )
            return csv_path, jsonl_path

        except Exception as e:
            logger.warning("run_archiver_low_confidence_export_failed", error=str(e))
            return None

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
