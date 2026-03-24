"""One-time data sync from local Docker PostgreSQL to Supabase.

Copies all application tables from the local database to a remote Supabase
database. Dagster internal tables are NOT synced (they stay local).

Usage:
    # Dry run — show what would be synced:
    python -m src.scripts.sync_to_supabase --dry-run

    # Sync to Supabase (reads REGS_SUPABASE_URL from env or .env):
    REGS_SUPABASE_URL=postgresql://postgres.<ref>:<pw>@aws-0-us-east-1.pooler.supabase.com:6543/postgres \\
        python -m src.scripts.sync_to_supabase

    # Or set the URL directly:
    python -m src.scripts.sync_to_supabase --target-url "postgresql://..."
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

# Load .env from project root so REGS_SUPABASE_URL is available
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
from sqlalchemy.orm import Session, sessionmaker

# Application tables to sync (in dependency order for FK constraints)
SYNC_TABLES = [
    "sources",
    "document_families",
    "document_versions",
    "legal_events",
    "ingestion_jobs",
    "raw_artifacts",
    "normalized_source_records",
    "section_triage_results",
    "extraction_jobs",
    "extractions",
    "review_queue",
    "review_actions",
    "obligation_dependencies",
    "applicability_conditions",
    "api_keys",
    "export_jobs",
]


def _serialize_row(row) -> dict:
    """Serialize a row dict so JSONB columns (dict/list) become JSON strings.

    psycopg2 cannot auto-serialize Python dicts into PostgreSQL JSONB params,
    so we must json.dumps() them before passing to execute().
    """
    return {
        k: json.dumps(v) if isinstance(v, (dict, list)) else v
        for k, v in dict(row).items()
    }


def sync_tables(source_url: str, target_url: str, dry_run: bool = False) -> dict:
    """Copy all rows from source DB to target DB for application tables."""
    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)

    source_session = sessionmaker(bind=source_engine)()
    target_session = sessionmaker(bind=target_engine)()

    # First, run Alembic migrations on target to ensure schema exists
    print("Verifying target schema...")
    target_inspector = inspect(target_engine)
    existing_tables = target_inspector.get_table_names()

    missing = [t for t in SYNC_TABLES if t not in existing_tables]
    if missing:
        print(f"\nTarget database is missing tables: {missing}")
        print("Run Alembic migrations first:")
        print(f"  REGS_DATABASE_URL='{target_url}' alembic upgrade head")
        sys.exit(1)

    summary = {}
    total_rows = 0

    for table_name in SYNC_TABLES:
        if table_name not in target_inspector.get_table_names():
            print(f"  SKIP {table_name} (not in target)")
            continue

        # Count source rows
        source_count = source_session.execute(
            text(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608
        ).scalar()

        # Count existing target rows
        target_count = target_session.execute(
            text(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608
        ).scalar()

        if dry_run:
            print(f"  {table_name}: {source_count} rows (target has {target_count})")
            summary[table_name] = {"source": source_count, "target": target_count}
            total_rows += source_count
            continue

        if source_count == 0:
            print(f"  {table_name}: empty, skipping")
            summary[table_name] = 0
            continue

        # Fetch all rows from source
        rows = source_session.execute(text(f"SELECT * FROM {table_name}")).mappings().all()  # noqa: S608

        if not rows:
            summary[table_name] = 0
            continue

        # Get column names and primary key for upsert
        columns = list(rows[0].keys())
        col_list = ", ".join(columns)
        param_list = ", ".join(f":{c}" for c in columns)

        pk_cols = target_inspector.get_pk_constraint(table_name).get("constrained_columns", [])
        if pk_cols:
            conflict_clause = f"ON CONFLICT ({', '.join(pk_cols)}) DO NOTHING"
        else:
            conflict_clause = "ON CONFLICT DO NOTHING"

        insert_sql = text(  # noqa: S608
            f"INSERT INTO {table_name} ({col_list}) VALUES ({param_list}) {conflict_clause}"
        )

        batch_size = 500
        for i in range(0, len(rows), batch_size):
            batch = [_serialize_row(row) for row in rows[i : i + batch_size]]
            target_session.execute(insert_sql, batch)

        # Reset sequences to max ID
        for pk_col in pk_cols:
            if pk_col == "id":
                target_session.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table_name}', '{pk_col}'), "  # noqa: S608
                    f"COALESCE((SELECT MAX({pk_col}) FROM {table_name}), 1))"
                ))

        target_session.commit()

        # Count how many rows actually landed
        new_target_count = target_session.execute(
            text(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608
        ).scalar()
        inserted = new_target_count - target_count

        print(f"  {table_name}: {inserted} new rows inserted ({len(rows)} source, {target_count} already in target)")
        summary[table_name] = inserted
        total_rows += inserted

    source_session.close()
    target_session.close()

    summary["_total"] = total_rows
    return summary


def main():
    parser = argparse.ArgumentParser(description="Sync local DB to Supabase")
    parser.add_argument(
        "--source-url",
        default="postgresql://regs:regs@localhost:5434/regs_checker",
        help="Source database URL (default: local Docker postgres)",
    )
    parser.add_argument(
        "--target-url",
        default=None,
        help="Target Supabase URL (or set REGS_SUPABASE_URL env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be synced without writing",
    )
    args = parser.parse_args()

    target_url = args.target_url or os.environ.get("REGS_SUPABASE_URL")
    if not target_url:
        print("Error: No target URL. Set --target-url or REGS_SUPABASE_URL env var.")
        sys.exit(1)

    if target_url == args.source_url:
        print("Error: Source and target URLs are the same!")
        sys.exit(1)

    print(f"Source: {args.source_url}")
    print(f"Target: {target_url[:50]}...")
    print(f"Mode:   {'DRY RUN' if args.dry_run else 'LIVE SYNC'}\n")

    summary = sync_tables(args.source_url, target_url, dry_run=args.dry_run)

    print(f"\n{'=' * 60}")
    if args.dry_run:
        print(f"Would sync {summary['_total']} total rows across {len(SYNC_TABLES)} tables")
    else:
        print(f"Synced {summary['_total']} total rows")
    print("Done.")


if __name__ == "__main__":
    main()
