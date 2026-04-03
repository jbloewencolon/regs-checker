"""Local file ingestion — seeds document families from CSV and ingests pre-fetched files.

Replaces the old URL-fetching pipeline with a deterministic, local-file-based
approach. Source documents come from the data/ and output/ directories:

  - data/fact_laws.csv           → Document family metadata (243 laws)
  - data/dim_jurisdictions.csv   → Jurisdiction lookup
  - output/law_fulltext_report.csv → Maps canonical_law_id → source URL + filename
  - output/law_sources/          → Original source files (HTML, PDF, TXT)
  - output/law_texts/            → Pre-extracted plain text fallback

Usage:
    # Seed all 243 laws from CSV + ingest local files:
    python -m src.scripts.seed_pipeline --mode seed-local

    # Seed only (create families, don't ingest yet):
    python -m src.scripts.seed_pipeline --mode seed-local --seed-only

    # Ingest with a limit (useful for testing):
    python -m src.scripts.seed_pipeline --mode seed-local --limit 5
"""

from __future__ import annotations

import csv
import hashlib
import mimetypes
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import structlog

from src.db.models import (
    DocumentFamily,
    DocumentVersion,
    IngestionJob,
    IngestionStatus,
    LegalEvent,
    LegalEventType,
    RawArtifact,
    Source,
    TemporalStatus,
)

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Paths (relative to repo root)
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _ROOT / "data"
_OUTPUT_DIR = _ROOT / "output"
_LAW_SOURCES_DIR = _OUTPUT_DIR / "law_sources"
_LAW_TEXTS_DIR = _OUTPUT_DIR / "law_texts"

_FACT_LAWS_CSV = _DATA_DIR / "fact_laws.csv"
_DIM_JURISDICTIONS_CSV = _DATA_DIR / "dim_jurisdictions.csv"
_FULLTEXT_REPORT_CSV = _OUTPUT_DIR / "law_fulltext_report.csv"


# ---------------------------------------------------------------------------
# Jurisdiction + status helpers
# ---------------------------------------------------------------------------

