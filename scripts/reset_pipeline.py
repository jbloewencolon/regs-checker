"""Full pipeline reset — clears all stale data from the local DB.

Run this BEFORE re-seeding from the fixed fact_laws.csv.
Requires Docker Postgres to be running on port 5434.

Usage:
    python -m scripts.reset_pipeline              # Full reset (interactive confirmation)
    python -m scripts.reset_pipeline --confirm     # Skip confirmation
    python -m scripts.reset_pipeline --dry-run     # Show what would be deleted

What this does (in order):
1. Deletes all applicability_conditions
2. Deletes all obligation_dependencies
3. Deletes all review_actions
4. Deletes all review_queue items
5. Deletes all failed_extraction_attempts
6. Deletes all extractions
7. Deletes all extraction_jobs
8. Deletes all section_triage_results
9. Deletes all normalized_source_records
10. Deletes all ingestion_jobs
11. Deletes all raw_artifacts
12. Deletes all document_versions
13. Deletes all document_families
14. Sources table is PRESERVED (48 jurisdiction records)

After reset, run:
    python start.py          # Start Docker + server
    # Then use dashboard Step 1 (Seed) to re-seed from fixed CSV
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text


# Tables to clear in FK-safe order (children before parents)
TABLES_TO_CLEAR = [
    "export_jobs",
    "applicability_conditions",
    "obligation_dependencies",
    "review_actions",
    "review_queue",
    "failed_extraction_attempts",
    "extractions",
    "extraction_jobs",
    "section_triage_results",
    "normalized_source_records",
    "ingestion_jobs",
    "raw_artifacts",
    "legal_events",
    "document_versions",
    "document_families",
    # api_keys preserved (user credentials)
    # sources preserved (48 jurisdiction records)
]


def main():
    parser = argparse.ArgumentParser(description="Full pipeline reset")
    parser.add_argument(
        "--source-url",
        default="postgresql://regs:regs@localhost:5434/regs_checker",
        help="Database URL (default: local Docker postgres on 5434)",
    )
    parser.add_argument("--confirm", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--dry-run", action="store_true", help="Show counts, don't delete")
    args = parser.parse_args()

    engine = create_engine(args.source_url)

    with engine.connect() as conn:
        # Show current counts
        print("Current table counts:")
        total = 0
        for table in TABLES_TO_CLEAR:
            try:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608
            except Exception:
                count = 0  # table may not exist
            print(f"  {table:40s} {count:>8,}")
            total += count

        # Also show sources (preserved)
        src_count = conn.execute(text("SELECT COUNT(*) FROM sources")).scalar()
        print(f"  {'sources (PRESERVED)':40s} {src_count:>8,}")
        print(f"  {'─' * 50}")
        print(f"  {'Total rows to delete':40s} {total:>8,}")

        if args.dry_run:
            print("\nDry run — no changes made.")
            return

        if not args.confirm:
            print(f"\nThis will DELETE {total:,} rows from {len(TABLES_TO_CLEAR)} tables.")
            print("Sources (48 jurisdiction records) will be preserved.")
            resp = input("Type 'yes' to confirm: ")
            if resp.strip().lower() != "yes":
                print("Aborted.")
                return

        # Delete in order, using savepoints so one FK error doesn't abort the rest
        print("\nDeleting...")
        for table in TABLES_TO_CLEAR:
            savepoint = conn.begin_nested()
            try:
                result = conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608
                count = result.rowcount
                print(f"  {table:40s} {count:>8,} deleted")
                savepoint.commit()
            except Exception as e:
                savepoint.rollback()
                print(f"  {table:40s} SKIP ({e})")

        conn.commit()

        # Verify reset
        print("\nVerifying...")
        all_clear = True
        for table in TABLES_TO_CLEAR:
            try:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608
                if count > 0:
                    print(f"  WARNING: {table} still has {count} rows!")
                    all_clear = False
            except Exception:
                pass
        src_count = conn.execute(text("SELECT COUNT(*) FROM sources")).scalar()
        print(f"  sources: {src_count} rows (preserved)")
        if all_clear:
            print("  All pipeline tables are empty.")
        else:
            print("  WARNING: Some tables still have data — check for FK issues.")

        # Reset sequences so new IDs start from 1
        print("\nResetting sequences...")
        for table in TABLES_TO_CLEAR:
            try:
                conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), 1, false)"  # noqa: S608
                ))
            except Exception:
                pass  # Some tables may not have serial PKs
        conn.commit()
        print("  Sequences reset to 1.")

        print("\nDone. All pipeline data cleared. Dashboard will show zeros.")
        print("Next steps:")
        print("  1. python start.py                    # Start Docker + server")
        print("  2. Use dashboard Step 1 (Seed)        # Re-seed from fixed CSV")
        print("  3. Use dashboard Step 2 (Fetch/Parse)  # Re-ingest documents")
        print("  4. Use dashboard Step 3 (Extract)      # Run extraction pipeline")


if __name__ == "__main__":
    main()
