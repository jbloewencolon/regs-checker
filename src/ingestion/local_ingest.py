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
# Regulatory category derivation
# ---------------------------------------------------------------------------

def _normalize_source_url(url: str) -> str:
    """Strip known wrapper prefixes so primary_source_url holds the canonical URL.

    Removes the Jina AI proxy prefix (``https://r.jina.ai/<url>``), which is
    stored verbatim in some tracker exports and breaks consumers doing exact-URL
    matching against .gov sources.  Orrick PDF mirrors have no programmatic fix
    and are left unchanged for manual curation.
    """
    if not url:
        return url
    jina_prefix = "https://r.jina.ai/"
    if url.startswith(jina_prefix):
        url = url[len(jina_prefix):]
    return url


def _derive_regulatory_category(ai_scope_summary: str, title: str) -> str:
    """Map ai_scope_summary / title text to a human-readable regulatory category.

    Used as display metadata — does not affect extraction logic.
    """
    combined = (ai_scope_summary + " " + title).lower()

    if any(k in combined for k in ("intimate image", "csam", "deepfake", "synthetic content",
                                    "likeness", "pornograph", "nonconsensual")):
        return "synthetic_content"
    if any(k in combined for k in ("political", "election", "campaign", "ballot")):
        return "political_advertising"
    if any(k in combined for k in ("automated decision", "algorithmic", "automated-decision",
                                    "automated making", "admt")):
        return "automated_decision"
    if any(k in combined for k in ("consumer privacy", "data privacy", "personal data",
                                    "ccpa", "gdpr")):
        return "data_privacy"
    if any(k in combined for k in ("government", "public sector", "state agency",
                                    "law enforcement")):
        return "government_ai"
    if any(k in combined for k in ("employment", "workplace", "worker", "hiring",
                                    "employer")):
        return "employment"
    if any(k in combined for k in ("healthcare", "health care", "medical", "clinical",
                                    "patient")):
        return "healthcare"
    if any(k in combined for k in ("education", "student", "school", "academic")):
        return "education"
    if any(k in combined for k in ("frontier", "foundation model", "general purpose",
                                    "large language")):
        return "frontier_models"
    if any(k in combined for k in ("social media", "social network", "platform")):
        return "social_media"
    if any(k in combined for k in ("insurance",)):
        return "insurance"
    if any(k in combined for k in ("comprehensive", "all ai", "general ai")):
        return "comprehensive_ai"
    if any(k in combined for k in ("transparency", "disclosure", "notice")):
        return "transparency"
    return "general_ai"


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

    stats = {"created": 0, "skipped": 0, "repaired": 0, "total": len(rows), "errors": 0}

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    _log(f"Seeding {len(rows)} laws from {_FACT_LAWS_CSV.name}")

    for row in rows:
        canonical_law_id = row.get("canonical_law_id", "").strip()
        if not canonical_law_id:
            stats["errors"] += 1
            continue

        # Check if family already exists (by canonical_key column — DI-1).
        # Falls back to the JSONB path for rows seeded before the migration.
        existing = (
            db.query(DocumentFamily)
            .filter(DocumentFamily.canonical_key == canonical_law_id)
            .first()
        ) or (
            db.query(DocumentFamily)
            .filter(DocumentFamily.metadata_["canonical_law_id"].astext == canonical_law_id)
            .first()
        )
        if existing:
            # Back-fill canonical_key if this family was seeded before migration (DI-1).
            if existing.canonical_key is None:
                existing.canonical_key = canonical_law_id
                db.flush()
            # Self-heal: a partial reset can delete ingestion_jobs while leaving
            # the family + version behind (FK-blocked deletes on the parent rows).
            # Without a job, neither "Parse Documents" nor ingest will ever touch
            # this law again, so re-seeding looks like a no-op. Recreate a pending
            # job so re-seeding repairs the broken state.
            version = (
                db.query(DocumentVersion)
                .filter_by(family_id=existing.id)
                .order_by(DocumentVersion.id.desc())
                .first()
            )
            if version is None:
                # No version to attach a job to — can't safely repair without
                # rebuilding the family; leave it as already-seeded.
                stats["skipped"] += 1
                continue
            has_job = (
                db.query(IngestionJob)
                .filter_by(document_version_id=version.id)
                .first()
                is not None
            )
            if has_job:
                stats["skipped"] += 1
                continue
            # Version exists but has no ingestion job → recreate a pending one.
            report_entry = fulltext_report.get(canonical_law_id, {})
            source_url = report_entry.get("url", row.get("source_url", ""))
            local_filename = _resolve_local_file(canonical_law_id)
            db.add(IngestionJob(
                document_version_id=version.id,
                status=IngestionStatus.pending,
                fetch_url=source_url or None,
                metadata_={
                    "canonical_law_id": canonical_law_id,
                    "local_file": str(local_filename) if local_filename else None,
                    "ingest_mode": "local",
                },
            ))
            db.flush()
            stats["repaired"] += 1
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

        # Resolve source URL from fulltext report; strip known proxy wrappers (DI-3).
        report_entry = fulltext_report.get(canonical_law_id, {})
        source_url = _normalize_source_url(
            report_entry.get("url", row.get("source_url", ""))
        )

        title = row.get("title", "").strip()
        bill_number = row.get("bill_number", "").strip()
        ai_scope_summary = row.get("ai_scope_summary", "").strip()

        # Build a disambiguated title: append bill number when present so that
        # two rows with the same statute title (e.g. "California Consumer Privacy
        # Act") but different bill numbers are clearly distinct in the UI.
        if title and bill_number:
            canonical_title = f"{state_name} - {title} ({bill_number})"
        elif title:
            canonical_title = f"{state_name} - {title}"
        else:
            canonical_title = canonical_law_id

        regulatory_category = _derive_regulatory_category(ai_scope_summary, title)

        # Combine both Orrick columns so no data is lost when only one is
        # populated (the CSV alternates between the two columns depending on
        # how Orrick formatted each row).
        key_req = row.get("key_requirements_raw", "").strip()
        enforcement = row.get("enforcement_penalties", "").strip()
        orrick_summary = " ".join(p for p in [key_req, enforcement] if p)

        family = DocumentFamily(
            source_id=source.id,
            canonical_key=canonical_law_id,
            canonical_title=canonical_title,
            short_cite=bill_number or canonical_law_id,
            subject_area="artificial_intelligence",
            primary_source_url=source_url,
            metadata_={
                "canonical_law_id": canonical_law_id,
                "bill_number": bill_number,
                "ai_scope_summary": ai_scope_summary,
                "key_requirements": key_req,
                "enforcement_penalties": enforcement,
                "orrick_summary": orrick_summary,
                "source_tracker": "orrick" if row.get("source_id") == "1" else "iapp",
                "iapp_scope": row.get("iapp_scope", ""),
                "iapp_section": row.get("iapp_section", ""),
                "regulatory_category": regulatory_category,
            },
        )
        db.add(family)
        db.flush()

        # Document Version
        effective_date = _parse_effective_date(row.get("effective_date", ""))
        status_id = row.get("status_id", "")
        temporal_status = _temporal_status_from_csv(status_id)
        session_year = effective_date.year if effective_date else None

        version = DocumentVersion(
            family_id=family.id,
            version_label="Current",
            temporal_status=temporal_status,
            effective_date=effective_date,
            bill_number=bill_number or None,
            session_year=session_year,
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
        f"{stats['repaired']} repaired (missing job re-created), "
        f"{stats['skipped']} skipped (already exist), "
        f"{stats['errors']} errors"
    )
    return stats


