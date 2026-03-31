"""Dagster ops and jobs for law tracker parsing and seeding."""

import csv
from pathlib import Path

import dagster
import structlog

from src.db.engine import SessionLocal
from src.ingestion._archived.pdf_tracker import STATE_CODES, seed_from_tracker

logger = structlog.get_logger()

TRACKER_CSV = Path("static/ai_law_tracker.csv")


def _csv_to_records() -> list[dict]:
    """Read ai_law_tracker.csv into seed_from_tracker record format."""
    if not TRACKER_CSV.exists():
        return []
    with open(TRACKER_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    records = []
    for row in rows:
        state_name = row.get("State/Terr", "").strip()
        state_code = STATE_CODES.get(state_name, "")
        if not state_code and len(state_name) == 2:
            state_code = state_name.upper()
        records.append({
            "state": state_name,
            "state_code": state_code,
            "ai_scope": row.get("AI Scope", ""),
            "law_name": row.get("Relevant Law", ""),
            "law_url": row.get("Source URL", ""),
            "bill_id": row.get("Bill ID", ""),
            "effective_date": row.get("Effective Date", ""),
            "key_requirements": row.get("Key Requirements", ""),
            "enforcement": row.get("Enforcements Penalties", ""),
        })
    return records


@dagster.op(
    description="Parse law tracker CSV and seed new legislation for ingestion",
)
def parse_and_seed_pdf(context: dagster.OpExecutionContext) -> int:
    """Parse the AI law tracker CSV and create ingestion jobs.

    Falls back to the Orrick PDF if the CSV doesn't exist.

    Returns the number of new laws seeded.
    """
    db = SessionLocal()

    try:
        records = _csv_to_records()
        if records:
            context.log.info(f"Loaded {len(records)} rows from tracker CSV")
        else:
            from src.ingestion._archived.pdf_tracker import parse_tracker_pdf
            records = parse_tracker_pdf()
            context.log.info(f"Parsed {len(records)} rows from PDF tracker (fallback)")

        jobs, stats = seed_from_tracker(db, records)
        db.commit()

        context.log.info(
            f"Seeded {stats['new_jobs']} new, {stats['existing']} existing, "
            f"{len(stats['skipped_no_url'])} skipped (no URL)"
        )
        for job in jobs:
            dv = job.document_version
            context.log.info(
                f"  Job #{job.id}: {dv.family.source.jurisdiction_code} - "
                f"{dv.family.short_cite}"
            )
        return len(jobs)

    except Exception as e:
        db.rollback()
        context.log.error(f"Law tracker parse failed: {e}")
        raise
    finally:
        db.close()


pdf_discovery_job = dagster.GraphDefinition(
    name="pdf_discovery",
    description="Parse law tracker CSV and seed legislation for ingestion",
    node_defs=[parse_and_seed_pdf],
).to_job()
