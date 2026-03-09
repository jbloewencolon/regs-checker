"""Dagster ops and jobs for Orrick AI law tracker scraping."""

import dagster
import structlog

from src.db.engine import SessionLocal
from src.ingestion.orrick_scraper import scrape_tracker, seed_from_tracker

logger = structlog.get_logger()


@dagster.op(
    description="Scrape Orrick AI Law Tracker and seed new legislation for ingestion",
)
def scrape_and_seed_orrick(context: dagster.OpExecutionContext) -> int:
    """Scrape the Orrick AI law tracker table and create ingestion jobs.

    Returns the number of new laws seeded.
    """
    db = SessionLocal()

    try:
        context.log.info("Starting Orrick AI Law Tracker scrape")
        records = scrape_tracker()
        context.log.info(f"Parsed {len(records)} rows from Orrick tracker")

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
        context.log.error(f"Orrick scrape job failed: {e}")
        raise
    finally:
        db.close()


orrick_discovery_job = dagster.GraphDefinition(
    name="orrick_discovery",
    description="Scrape Orrick AI Law Tracker and seed legislation for ingestion",
    node_defs=[scrape_and_seed_orrick],
).to_job()
