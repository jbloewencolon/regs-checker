"""Dagster ops and jobs for PDF tracker parsing and law seeding."""

import dagster
import structlog

from src.db.engine import SessionLocal
from src.ingestion.pdf_tracker import parse_tracker_pdf, seed_from_tracker

logger = structlog.get_logger()


@dagster.op(
    description="Parse Orrick PDF tracker and seed new legislation for ingestion",
)
def parse_and_seed_pdf(context: dagster.OpExecutionContext) -> int:
    """Parse the Orrick AI law tracker PDF and create ingestion jobs.

    Returns the number of new laws seeded.
    """
    db = SessionLocal()

    try:
        context.log.info("Starting Orrick PDF tracker parse")
        records = parse_tracker_pdf()
        context.log.info(f"Parsed {len(records)} rows from PDF tracker")

        jobs = seed_from_tracker(db, records)
        db.commit()

        context.log.info(f"Seeded {len(jobs)} new laws for ingestion")
        for job in jobs:
            dv = job.document_version
            context.log.info(
                f"  Job #{job.id}: {dv.family.source.jurisdiction_code} - "
                f"{dv.family.short_cite}"
            )
        return len(jobs)

    except Exception as e:
        db.rollback()
        context.log.error(f"PDF tracker parse failed: {e}")
        raise
    finally:
        db.close()


pdf_discovery_job = dagster.GraphDefinition(
    name="pdf_discovery",
    description="Parse Orrick PDF tracker and seed legislation for ingestion",
    node_defs=[parse_and_seed_pdf],
).to_job()