# ---------------------------------------------------------------------------
# Phase 2: Local file ingestion — read files from disk, store + parse
# ---------------------------------------------------------------------------

def _resolve_local_file(canonical_law_id: str) -> Path | None:
    """Find the best local source file for a given canonical_law_id.

    Resolution order — prefer pre-extracted plain text (law_texts/) since
    all source files have already been converted to .txt.  The law_sources/
    folder contains raw HTML/PDF originals that are no longer needed.
    """
    # 1. Pre-extracted plain text (primary)
    txt_path = _LAW_TEXTS_DIR / f"{canonical_law_id}.txt"
    if txt_path.exists():
        return txt_path

    # 2. Fallback to law_sources/ if no law_text exists
    for ext in (".txt", ".html", ".pdf"):
        path = _LAW_SOURCES_DIR / f"{canonical_law_id}{ext}"
        if path.exists():
            return path

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


# Minimum bytes for a file to be treated as real bill text.
_MIN_SOURCE_BYTES = 500

# Byte-string fragments that indicate a fetch captured a portal/JS page, not statute text.
# Each entry is a bytes literal matched against the first 512 bytes of the file.
_PORTAL_SIGNATURES: list[bytes] = [
    b"Rocket NXT",                        # LexisNexis-style search portal
    b"enable JavaScript",                 # JS-gated app shell
    b"You need to enable JavaScript",
    b"<html",                             # raw HTML (should never reach law_texts/)
    b"<!DOCTYPE html",
]

# At least one of these byte patterns must appear within the scan window for the
# file to be accepted as real statutory/bill text.  WHEREAS covers executive
# orders; § covers regulation-style documents that skip "SECTION" headers.
#
# 20 KB (not 4 KB) because scraped LegiScan/portal pages routinely prepend a
# nav-menu/sidebar of that size before the actual bill text — a handful of
# real bills were being quality-gate-rejected solely because their markers
# landed at bytes 5-12K, past the old 4 KB cutoff.
_STRUCTURE_SCAN_BYTES = 20_000

