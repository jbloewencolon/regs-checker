"""Seed script for populating the database with initial documents for ingestion.

Usage:
    # Seed Colorado SB205 manually:
    python -m src.scripts.seed_pipeline --mode manual

    # Discover and seed all bills from Orrick PDF tracker:
    python -m src.scripts.seed_pipeline --mode pdf

    # Fetch + parse + chunk all pending ingestion jobs:
    python -m src.scripts.seed_pipeline --mode fetch

    # Fetch with a limit (useful for testing):
    python -m src.scripts.seed_pipeline --mode fetch --limit 5

    # === PRIMARY EXTRACTION WORKFLOW (API) ===

    # Run AI extraction on all unprocessed passages:
    python -m src.scripts.seed_pipeline --mode extract

    # Extract with a limit (test first!):
    python -m src.scripts.seed_pipeline --mode extract --limit 20

    # Use Batch API for 50% cost savings (results in 24h):
    python -m src.scripts.seed_pipeline --mode extract --batch

    # === SUPPLEMENTARY WORKFLOWS ===

    # Export passages for offline/debug extraction:
    python -m src.scripts.seed_pipeline --mode export-passages

    # Import extraction results from JSON:
    python -m src.scripts.seed_pipeline --mode import-extractions

    # Re-queue failed jobs back to pending and retry:
    python -m src.scripts.seed_pipeline --mode retry-failed

    # === QUALITY & COMPLETENESS ===

    # Check extraction coverage per law:
    python -m src.scripts.seed_pipeline --mode check-completeness

    # Find extractions from outdated models:
    python -m src.scripts.seed_pipeline --mode check-stale

    # === CSV EXPORT/IMPORT ===

    # Export pipeline state to editable CSV:
    python -m src.scripts.seed_pipeline --mode export-csv

    # Import corrections from edited CSV:
    python -m src.scripts.seed_pipeline --mode import-csv --input export/pipeline_data.csv
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


def seed_via_pdf(db) -> list[IngestionJob]:
    """Seed from ai_law_tracker.csv (primary) or Orrick PDF (fallback)."""
    import csv
    from src.ingestion.pdf_tracker import STATE_CODES, seed_from_tracker

    csv_path = Path("static/ai_law_tracker.csv")
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8") as f:
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
        logger.info("seeding_from_csv", count=len(records))
    else:
        from src.ingestion.pdf_tracker import parse_tracker_pdf
        records = parse_tracker_pdf()
        logger.info("seeding_from_pdf_fallback", count=len(records))

    jobs, _stats = seed_from_tracker(db, records)
    return jobs


def run_fetch(db, limit: int | None = None) -> dict:
    """Fetch, store, parse, and chunk all pending ingestion jobs."""
    from src.ingestion.pipeline import run_pending_ingestion

    return run_pending_ingestion(db, limit=limit, on_progress=print)


def run_extract(db, limit: int | None = None, batch: bool = False) -> dict:
    """Run AI extraction agents on all unprocessed passages."""
    from src.ingestion.extractor import run_extraction

    return run_extraction(db, limit=limit, on_progress=print, batch_mode=batch)


def run_recover(db, limit: int | None = None) -> dict:
    """Re-extract passages with partial results (missing agent outputs)."""
    from src.ingestion.extractor import run_recovery_extraction

    return run_recovery_extraction(db, limit=limit, on_progress=print)


def run_batch_results(db, batch_id: str) -> dict:
    """Retrieve and process results from a completed Batch API run."""
    from src.ingestion.extractor import retrieve_batch_results

    return retrieve_batch_results(db, batch_id=batch_id, on_progress=print)


def run_evaluate() -> None:
    """Run the evaluation harness against gold-standard fixtures and print report."""
    from src.evaluation.harness import EvaluationHarness

    harness = EvaluationHarness()
    result = harness.run()
    report = harness.print_report(result)
    print(report)


def run_completeness_check(db, check_stale: bool = False) -> None:
    """Run extraction completeness report and print results."""
    from src.core.completeness import format_completeness_report, run_completeness_report
    from src.core.config import settings

    model_id = None
    if check_stale:
        model_id = settings.extraction_model

    report = run_completeness_report(db, current_model_id=model_id)
    print(format_completeness_report(report))


def run_export_csv(db, mode: str = "discovery") -> None:
    """Export pipeline data to CSV."""
    from src.scripts.pipeline_csv import export_discovery_csv, export_fetch_status_csv

    if mode == "fetch-status":
        export_fetch_status_csv(db)
    else:
        export_discovery_csv(db)


def run_import_csv(db, input_path: str) -> None:
    """Import corrections from edited CSV."""
    from src.scripts.pipeline_csv import import_discovery_csv

    import_discovery_csv(db, input_path)


# ---------------------------------------------------------------------------
# Known bad URL corrections — keyed by ingestion_job.id or (state_code, short_cite)
# These are jobs where the Orrick tracker seeded the wrong URL or the original
# source is permanently dead and we have a known-good replacement.
# ---------------------------------------------------------------------------
_URL_FIXES: dict[str, str] = {
    # -----------------------------------------------------------------------
    # Category B: legiscan.com 403s — replaced with direct state legislature URLs
    # -----------------------------------------------------------------------
    # NH — gencourt.state.nh.us
    "100": "https://gencourt.state.nh.us/rsa/html/LXIII/664/664-14-c.htm",  # AI Political Advertising
    "101": "https://www.gencourt.state.nh.us/bill_status/pdf.aspx?id=2024-HB1432",  # Deepfake Act
    "103": "https://www.gencourt.state.nh.us/bill_status/pdf.aspx?id=2024-HB1688",  # State Agency AI Bill
    # MD — mgaleg.maryland.gov
    "76": "https://mgaleg.maryland.gov/2024RS/bills/sb/sb0818e.pdf",  # AI Governance Act 2024
    # RI — rilegislature.gov
    "138": "https://webserver.rilegislature.gov/BillText/BillText24/SenateText24/S2500A.pdf",  # RI Data Privacy Act
    # TX — capitol.texas.gov
    "154": "https://capitol.texas.gov/tlodocs/88R/billtext/html/SB01361E.htm",  # Deepfake Explicit Videos
    # WI — docs.legis.wisconsin.gov
    "178": "https://docs.legis.wisconsin.gov/2023/proposals/reg/sen/bill/sb314",  # WI CSAM Amendment
    # -----------------------------------------------------------------------
    # Category B: ncleg.gov 403s — direct PDF links
    # -----------------------------------------------------------------------
    "123": "https://www.ncleg.gov/Sessions/2023/Bills/House/PDF/H591v5.pdf",  # NC CSAM Laws (same bill)
    "124": "https://www.ncleg.gov/Sessions/2023/Bills/House/PDF/H591v5.pdf",  # NC Intimate Images (same bill)
    # -----------------------------------------------------------------------
    # Category B: NV leg.state.nv.us 403
    # -----------------------------------------------------------------------
    "99": "https://www.leg.state.nv.us/App/NELIS/REL/82nd2023/Bill/10280/Text",  # NV AI Political Advertising (AB468)
    # -----------------------------------------------------------------------
    # Category C: NJ connection refused — use www subdomain
    # -----------------------------------------------------------------------
    "105": "https://www.njleg.state.nj.us/Bills/2024/A4000/3540_R3.PDF",  # Criminal Penalties Deepfakes
    "106": "https://www.njleg.state.nj.us/Bills/2022/PL23/266_.PDF",  # NJ Data Privacy Act
    # -----------------------------------------------------------------------
    # Category C: MD casetext 410 → mgaleg.maryland.gov
    # (also handled by _ALTERNATIVE_URL_RULES in connector.py, but explicit fix
    # ensures the stored URL is correct for future retries)
    # -----------------------------------------------------------------------
    "75": "https://mgaleg.maryland.gov/2024RS/bills/hb/hb0033E.pdf",  # Amendment to MD CSAM Statute
    # -----------------------------------------------------------------------
    # Category C: MS SSL — billstatus.ls.state.ms.us (SSL bypass in connector.py)
    # Job #85 should resolve with SSL bypass on retry; no URL change needed.
    # -----------------------------------------------------------------------
    # -----------------------------------------------------------------------
    # Bug: Job #116 — NY law with wrong CT URL (handled separately in fix_known_bad_urls)
    # The correct URL needs to be found at legislation.nysenate.gov for the
    # NY Algorithmic Pricing Disclosure Act.
    # -----------------------------------------------------------------------
}


def fix_known_bad_urls(db) -> int:
    """Update ingestion job URLs using the _URL_FIXES map and fix known data bugs.

    Also handles:
      - Job #116: NY Algorithmic Pricing Disclosure Act had a CT URL (wrong state)
    """
    from src.db.models import DocumentFamily

    fixed = 0

    # --- Fix Job #116: wrong URL (CT URL for NY law) ---
    job_116 = db.query(IngestionJob).filter_by(id=116).first()
    if job_116 and job_116.fetch_url and "cga.ct.gov" in job_116.fetch_url:
        # This job is for NY Algorithmic Pricing Disclosure Act but was seeded
        # with a CT URL. Mark it as needing manual URL replacement.
        dv = job_116.document_version
        if dv and dv.family:
            label = f"{dv.family.source.jurisdiction_code} - {dv.family.short_cite}"
        else:
            label = f"Job #{job_116.id}"
        logger.warning(
            "job_116_wrong_url",
            job_id=116,
            label=label,
            current_url=job_116.fetch_url,
            action="marked_failed_wrong_url",
        )
        job_116.status = IngestionStatus.failed
        job_116.error_message = (
            "DATA BUG: Orrick seeded CT URL for NY law. "
            "Needs manual URL update to correct NY legislature link."
        )
        fixed += 1

    # --- Apply _URL_FIXES map ---
    for key, new_url in _URL_FIXES.items():
        # Try by job ID first
        if key.isdigit():
            job = db.query(IngestionJob).filter_by(id=int(key)).first()
        else:
            # Try by "STATE-ShortCite"
            parts = key.split("-", 1)
            if len(parts) == 2:
                state_code, short_cite = parts
                job = (
                    db.query(IngestionJob)
                    .join(IngestionJob.document_version)
                    .join(DocumentVersion.family)
                    .join(DocumentFamily.source)
                    .filter(
                        Source.jurisdiction_code == state_code,
                        DocumentFamily.short_cite == short_cite,
                    )
                    .first()
                )
            else:
                continue

        if job and job.fetch_url != new_url:
            logger.info(
                "url_fixed",
                job_id=job.id,
                old_url=job.fetch_url[:80],
                new_url=new_url[:80],
            )
            job.fetch_url = new_url
            if job.status == IngestionStatus.failed:
                job.status = IngestionStatus.pending
                job.error_message = None
            fixed += 1

    if fixed:
        db.commit()
    print(f"Fixed {fixed} job URLs.")
    return fixed


def retry_failed_jobs(db, error_filter: str | None = None) -> dict:
    """Re-queue failed ingestion jobs back to pending, then re-run them.

    Args:
        db: SQLAlchemy session
        error_filter: If set, only retry jobs whose error_message contains this
                      substring (case-insensitive). E.g. "403", "SSL", "timeout".

    Returns:
        Summary dict with requeued count and fetch results.
    """
    from src.ingestion.pipeline import run_pending_ingestion

    failed_jobs = db.query(IngestionJob).filter(
        IngestionJob.status == IngestionStatus.failed
    ).all()

    if error_filter:
        needle = error_filter.lower()
        failed_jobs = [
            j for j in failed_jobs
            if j.error_message and needle in j.error_message.lower()
        ]

    if not failed_jobs:
        print("No matching failed jobs found.")
        return {"requeued": 0, "completed": 0, "failed": 0, "total_passages": 0}

    # Show what we're about to retry
    print(f"Re-queuing {len(failed_jobs)} failed jobs:")
    for job in failed_jobs:
        dv = job.document_version
        label = "unknown"
        if dv and dv.family:
            label = f"{dv.family.source.jurisdiction_code} - {dv.family.short_cite}"
        err_snippet = (job.error_message or "")[:80]
        print(f"  Job #{job.id}: {label}  ({err_snippet})")

    # Reset to pending
    for job in failed_jobs:
        job.status = IngestionStatus.pending
        job.error_message = None
    db.commit()

    print(f"\nRe-queued {len(failed_jobs)} jobs. Starting fetch...\n")

    # Now run the fetch pipeline on the re-queued jobs
    summary = run_pending_ingestion(db, on_progress=print)
    summary["requeued"] = len(failed_jobs)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Seed the regs-checker pipeline")
    parser.add_argument(
        "--mode",
        choices=[
            "manual", "pdf", "fetch", "export-passages", "import-extractions",
            "extract", "recover", "batch-results", "evaluate", "retry-failed",
            "fix-urls", "check-completeness", "check-stale",
            "export-csv", "import-csv", "export-fetch-csv",
        ],
        default="manual",
        help=(
            "Pipeline mode: "
            "'manual' seeds hardcoded docs, "
            "'pdf' parses Orrick PDF tracker, "
            "'fetch' processes all pending ingestion jobs, "
            "'export-passages' exports unprocessed passages for external extraction, "
            "'import-extractions' imports JSON extraction results, "
            "'extract' runs AI extraction agents via API, "
            "'recover' re-extracts passages with partial results (missing agents), "
            "'batch-results' retrieves and processes completed Batch API results, "
            "'evaluate' runs extraction agents against gold-standard fixtures, "
            "'retry-failed' re-queues and retries failed jobs, "
            "'fix-urls' applies known URL corrections and data bug fixes, "
            "'check-completeness' reports extraction coverage gaps per law, "
            "'check-stale' reports extractions from outdated models/prompts, "
            "'export-csv' exports pipeline discovery data to CSV, "
            "'export-fetch-csv' exports fetch/parse status to CSV, "
            "'import-csv' imports corrections from edited CSV"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of jobs to process in fetch mode (default: all)",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        default=False,
        help="Use Anthropic Batch API for extraction (50%% discount, 24h turnaround)",
    )
    parser.add_argument(
        "--batch-id",
        type=str,
        default=None,
        help="Batch ID to retrieve results for (use with --mode batch-results)",
    )
    parser.add_argument(
        "--error-filter",
        type=str,
        default=None,
        help="Only retry failed jobs matching this substring (e.g. '403', 'SSL', 'timeout')",
    )
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Input JSON file for import-extractions mode (default: all export/batch_*_results.json)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=15,
        help="Passages per export file in export-passages mode (default: 15)",
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
        elif args.mode == "pdf":
            jobs = seed_via_pdf(db)
            db.commit()
            print(f"Seeded {len(jobs)} laws from Orrick PDF tracker")
            for job in jobs:
                dv = job.document_version
                print(
                    f"  Job #{job.id}: {dv.family.source.jurisdiction_code} - "
                    f"{dv.family.short_cite}"
                )
        elif args.mode == "fetch":
            summary = run_fetch(db, limit=args.limit)
            print(f"\n{'=' * 60}")
            print("Ingestion complete:")
            print(f"  Pending:         {summary['total_pending']}")
            print(f"  Completed:       {summary['completed']}")
            print(f"  Failed:          {summary['failed']}")
            print(f"  Total passages:  {summary['total_passages']}")
        elif args.mode == "export-passages":
            from src.scripts.manual_extraction import export_passages
            export_passages(db, limit=args.limit, batch_size=args.batch_size)
        elif args.mode == "import-extractions":
            from src.scripts.manual_extraction import import_extractions
            import_extractions(db, input_path=args.input)
        elif args.mode == "extract":
            summary = run_extract(db, limit=args.limit, batch=args.batch)
            print(f"\n{'=' * 60}")
            if args.batch:
                print("Batch extraction submitted:")
                print(f"  Batch ID:           {summary.get('batch_id')}")
                print(f"  Requests submitted: {summary.get('requests_submitted')}")
                print(f"  Status:             {summary.get('status')}")
            else:
                print("Extraction complete:")
                print(f"  Passages processed: {summary['records_processed']}")
                print(f"  Extractions created: {summary['total_extractions']}")
                print(f"  Failures:           {summary['records_failed']}")
                print(f"  Short skipped:      {summary.get('records_skipped_short', 0)}")
                print(f"  Passages merged:    {summary.get('passages_merged', 0)}")
                print(f"  Agents skipped:     {summary.get('agents_skipped_by_signal', 0)}")
                tokens = summary.get("token_usage", {})
                if tokens.get("total_calls"):
                    print(f"\nToken usage:")
                    print(f"  Input tokens:  {tokens['input_tokens']:,}")
                    print(f"  Output tokens: {tokens['output_tokens']:,}")
                    print(f"  Total tokens:  {tokens['total_tokens']:,}")
                    print(f"  API calls:     {tokens['total_calls']}")
        elif args.mode == "recover":
            summary = run_recover(db, limit=args.limit)
            print(f"\n{'=' * 60}")
            print("Recovery extraction complete:")
            print(f"  Passages checked:    {summary['total_checked']}")
            print(f"  Gaps found:          {summary['gaps_found']}")
            print(f"  Extractions created: {summary['extractions_created']}")
            print(f"  Errors:              {summary.get('errors', 0)}")
            tokens = summary.get("token_usage", {})
            if tokens.get("total_calls"):
                print(f"\nToken usage:")
                print(f"  Input tokens:  {tokens['input_tokens']:,}")
                print(f"  Output tokens: {tokens['output_tokens']:,}")
                print(f"  Total tokens:  {tokens['total_tokens']:,}")
                print(f"  API calls:     {tokens['total_calls']}")
        elif args.mode == "batch-results":
            if not args.batch_id:
                print("Error: --batch-id is required for batch-results mode", file=sys.stderr)
                sys.exit(1)
            summary = run_batch_results(db, batch_id=args.batch_id)
            print(f"\n{'=' * 60}")
            print("Batch results:")
            print(f"  Batch ID:           {summary.get('batch_id')}")
            print(f"  Status:             {summary.get('status')}")
            print(f"  Results processed:  {summary.get('results_processed', 0)}")
            print(f"  Extractions created: {summary.get('extractions_created', 0)}")
            print(f"  Errors:             {summary.get('errors', 0)}")
        elif args.mode == "evaluate":
            run_evaluate()
        elif args.mode == "fix-urls":
            fixed = fix_known_bad_urls(db)
            print(f"\n{'=' * 60}")
            print(f"URL fix complete: {fixed} jobs updated")
            print("Run --mode retry-failed to re-process the fixed jobs.")
        elif args.mode == "retry-failed":
            summary = retry_failed_jobs(db, error_filter=args.error_filter)
            print(f"\n{'=' * 60}")
            print("Retry complete:")
            print(f"  Re-queued:       {summary['requeued']}")
            print(f"  Completed:       {summary['completed']}")
            print(f"  Still failed:    {summary['failed']}")
            print(f"  Total passages:  {summary['total_passages']}")
        elif args.mode == "check-completeness":
            run_completeness_check(db, check_stale=False)
        elif args.mode == "check-stale":
            run_completeness_check(db, check_stale=True)
        elif args.mode == "export-csv":
            run_export_csv(db, mode="discovery")
        elif args.mode == "export-fetch-csv":
            run_export_csv(db, mode="fetch-status")
        elif args.mode == "import-csv":
            if not args.input:
                print("Error: --input is required for import-csv mode", file=sys.stderr)
                sys.exit(1)
            run_import_csv(db, input_path=args.input)
    except Exception as e:
        db.rollback()
        print(f"Error: {e}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