def _load_jurisdictions() -> dict[int, dict]:
    """Load dim_jurisdictions.csv into {jurisdiction_id: {name, state_abbrev}}."""
    result = {}
    with open(_DIM_JURISDICTIONS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            result[int(row["jurisdiction_id"])] = {
                "name": row["name"],
                "state_abbrev": row["state_abbrev"],
            }
    return result


def _load_fulltext_report() -> dict[str, dict]:
    """Load law_fulltext_report.csv into {canonical_law_id: {url, filename}}."""
    result = {}
    with open(_FULLTEXT_REPORT_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            canonical_id = row.get("Canonical Law ID", "").strip()
            if canonical_id:
                result[canonical_id] = {
                    "url": row.get("url", "").strip(),
                    "filename": row.get("filename", "").strip(),
                }
    return result


def _parse_effective_date(date_str: str) -> date | None:
    """Parse effective date from various CSV formats."""
    if not date_str or not date_str.strip():
        return None
    date_str = date_str.strip()
    if date_str.lower() in ("tbd", "n/a", "pending", "varies", ""):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _temporal_status_from_csv(status_id: str) -> TemporalStatus:
    """Map dim_legislative_statuses status_id to TemporalStatus enum."""
    mapping = {
        "1": TemporalStatus.active,          # Active
        "2": TemporalStatus.enacted,         # Enacted
        "3": TemporalStatus.dead,            # Failed/Dead
        "4": TemporalStatus.enacted,         # Signed
    }
    return mapping.get(str(status_id).strip(), TemporalStatus.enacted)


# ---------------------------------------------------------------------------
# Phase 1: Seed document families from fact_laws.csv
# ---------------------------------------------------------------------------

def seed_from_csv(
    db,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Create Source, DocumentFamily, DocumentVersion, and IngestionJob rows
    from data/fact_laws.csv.

    Upserts: skips families that already have the same canonical_law_id in
    their metadata. Returns summary dict.
    """
    jurisdictions = _load_jurisdictions()
    fulltext_report = _load_fulltext_report()

    rows = []
    with open(_FACT_LAWS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    stats = {"created": 0, "skipped": 0, "total": len(rows), "errors": 0}

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    _log(f"Seeding {len(rows)} laws from {_FACT_LAWS_CSV.name}")

    for row in rows:
        canonical_law_id = row.get("canonical_law_id", "").strip()
        if not canonical_law_id:
            stats["errors"] += 1
            continue

        # Check if family already exists (by canonical_law_id in metadata)
        existing = (
            db.query(DocumentFamily)
            .filter(DocumentFamily.metadata_["canonical_law_id"].astext == canonical_law_id)
            .first()
        )
        if existing:
            stats["skipped"] += 1
            continue

        # Resolve jurisdiction
        jurisdiction_id = row.get("jurisdiction_id", "").strip()
        jur = jurisdictions.get(int(jurisdiction_id)) if jurisdiction_id else None
        state_abbrev = jur["state_abbrev"] if jur else ""
        state_name = jur["name"] if jur else "Unknown"

        # Find or create Source for this jurisdiction
        source = db.query(Source).filter_by(
            jurisdiction_code=state_abbrev,
        ).first()
        if not source:
            source = Source(
                jurisdiction_code=state_abbrev,
                jurisdiction_name=state_name,
                source_type="state_statute",
                base_url="",
                connector_id="local_file",
            )
            db.add(source)
            db.flush()

        # Resolve source URL from fulltext report
        report_entry = fulltext_report.get(canonical_law_id, {})
        source_url = report_entry.get("url", row.get("source_url", ""))

        title = row.get("title", "").strip()
        bill_number = row.get("bill_number", "").strip()

        family = DocumentFamily(
            source_id=source.id,
            canonical_title=f"{state_name} - {title}" if title else canonical_law_id,
            short_cite=bill_number or canonical_law_id,
            subject_area="artificial_intelligence",
            primary_source_url=source_url,
            metadata_={
                "canonical_law_id": canonical_law_id,
                "bill_number": bill_number,
                "ai_scope_summary": row.get("ai_scope_summary", ""),
                "key_requirements": row.get("key_requirements_raw", ""),
                "enforcement_penalties": row.get("enforcement_penalties", ""),
                "source_tracker": "orrick" if row.get("source_id") == "1" else "iapp",
            },
        )
        db.add(family)
        db.flush()

        # Document Version
        effective_date = _parse_effective_date(row.get("effective_date", ""))
        status_id = row.get("status_id", "")
        temporal_status = _temporal_status_from_csv(status_id)

        version = DocumentVersion(
            family_id=family.id,
            version_label="Current",
            temporal_status=temporal_status,
            effective_date=effective_date,
            metadata_={"seeded_from": "fact_laws.csv"},
        )
        db.add(version)
        db.flush()

        # Legal event for effective date
        if effective_date:
            db.add(LegalEvent(
                document_version_id=version.id,
                event_type=LegalEventType.effective,
                event_date=effective_date,
                description=f"Effective date for {title or canonical_law_id}",
                authority=state_name,
            ))

        # Ingestion Job — points to local file, not a URL
        local_filename = _resolve_local_file(canonical_law_id)
        job = IngestionJob(
            document_version_id=version.id,
            status=IngestionStatus.pending,
            fetch_url=source_url or None,
            metadata_={
                "canonical_law_id": canonical_law_id,
                "local_file": str(local_filename) if local_filename else None,
                "ingest_mode": "local",
            },
        )
        db.add(job)
        db.flush()

        stats["created"] += 1

    db.commit()
    _log(
        f"Seeding complete: {stats['created']} created, "
        f"{stats['skipped']} skipped (already exist), "
        f"{stats['errors']} errors"
    )
    return stats


# ---------------------------------------------------------------------------
# Phase 2: Local file ingestion — read files from disk, store + parse
# ---------------------------------------------------------------------------

def _resolve_local_file(canonical_law_id: str) -> Path | None:
    """Find the best local source file for a given canonical_law_id.

    Resolution order:
      1. output/law_sources/{id}.html
      2. output/law_sources/{id}.pdf
      3. output/law_sources/{id}.txt
      4. output/law_texts/{id}.txt  (pre-extracted plain text fallback)
    """
    for ext in (".html", ".pdf", ".txt"):
        path = _LAW_SOURCES_DIR / f"{canonical_law_id}{ext}"
        if path.exists():
            return path

    txt_path = _LAW_TEXTS_DIR / f"{canonical_law_id}.txt"
    if txt_path.exists():
        return txt_path

    return None


def _detect_content_type(path: Path) -> str:
    """Detect content type from file extension."""
    suffix = path.suffix.lower()
    mapping = {
        ".html": "text/html",
        ".htm": "text/html",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
    }
    return mapping.get(suffix, mimetypes.guess_type(str(path))[0] or "text/plain")


def ingest_local_files(
    db,
    limit: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Ingest local source files for all pending ingestion jobs with ingest_mode=local.

    For each pending job:
      1. Read the local file from output/law_sources/ or output/law_texts/
      2. Store as a RawArtifact in S3/MinIO (content-addressed)
      3. Parse into normalized_source_records (passages)
      4. Mark job as completed

    Returns summary dict.
    """
    from sqlalchemy import select
    from src.ingestion.parser import parse_and_normalize

    query = select(IngestionJob).where(
        IngestionJob.status.in_([IngestionStatus.pending, IngestionStatus.fetched]),
    )
    if limit:
        query = query.limit(limit)

    pending_jobs = db.scalars(query).all()

    # Filter to local-mode jobs
    local_jobs = [
        j for j in pending_jobs
        if (j.metadata_ or {}).get("ingest_mode") == "local"
    ]

    summary = {
        "total_pending": len(local_jobs),
        "completed": 0,
        "failed": 0,
        "skipped_no_file": 0,
        "total_passages": 0,
    }

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    if not local_jobs:
        _log("No pending local ingestion jobs found.")
        return summary

    _log(f"Found {len(local_jobs)} pending local ingestion jobs")

    for i, job in enumerate(local_jobs, 1):
        dv = job.document_version
        family = dv.family if dv else None
        canonical_law_id = (job.metadata_ or {}).get("canonical_law_id", "")
        label = family.canonical_title if family else canonical_law_id

        _log(f"\n[{i}/{len(local_jobs)}] {label}")

        # Resolve local file
        local_file_str = (job.metadata_ or {}).get("local_file")
        local_file = Path(local_file_str) if local_file_str else None

        if not local_file or not local_file.exists():
            # Try resolving again (file may have been added after seeding)
            local_file = _resolve_local_file(canonical_law_id)

        if not local_file or not local_file.exists():
            _log(f"  No local file found for {canonical_law_id} — skipping")
            job.status = IngestionStatus.failed
            job.error_message = f"No local source file found for {canonical_law_id}"
            db.commit()
            summary["skipped_no_file"] += 1
            summary["failed"] += 1
            continue

        try:
            # Read file content
            content_bytes = local_file.read_bytes()
            content_type = _detect_content_type(local_file)

            # Content-addressable storage
            sha256 = hashlib.sha256(content_bytes).hexdigest()

            # Check for existing artifact (dedup)
            existing_artifact = db.query(RawArtifact).filter_by(sha256_hash=sha256).first()
            if existing_artifact:
                artifact = existing_artifact
                _log(f"  Artifact deduplicated: sha256={sha256[:12]}")
            else:
                # Store in S3/MinIO
                source_code = family.source.jurisdiction_code if family and family.source else "XX"
                s3_key = f"raw/{source_code}/{sha256}"
                _upload_to_s3(s3_key, content_bytes, content_type)

                artifact = RawArtifact(
                    document_version_id=job.document_version_id,
                    sha256_hash=sha256,
                    s3_key=s3_key,
                    content_type=content_type,
                    size_bytes=len(content_bytes),
                    is_primary=True,
                )
                db.add(artifact)
                db.flush()

            _log(
                f"  Stored: {content_type}, {len(content_bytes):,} bytes, "
                f"sha256={sha256[:12]}"
            )

            # Parse into passages
            job.status = IngestionStatus.parsing
            job.parse_started_at = datetime.utcnow()
            db.commit()

            records = parse_and_normalize(db, job, artifact)

            job.status = IngestionStatus.completed
            job.parse_completed_at = datetime.utcnow()
            job.parse_quality_score = _compute_parse_quality(records)
            db.commit()

            _log(f"  Parsed into {len(records)} passages")
            summary["completed"] += 1
            summary["total_passages"] += len(records)

        except Exception as e:
            job.status = IngestionStatus.failed
            job.error_message = str(e)[:2000]
            db.commit()
            _log(f"  FAILED: {e}")
            summary["failed"] += 1

    return summary


def _compute_parse_quality(records) -> float:
    """Simple parse quality heuristic."""
    if not records:
        return 0.0
    scores = []
    for r in records:
        text = r.text_content
        score = 1.0
        if len(text) < 20:
            score *= 0.5
        if len(text) > 5000:
            score *= 0.8
        scores.append(score)
    return sum(scores) / len(scores)


def _upload_to_s3(key: str, content: bytes, content_type: str) -> None:
    """Upload content to S3/MinIO."""
    import boto3
    from src.core.config import settings

    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )
    s3.put_object(
        Bucket=settings.s3_bucket_raw,
        Key=key,
        Body=content,
        ContentType=content_type,
    )


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------

def run_local_ingest(
    db,
    limit: int | None = None,
    seed_only: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Full local ingestion pipeline: seed from CSV + ingest local files.

    Args:
        db: SQLAlchemy session
        limit: Max number of jobs to ingest (None = all)
        seed_only: If True, only seed families without ingesting
        on_progress: Optional callback(message: str)

    Returns:
        Combined summary dict.
    """
    # Phase 1: Seed
    seed_stats = seed_from_csv(db, on_progress=on_progress)

    if seed_only:
        return {**seed_stats, "ingest_skipped": True}

    # Phase 2: Ingest
    ingest_stats = ingest_local_files(db, limit=limit, on_progress=on_progress)

    return {**seed_stats, **ingest_stats}