_STATUTORY_STRUCTURE_MARKERS: list[bytes] = [
    b"AN ACT",
    b"Be it enacted",
    b"BE IT ENACTED",
    b"SECTION ",
    b"Section ",
    b"WHEREAS",
    b"\xc2\xa7",   # UTF-8 §
    b"\xa7",       # Latin-1 §
    b"CHAPTER ",
    b"Chapter ",
    b"Subd.",      # MN/other subdivision style
]


def _compute_fulltext_status(content: bytes) -> str:
    """Classify source content fulltext quality for downstream reporting.

    Returns one of:
      ok                    — passes all checks
      too_short             — below minimum byte threshold
      capture_failed        — portal / JS-gated page detected
      no_statutory_structure — no recognizable bill/statute markers found
    """
    if len(content) < _MIN_SOURCE_BYTES:
        return "too_short"
    head = content[:512]
    for sig in _PORTAL_SIGNATURES:
        if sig in head:
            return "capture_failed"
    structural_sample = content[:_STRUCTURE_SCAN_BYTES]
    if any(marker in structural_sample for marker in _STATUTORY_STRUCTURE_MARKERS):
        return "ok"
    return "no_statutory_structure"


def _check_source_quality(content: bytes, law_id: str) -> str | None:
    """Return a human-readable failure reason, or None if content looks like bill text.

    Checks run in priority order — first failure wins.
    """
    status = _compute_fulltext_status(content)
    if status == "too_short":
        return f"file too small ({len(content)} bytes < {_MIN_SOURCE_BYTES} minimum)"
    if status == "capture_failed":
        head = content[:512]
        for sig in _PORTAL_SIGNATURES:
            if sig in head:
                return f"portal/JS page detected (matched '{sig.decode(errors='replace')[:40]}')"
    if status == "no_statutory_structure":
        return (
            "no statutory structure found (missing AN ACT / SECTION / § markers "
            f"in first {_STRUCTURE_SCAN_BYTES // 1000} KB)"
        )
    return None


def _quarantine_file(source_path: Path, law_id: str, reason: str) -> None:
    """Move a bad source file to law_texts_quarantine/ and leave a note."""
    quarantine_dir = source_path.parent.parent / "law_texts_quarantine"
    quarantine_dir.mkdir(exist_ok=True)
    dest = quarantine_dir / source_path.name
    if not dest.exists():
        source_path.rename(dest)
    note_path = quarantine_dir / "NEEDED_SOURCES.md"
    if note_path.exists():
        existing = note_path.read_text(encoding="utf-8")
        if law_id not in existing:
            with note_path.open("a", encoding="utf-8") as f:
                f.write(f"| `{law_id}` | ⚠️ NEEDS SOURCE | Auto-quarantined: {reason} |\n")


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

            # Source quality gate: reject files that are clearly not bill text.
            # _compute_fulltext_status determines the sub-class of failure;
            # _check_source_quality maps it to a human-readable reason string.
            fulltext_status = _compute_fulltext_status(content_bytes)
            meta = dict(job.metadata_ or {})
            meta["fulltext_status"] = fulltext_status
            job.metadata_ = meta

            _quality_failure = _check_source_quality(content_bytes, canonical_law_id)
            if _quality_failure:
                _log(f"  ⚠️  Source quality gate FAILED ({_quality_failure}) — quarantining {canonical_law_id}")
                job.status = IngestionStatus.failed
                job.error_message = f"Source quality gate: {_quality_failure}"
                _quarantine_file(local_file, canonical_law_id, _quality_failure)
                db.commit()
                summary["failed"] += 1
                continue

            # Mark fetch phase complete (local files are "fetched" instantly)
            job.fetch_started_at = datetime.utcnow()
            job.fetch_completed_at = datetime.utcnow()

            # Content-addressable storage
            sha256 = hashlib.sha256(content_bytes).hexdigest()

            # RR7b: stamp source provenance on the DocumentVersion
            if dv and not dv.retrieved_at:
                dv.retrieved_at = datetime.utcnow()
            if dv and not dv.source_hash:
                dv.source_hash = sha256

            # Check for existing artifact (dedup)
            existing_artifact = db.query(RawArtifact).filter_by(sha256_hash=sha256).first()
            if existing_artifact:
                artifact = existing_artifact
                _log(f"  Artifact deduplicated: sha256={sha256[:12]}")
            else:
                # Store reference to local file (no S3/MinIO needed)
                s3_key = f"local://{local_file}"

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

            # Parse into passages (pass bytes directly, skip S3 round-trip)
            job.status = IngestionStatus.parsing
            job.parse_started_at = datetime.utcnow()
            db.commit()

            records = parse_and_normalize(db, job, artifact, content_bytes=content_bytes)

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
