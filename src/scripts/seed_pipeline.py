"""Seed script for populating the database with initial documents for ingestion.

Usage:
    # Seed Colorado SB205 manually (no LegiScan API key needed):
    python -m src.scripts.seed_pipeline --mode manual

    # Discover and seed bills from LegiScan API:
    python -m src.scripts.seed_pipeline --mode legiscan --states CO

    # Discover bills from multiple states:
    python -m src.scripts.seed_pipeline --mode legiscan --states CO,CA,CT
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import structlog

from src.db.engine import SessionLocal
from src.db.models import (
    DocumentFamily,
    DocumentVersion,
    IngestionJob,
    IngestionStatus,
    LegalEvent,
    LegalEventType,
    Source,
    TemporalStatus,
)

logger = structlog.get_logger()


def seed_colorado_sb205(db) -> IngestionJob:
    """Seed Colorado SB21-169 (the Colorado AI Act) for ingestion.

    This is the primary test document for the pipeline — creates all records
    needed for Dagster to pick up and process.
    """
    # Source
    source = db.query(Source).filter_by(
        jurisdiction_code="CO", connector_id="colorado_ga"
    ).first()
    if not source:
        source = Source(
            jurisdiction_code="CO",
            jurisdiction_name="Colorado",
            source_type="state_statute",
            base_url="https://leg.colorado.gov",
            connector_id="colorado_ga",
        )
        db.add(source)
        db.flush()

    # Document Family
    family = db.query(DocumentFamily).filter_by(
        source_id=source.id, short_cite="SB21-169"
    ).first()
    if not family:
        family = DocumentFamily(
            source_id=source.id,
            canonical_title="Colorado SB21-169 - Concerning Consumer Protections for "
            "Interactions with Artificial Intelligence Systems",
            short_cite="SB21-169",
            subject_area="artificial_intelligence",
            metadata_={
                "bill_number": "SB21-169",
                "session": "2024 Regular Session",
                "also_known_as": "Colorado AI Act",
            },
        )
        db.add(family)
        db.flush()

    # Document Version (enrolled/final version)
    version = db.query(DocumentVersion).filter_by(
        family_id=family.id, version_label="Enrolled"
    ).first()
    if not version:
        version = DocumentVersion(
            family_id=family.id,
            version_label="Enrolled",
            temporal_status=TemporalStatus.active,
            effective_date=date(2026, 2, 1),
            metadata_={
                "source": "Colorado General Assembly website",
            },
        )
        db.add(version)
        db.flush()

        # Legal events for SB21-169
        events = [
            (LegalEventType.enactment, date(2024, 5, 8), "Signed by Governor", "Governor"),
            (LegalEventType.effective, date(2026, 2, 1), "Effective date", "Colorado Legislature"),
        ]
        for event_type, event_date, desc, authority in events:
            db.add(LegalEvent(
                document_version_id=version.id,
                event_type=event_type,
                event_date=event_date,
                description=desc,
                authority=authority,
            ))

    # Ingestion Job
    existing_job = db.query(IngestionJob).filter_by(
        document_version_id=version.id
    ).first()
    if existing_job:
        logger.info("job_exists", job_id=existing_job.id, status=existing_job.status)
        return existing_job

    job = IngestionJob(
        document_version_id=version.id,
        status=IngestionStatus.pending,
        fetch_url="https://leg.colorado.gov/sites/default/files/2024a_205_signed.pdf",
    )
    db.add(job)
    db.flush()

    logger.info("seeded_sb205", job_id=job.id, version_id=version.id)
    return job


def seed_federal_nist_ai_rmf(db) -> IngestionJob:
    """Seed the NIST AI Risk Management Framework for ingestion."""
    source = db.query(Source).filter_by(
        jurisdiction_code="US", connector_id="federal_nist"
    ).first()
    if not source:
        source = Source(
            jurisdiction_code="US",
            jurisdiction_name="United States (Federal)",
            source_type="federal_framework",
            base_url="https://www.nist.gov",
            connector_id="federal_nist",
        )
        db.add(source)
        db.flush()

    family = db.query(DocumentFamily).filter_by(
        source_id=source.id, short_cite="NIST AI 100-1"
    ).first()
    if not family:
        family = DocumentFamily(
            source_id=source.id,
            canonical_title="NIST AI Risk Management Framework (AI RMF 1.0)",
            short_cite="NIST AI 100-1",
            subject_area="artificial_intelligence",
            metadata_={"framework_version": "1.0"},
        )
        db.add(family)
        db.flush()

    version = db.query(DocumentVersion).filter_by(
        family_id=family.id, version_label="1.0"
    ).first()
    if not version:
        version = DocumentVersion(
            family_id=family.id,
            version_label="1.0",
            temporal_status=TemporalStatus.active,
            effective_date=date(2023, 1, 26),
        )
        db.add(version)
        db.flush()

        db.add(LegalEvent(
            document_version_id=version.id,
            event_type=LegalEventType.effective,
            event_date=date(2023, 1, 26),
            description="NIST AI RMF 1.0 published",
            authority="NIST",
        ))

    existing_job = db.query(IngestionJob).filter_by(
        document_version_id=version.id
    ).first()
    if existing_job:
        return existing_job

    job = IngestionJob(
        document_version_id=version.id,
        status=IngestionStatus.pending,
        fetch_url="https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf",
    )
    db.add(job)
    db.flush()

    logger.info("seeded_nist_rmf", job_id=job.id)
    return job


def seed_via_legiscan(db, states: list[str]) -> list[IngestionJob]:
    """Use LegiScan API to discover and seed AI bills."""
    from src.ingestion.legiscan import discover_ai_bills, seed_bill_for_ingestion, SUPPORTED_STATES

    bills = discover_ai_bills(db, states=states)
    jobs = []

    for bill in bills:
        state = bill.get("state", "")
        state_info = SUPPORTED_STATES.get(state, {})
        if not state_info:
            continue

        job = seed_bill_for_ingestion(
            db,
            legiscan_bill_id=bill["legiscan_bill_id"],
            jurisdiction_code=state,
            jurisdiction_name=state_info["name"],
        )
        if job:
            jobs.append(job)

    return jobs


def main():
    parser = argparse.ArgumentParser(description="Seed the regs-checker pipeline")
    parser.add_argument(
        "--mode",
        choices=["manual", "legiscan"],
        default="manual",
        help="Seeding mode: 'manual' for hardcoded docs, 'legiscan' for API discovery",
    )
    parser.add_argument(
        "--states",
        default="CO",
        help="Comma-separated state codes for LegiScan discovery (default: CO)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.mode == "manual":
            job1 = seed_colorado_sb205(db)
            job2 = seed_federal_nist_ai_rmf(db)
            db.commit()
            print(f"Seeded CO SB21-169: IngestionJob #{job1.id} (status: {job1.status})")
            print(f"Seeded NIST AI RMF: IngestionJob #{job2.id} (status: {job2.status})")
        elif args.mode == "legiscan":
            states = [s.strip().upper() for s in args.states.split(",")]
            jobs = seed_via_legiscan(db, states)
            db.commit()
            print(f"Seeded {len(jobs)} bills from LegiScan for states: {args.states}")
            for job in jobs:
                dv = job.document_version
                print(f"  Job #{job.id}: {dv.family.short_cite} - {dv.family.canonical_title[:60]}")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
