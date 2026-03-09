"""Dagster ops and jobs for LegiScan discovery and multi-state expansion."""

import dagster
import structlog

from src.db.engine import SessionLocal
from src.ingestion.legiscan import (
    SUPPORTED_STATES,
    discover_ai_bills,
    seed_bill_for_ingestion,
)

logger = structlog.get_logger()


@dagster.op(
    description="Discover AI-related bills via LegiScan API and seed for ingestion",
)
def discover_and_seed_bills(context: dagster.OpExecutionContext) -> int:
    """Discover new AI bills across supported states and create ingestion jobs.

    Returns the number of new bills seeded.
    """
    db = SessionLocal()
    seeded = 0

    try:
        # Discover across all supported states
        states = list(SUPPORTED_STATES.keys())
        context.log.info(f"Starting LegiScan discovery for states: {states}")

        bills = discover_ai_bills(db, states=states)
        context.log.info(f"Discovered {len(bills)} unique AI-related bills")

        for bill in bills:
            state = bill.get("state", "")
            state_info = SUPPORTED_STATES.get(state)
            if not state_info:
                continue

            try:
                job = seed_bill_for_ingestion(
                    db,
                    legiscan_bill_id=bill["legiscan_bill_id"],
                    jurisdiction_code=state,
                    jurisdiction_name=state_info["name"],
                )
                if job:
                    seeded += 1
                    context.log.info(
                        f"Seeded: {bill.get('bill_number')} ({state}) - job #{job.id}"
                    )
            except Exception as e:
                context.log.warning(
                    f"Failed to seed {bill.get('bill_number', '?')} ({state}): {e}"
                )

        db.commit()
        context.log.info(f"Discovery complete: {seeded} new bills seeded")
        return seeded

    except Exception as e:
        db.rollback()
        context.log.error(f"Discovery job failed: {e}")
        raise
    finally:
        db.close()


legiscan_discovery_job = dagster.GraphDefinition(
    name="legiscan_discovery",
    description="Discover and seed AI legislation from LegiScan",
    node_defs=[discover_and_seed_bills],
).to_job()
