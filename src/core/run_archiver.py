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
        run_id: int | None = None,
    ) -> Path:
        """Write run summary and export extractions to the run folder.

        Args:
            db: SQLAlchemy session for querying extractions.
            summary: The run summary dict from run_extraction().
            extraction_job_ids: Specific job IDs to export. If None, exports
                all extractions created after self.started_at.
            run_id: When provided, a named snapshot is written to
                output/extraction_runs/run_{run_id}/ alongside the active folder.

        Returns:
            Path to the run folder.
        """
        finished_at = datetime.utcnow()
        duration_seconds = round((finished_at - self.started_at).total_seconds(), 1)

        # Every output file this method writes carries this same run's
        # timestamp — either as a structured field (JSON) or a header
        # comment line (CSV/JSONL) — so a file can be identified and compared
        # against another run's output without cross-referencing a second
        # file. Computed once so every output shows the identical stamp.
        run_comparison = self._build_run_comparison_summary(summary, duration_seconds)

        # 1. Write run summary
        run_meta = {
            "run_type": self.run_type,
            "started_at": self.started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": duration_seconds,
            "folder": str(self.run_dir),
            "run_id": run_id,
            **summary,
            "run_comparison_summary": run_comparison,
        }
        summary_path = self.run_dir / "run_summary.json"
        with open(summary_path, "w") as f:
            json.dump(run_meta, f, indent=2, default=str)

        # 2. Export extractions created during this run
        extractions_path = self._export_extractions(db, extraction_job_ids)

        # 2b. Export one CSV per producing agent, alongside the combined CSV
        self._export_by_agent(db)

        # 3. Export bill-level extractions (applicability, enforcement, timeline)
        self._export_bill_level_extractions(db)

        # 4. Export low-confidence (Tier C/D) extractions for review
        self._export_low_confidence(db)

        # 5. Write agent stats if available from the monitor
        self._export_agent_stats(run_comparison)

        # Add folder path to the summary so callers can reference it
        summary["folder"] = str(self.run_dir)

        logger.info(
            "run_archiver_finalized",
            run_dir=str(self.run_dir),
            summary_path=str(summary_path),
            extractions_path=str(extractions_path),
            duration_seconds=run_meta["duration_seconds"],
        )

        # 6. Write a named per-run snapshot so each run's output is preserved
        #    even when the active folder is overwritten by later runs.
        if run_id is not None:
            self._write_run_snapshot(run_id, run_meta)

        return self.run_dir

    def _write_run_snapshot(self, run_id: int, run_meta: dict[str, Any]) -> None:
        """Copy active folder contents (files + subfolders) into
        output/extraction_runs/run_{run_id}/.
        """
        snapshot_dir = RUNS_DIR / f"run_{run_id}"
        try:
            shutil.copytree(self.run_dir, snapshot_dir, dirs_exist_ok=True)
            logger.info(
                "run_archiver_snapshot_written",
                snapshot_dir=str(snapshot_dir),
                run_id=run_id,
            )
        except Exception as e:
            logger.warning("run_archiver_snapshot_failed", run_id=run_id, error=str(e))

    def _run_header_line(self) -> str:
        """Timestamped comment line prepended to every CSV/JSONL export.

        Every output file in the run folder carries this same line so a
        file can be identified — and compared against a different run's
        output — without opening a second file to find out when it was
        produced. self.started_at is the one timestamp all outputs in this
        finalize() call share, matching run_summary.json's `started_at` and
        `run_comparison_summary.run_timestamp` exactly.
        """
        return (
            f"# RUN: {self.started_at.strftime('%Y-%m-%d %H:%M:%S')} UTC "
            f"| type={self.run_type} "
            f"| full run comparison stats: run_summary.json -> run_comparison_summary\n"
        )

    def _build_run_comparison_summary(
        self, summary: dict[str, Any], duration_seconds: float
    ) -> dict[str, Any]:
        """One consolidated block answering "how does this run compare to
        the next one" — written into run_summary.json and echoed into
        agent_stats.json, so neither file requires the other to answer it.

        Combines the live ExtractionMonitor snapshot (per-agent call/error/
        duration counters — reset at the start of run_extraction(), so this
        reflects only the run just finished) with the run-level `summary`
        dict already assembled by run_extraction() (conservation, token
        usage, confidence tiers aren't tracked there — pulled from the
        monitor instead).
        """
        from src.core.extraction_monitor import get_monitor
        from src.core.llm_rate_telemetry import get_llm_rate_telemetry

        snapshot = get_monitor().snapshot(recent_count=0)
        agent_stats = snapshot.agent_stats  # {name: {calls, errors, avg_duration_ms, ...}}

        total_calls = sum(a["calls"] for a in agent_stats.values())
        total_duration_ms = sum(a.get("total_duration_ms", 0) for a in agent_stats.values())
        avg_duration_ms_overall = (
            round(total_duration_ms / total_calls) if total_calls else 0
        )

        total_extractions = summary.get("total_extractions", 0)
        extractions_per_minute = (
            round(total_extractions / (duration_seconds / 60), 2)
            if duration_seconds > 0 else 0.0
        )

        return {
            "run_timestamp": self.started_at.isoformat() + "Z",
            "run_type": self.run_type,
            "total_duration_seconds": duration_seconds,
            "total_records_processed": summary.get("records_processed", 0),
            "total_extractions": total_extractions,
            "extractions_per_minute": extractions_per_minute,
            "failures": {
                "total_records_failed": summary.get("records_failed", 0),
                "circuit_breaker_tripped": summary.get("circuit_breaker_tripped", False),
                "circuit_breaker_detail": summary.get("circuit_breaker_detail"),
                "total_agent_errors": snapshot.total_errors,
                "overall_agent_failure_rate": round(snapshot.failure_rate, 3),
                "per_agent_errors": {
                    name: stats["errors"] for name, stats in agent_stats.items()
                },
            },
            "avg_duration_ms_overall": avg_duration_ms_overall,
            "avg_duration_ms_per_agent": {
                name: stats["avg_duration_ms"] for name, stats in agent_stats.items()
            },
            "confidence_tier_distribution": snapshot.confidence_tiers,
            "token_usage_total": summary.get("token_usage", {}).get("total_tokens", 0),
            "conservation_ok": summary.get("conservation", {}).get("conserved"),
            # NIM-0a: per-model request-rate/429/token telemetry from the
            # llm_provider.py chokepoint (see llm_rate_telemetry.py) —
            # NVIDIA exposes no balance or usage API, so this is the run's
            # own record of how close it came to its rate-limit ceiling,
            # keyed by model so a shared-model skew is visible run over run.
            "llm_throttle_telemetry": get_llm_rate_telemetry().snapshot(),
        }

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
            NormalizedSourceRecord,
            Source,
        )

        _common_cols = (
            Extraction.id,               # 0
            Extraction.extraction_type,  # 1
            Extraction.confidence_score, # 2
            Extraction.confidence_tier,  # 3
            Extraction.model_id,         # 4
            Extraction.payload,          # 5
            Extraction.evidence_spans,   # 6
            Extraction.created_at,       # 7
            NormalizedSourceRecord.section_path,   # 8
            NormalizedSourceRecord.text_content,   # 9  (not written; kept for query consistency)
            Source.jurisdiction_code,              # 10
            DocumentFamily.short_cite,             # 11
            DocumentFamily.canonical_title,        # 12
            DocumentVersion.effective_date,        # 13
            DocumentVersion.temporal_status,       # 14
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
            "effective_date", "law_status", "verified_span_count", "total_span_count",
        ]

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            f.write(self._run_header_line())
            f.write(
                "# DISCLAIMER: Informational only — not legal advice. "
                "Produced by an AI extraction pipeline; may be incomplete, outdated, or incorrect. "
                "Laws change — always verify against current official text. "
                "Consult a licensed attorney before relying on any regulatory content.\n"
            )
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                spans = row[6] or []
                verified = sum(1 for s in spans if isinstance(s, dict) and s.get("verified") is True)
                ts = row[14]
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
                    "evidence_spans_json": json.dumps(spans, default=str),
                    "created_at": row[7].isoformat() if row[7] else "",
                    "effective_date": row[13].isoformat() if row[13] else "",
                    "law_status": ts.value if hasattr(ts, "value") else (str(ts) if ts else ""),
                    "verified_span_count": verified,
                    "total_span_count": len(spans),
                })

        logger.info("run_archiver_exported_csv", path=str(csv_path), row_count=len(rows))
        return csv_path

    def _export_by_agent(self, db: Session) -> Path | None:
        """Export one CSV per producing agent into <run_dir>/by_agent/<agent>.csv.

        Reuses the same full-DB-state query as _export_extractions but adds
        agent_name and splits output by it. Legacy rows without agent_name
        (extracted before the Phase A migration) are written to
        by_agent/_unattributed.csv rather than silently dropped.
        """
        from src.db.models import (
            DocumentFamily,
            DocumentVersion,
            Extraction,
            NormalizedSourceRecord,
            Source,
        )

        _cols = (
            Extraction.id,               # 0
            Extraction.agent_name,       # 1
            Extraction.extraction_type,  # 2
            Extraction.confidence_score, # 3
            Extraction.confidence_tier,  # 4
            Extraction.model_id,         # 5
            Extraction.payload,          # 6
            Extraction.evidence_spans,   # 7
            Extraction.created_at,       # 8
            NormalizedSourceRecord.section_path,   # 9
            Source.jurisdiction_code,              # 10
            DocumentFamily.short_cite,              # 11
            DocumentFamily.canonical_title,        # 12
            DocumentFamily.canonical_key,           # 13
        )
        query = (
            select(*_cols)
            .join(NormalizedSourceRecord, Extraction.source_record_id == NormalizedSourceRecord.id)
            .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
            .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
            .join(Source, DocumentFamily.source_id == Source.id)
            .order_by(Extraction.agent_name, Source.jurisdiction_code, Extraction.id)
        )

        try:
            rows = db.execute(query).all()
        except Exception as e:
            # agent_name column may not exist yet if the migration hasn't run
            logger.warning("run_archiver_by_agent_export_skipped", error=str(e))
            return None

        if not rows:
            return None

        by_agent_dir = self.run_dir / "by_agent"
        by_agent_dir.mkdir(exist_ok=True)

        fieldnames = [
            "extraction_id", "agent_name", "jurisdiction", "law", "title",
            "canonical_key", "section", "extraction_type",
            "confidence_score", "confidence_tier", "model_id",
            "verified_span_count", "total_span_count",
            "payload_json", "evidence_spans_json", "created_at",
        ]
        disclaimer = (
            "# DISCLAIMER: Informational only — not legal advice. "
            "Produced by an AI extraction pipeline; may be incomplete, outdated, or incorrect. "
            "Laws change — always verify against current official text. "
            "Consult a licensed attorney before relying on any regulatory content.\n"
        )

        grouped: dict[str, list] = {}
        for row in rows:
            grouped.setdefault(row[1] or "_unattributed", []).append(row)

        written = 0
        for agent_name, agent_rows in grouped.items():
            csv_path = by_agent_dir / f"{agent_name}.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                f.write(self._run_header_line())
                f.write(disclaimer)
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for row in agent_rows:
                    spans = row[7] or []
                    verified = sum(
                        1 for s in spans if isinstance(s, dict) and s.get("verified") is True
                    )
                    ext_type_val = row[2].value if hasattr(row[2], "value") else str(row[2])
                    tier_val = row[4].value if hasattr(row[4], "value") else str(row[4])
                    writer.writerow({
                        "extraction_id": row[0],
                        "agent_name": row[1] or "",
                        "jurisdiction": row[10],
                        "law": row[11],
                        "title": row[12],
                        "canonical_key": row[13] or "",
                        "section": row[9],
                        "extraction_type": ext_type_val,
                        "confidence_score": round(float(row[3]), 4) if row[3] else "",
                        "confidence_tier": tier_val,
                        "model_id": row[5] or "",
                        "verified_span_count": verified,
                        "total_span_count": len(spans),
                        "payload_json": json.dumps(row[6], default=str) if row[6] else "",
                        "evidence_spans_json": json.dumps(spans, default=str),
                        "created_at": row[8].isoformat() if row[8] else "",
                    })
            written += 1

        logger.info(
            "run_archiver_exported_by_agent",
            path=str(by_agent_dir),
            agent_count=written,
            row_count=len(rows),
        )
        return by_agent_dir

    def _export_bill_level_extractions(self, db: Session) -> Path | None:
        """Export bill-level extractions (applicability, enforcement, timeline) to CSV.

        These rows live in bill_level_extractions — a separate table from the
        passage-level extractions table — and were previously absent from the run
        export. Written to bill_level_extractions.csv alongside extractions.csv.
        """
        try:
            from src.db.models import (
                BillLevelExtraction,
                DocumentFamily,
                DocumentVersion,
                Source,
            )

            query = (
                select(
                    BillLevelExtraction.id,
                    BillLevelExtraction.agent_name,
                    BillLevelExtraction.model_id,
                    BillLevelExtraction.input_tokens,
                    BillLevelExtraction.output_tokens,
                    BillLevelExtraction.truncated,
                    BillLevelExtraction.review_status,
                    BillLevelExtraction.payload,
                    BillLevelExtraction.created_at,
                    Source.jurisdiction_code,
                    DocumentFamily.short_cite,
                    DocumentFamily.canonical_title,
                )
                .join(DocumentVersion, BillLevelExtraction.document_version_id == DocumentVersion.id)
                .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
                .join(Source, DocumentFamily.source_id == Source.id)
                .order_by(Source.jurisdiction_code, BillLevelExtraction.id)
            )
            rows = db.execute(query).all()

            if not rows:
                return None

            csv_path = self.run_dir / "bill_level_extractions.csv"
            fieldnames = [
                "id", "agent_name", "model_id", "input_tokens", "output_tokens",
                "truncated", "review_status", "jurisdiction", "law", "title",
                "payload_json", "created_at",
            ]
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                f.write(self._run_header_line())
                f.write(
                    "# DISCLAIMER: Informational only — not legal advice. "
                    "AI-extracted; verify against current official statutory text.\n"
                )
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    (
                        rid, agent_name, model_id, in_tok, out_tok, truncated,
                        review_status, payload, created_at,
                        jurisdiction, law, title,
                    ) = row
                    writer.writerow({
                        "id": rid,
                        "agent_name": agent_name,
                        "model_id": model_id or "",
                        "input_tokens": in_tok or 0,
                        "output_tokens": out_tok or 0,
                        "truncated": truncated,
                        "review_status": review_status.value if hasattr(review_status, "value") else str(review_status),
                        "jurisdiction": jurisdiction or "",
                        "law": law or "",
                        "title": title or "",
                        "payload_json": json.dumps(payload, default=str) if payload else "",
                        "created_at": created_at.isoformat() if created_at else "",
                    })

            logger.info(
                "run_archiver_exported_bill_level",
                path=str(csv_path),
                row_count=len(rows),
            )
            return csv_path

        except Exception as e:
            logger.warning("run_archiver_bill_level_export_failed", error=str(e))
            return None

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
                .where(Extraction.confidence_tier.in_([ConfidenceTier.C, ConfidenceTier.D]))
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

            _disclaimer = (
                "# DISCLAIMER: Informational only — not legal advice. "
                "AI-extracted; verify against current official statutory text.\n"
            )
            with (
                open(csv_path, "w", newline="", encoding="utf-8") as csv_f,
                open(jsonl_path, "w", encoding="utf-8") as jsonl_f,
            ):
                csv_f.write(self._run_header_line())
                csv_f.write(_disclaimer)
                jsonl_f.write(
                    json.dumps({
                        "_record_type": "disclaimer",
                        "run_timestamp": self.started_at.isoformat() + "Z",
                        "run_type": self.run_type,
                        "text": "Informational only — not legal advice. "
                                "AI-extracted; verify against current official statutory text.",
                    })
                    + "\n"
                )
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

    def _export_agent_stats(self, run_comparison: dict[str, Any] | None = None) -> Path | None:
        """Export agent performance stats from the extraction monitor.

        Args:
            run_comparison: The same run_comparison_summary block written to
                run_summary.json. Echoed here (under "run_summary") so this
                file also carries its own run timestamp / failures / avg
                duration and doesn't require opening run_summary.json to
                answer "when was this, and how did it do."
        """
        try:
            from src.core.extraction_monitor import get_monitor
            monitor = get_monitor()
            snapshot = monitor.snapshot(recent_count=0)
            stats_dict = snapshot.to_dict()

            # Document the scope so consumers understand the difference from
            # run_summary.json token_usage (which tracks result tokens only).
            stats_dict["scope"] = "all_call_attempts_including_adaptive_retries"
            stats_dict["scope_note"] = (
                "token totals here include every LLM call attempt — "
                "successful, abstention, error, and internal adaptive retries; "
                "compare to run_summary.json token_usage which records result "
                "tokens only (excludes retries)"
            )
            if run_comparison is not None:
                stats_dict["run_summary"] = run_comparison

            stats_path = self.run_dir / "agent_stats.json"
            with open(stats_path, "w") as f:
                json.dump(stats_dict, f, indent=2, default=str)
            return stats_path
        except Exception as e:
            logger.warning("run_archiver_agent_stats_failed", error=str(e))
            return None
