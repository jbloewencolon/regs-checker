"""One-time data sync from local Docker PostgreSQL to Supabase via REST API.

Reads from the local database (SQLAlchemy) and writes to Supabase using the
PostgREST API (httpx), avoiding any direct Postgres connection to Supabase.

Usage:
    # Dry run — show what would be synced:
    python -m src.scripts.sync_to_supabase --dry-run

    # Sync (reads REGS_SUPABASE_PROJECT_URL and REGS_SUPABASE_ANON_KEY from env/.env):
    python -m src.scripts.sync_to_supabase

    # Or pass explicitly:
    python -m src.scripts.sync_to_supabase \
        --supabase-url https://wjxlimjpaijdogyrqtxc.supabase.co \
        --supabase-key "eyJ..."
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Load .env from project root
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

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

BATCH_SIZE = 500


def _json_default(obj):
    """Handle types that json.dumps can't serialize natively."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Unserializable type: {type(obj)}")


def _serialize_rows(rows: list[dict]) -> list[dict]:
    """Convert SQLAlchemy row mappings to JSON-safe dicts."""
    result = []
    for row in rows:
        clean = {}
        for k, v in dict(row).items():
            if isinstance(v, (datetime, date)):
                clean[k] = v.isoformat()
            elif isinstance(v, Decimal):
                clean[k] = float(v)
            elif isinstance(v, UUID):
                clean[k] = str(v)
            else:
                clean[k] = v
        result.append(clean)
    return result


def _supabase_post(
    client: httpx.Client, base_url: str, table: str, rows: list[dict]
) -> httpx.Response:
    """POST rows to Supabase PostgREST with upsert (Prefer: resolution=ignore)."""
    resp = client.post(
        f"{base_url}/rest/v1/{table}",
        json=rows,
        headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
    )
    return resp


def sync_tables(
    source_url: str,
    supabase_url: str,
    supabase_key: str,
    dry_run: bool = False,
) -> dict:
    """Read from local DB, push to Supabase REST API."""
    source_engine = create_engine(source_url)

    client = httpx.Client(
        timeout=60.0,
        headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
        },
    )

    summary = {}
    total_rows = 0

    with source_engine.connect() as conn:
        for table_name in SYNC_TABLES:
            # Count source rows
            source_count = conn.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608
            ).scalar()

            if dry_run:
                # Check target count via REST
                resp = client.get(
                    f"{supabase_url}/rest/v1/{table_name}",
                    params={"select": "count", "head": "true"},
                    headers={
                        **client.headers,
                        "Prefer": "count=exact",
                    },
                )
                target_count = 0
                if resp.status_code == 200:
                    content_range = resp.headers.get("content-range", "")
                    if "/" in content_range:
                        target_count = int(content_range.split("/")[1])

                print(f"  {table_name}: {source_count} rows (target has {target_count})")
                summary[table_name] = {"source": source_count, "target": target_count}
                total_rows += source_count
                continue

            if source_count == 0:
                print(f"  {table_name}: empty, skipping")
                summary[table_name] = 0
                continue

            # Fetch all rows from source
            rows = conn.execute(
                text(f"SELECT * FROM {table_name}")  # noqa: S608
            ).mappings().all()

            if not rows:
                summary[table_name] = 0
                continue

            serialized = _serialize_rows(rows)
            inserted = 0
            errors = 0

            for i in range(0, len(serialized), BATCH_SIZE):
                batch = serialized[i : i + BATCH_SIZE]
                resp = _supabase_post(client, supabase_url, table_name, batch)

                if resp.status_code in (200, 201):
                    inserted += len(batch)
                else:
                    errors += 1
                    print(f"    ERROR batch {i // BATCH_SIZE}: {resp.status_code} {resp.text[:200]}")

            # Reset sequences via REST RPC isn't available, but we can note it
            print(f"  {table_name}: {inserted} rows pushed ({errors} batch errors)")
            summary[table_name] = inserted
            total_rows += inserted

    print("\n  Note: auto-increment sequences may need resetting via Supabase dashboard or MCP.")

    client.close()
    summary["_total"] = total_rows
    return summary


def main():
    parser = argparse.ArgumentParser(description="Sync local DB to Supabase via REST API")
    parser.add_argument(
        "--source-url",
        default="postgresql://regs:regs@localhost:5434/regs_checker",
        help="Source database URL (default: local Docker postgres)",
    )
    parser.add_argument(
        "--supabase-url",
        default=None,
        help="Supabase project URL (or set REGS_SUPABASE_PROJECT_URL)",
    )
    parser.add_argument(
        "--supabase-key",
        default=None,
        help="Supabase anon/service_role key (or set REGS_SUPABASE_ANON_KEY)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be synced without writing",
    )
    args = parser.parse_args()

    supabase_url = args.supabase_url or os.environ.get("REGS_SUPABASE_PROJECT_URL")
    supabase_key = args.supabase_key or os.environ.get("REGS_SUPABASE_ANON_KEY")

    if not supabase_url:
        print("Error: No Supabase URL. Set --supabase-url or REGS_SUPABASE_PROJECT_URL env var.")
        sys.exit(1)
    if not supabase_key:
        print("Error: No Supabase key. Set --supabase-key or REGS_SUPABASE_ANON_KEY env var.")
        sys.exit(1)

    # Strip trailing slash
    supabase_url = supabase_url.rstrip("/")

    print(f"Source:   {args.source_url}")
    print(f"Target:   {supabase_url}")
    print(f"Mode:     {'DRY RUN' if args.dry_run else 'LIVE SYNC'}")
    print(f"Method:   REST API (PostgREST)\n")

    summary = sync_tables(args.source_url, supabase_url, supabase_key, dry_run=args.dry_run)

    print(f"\n{'=' * 60}")
    if args.dry_run:
        print(f"Would sync {summary['_total']} total rows across {len(SYNC_TABLES)} tables")
    else:
        print(f"Synced {summary['_total']} total rows")
    print("Done.")


if __name__ == "__main__":
    main()
