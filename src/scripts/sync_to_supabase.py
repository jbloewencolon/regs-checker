"""Data sync from local Docker PostgreSQL to Supabase via REST API.

Reads from the local database (SQLAlchemy) and writes to Supabase using the
PostgREST API (httpx), avoiding any direct Postgres connection to Supabase.

Supports two modes:
  - Full sync (default): all rows in each table
  - Incremental sync (--incremental): only rows with id > last_synced_id,
    tracked in the local sync_cursors table (RR6e)

Usage:
    # Dry run — show what would be synced:
    python -m src.scripts.sync_to_supabase --dry-run

    # Full sync (reads REGS_SUPABASE_PROJECT_URL and REGS_SUPABASE_ANON_KEY from env/.env):
    python -m src.scripts.sync_to_supabase

    # Incremental sync — only new rows since last run:
    python -m src.scripts.sync_to_supabase --incremental

    # Clear Supabase tables first, then do a fresh full sync:
    python -m src.scripts.sync_to_supabase --clear

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
from datetime import timezone

# Load .env from project root
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Application tables to sync (in dependency order for FK constraints).
# The order matters for both INSERT (parents first) and DELETE/--clear (reversed).
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
    "bill_level_extractions",       # depends on document_versions
    "failed_extraction_attempts",   # depends on normalized_source_records + extraction_jobs
]

BATCH_SIZE = 500
SYNC_DESTINATION = "supabase"

# Per-table natural unique key columns for PostgREST ON CONFLICT target.
# PostgREST's default resolution=ignore-duplicates targets the PRIMARY KEY only.
# Tables whose logical unique identity lives on a non-PK column need the
# column(s) named here so additive syncs skip duplicates without 409 errors.
# Multi-column keys are comma-separated (e.g. "col_a,col_b").
TABLE_CONFLICT_COLUMNS: dict[str, str] = {
    # sha256_hash is the content-addressable identity; id can differ across reseeds
    "raw_artifacts": "sha256_hash",
    # one passage per (document_version, ordinal) position
    "normalized_source_records": "document_version_id,ordinal",
    # one triage decision per source record
    "section_triage_results": "source_record_id",
    # one review queue entry per extraction
    "review_queue": "extraction_id",
    # one bill-level result per (law, agent)
    "bill_level_extractions": "document_version_id,agent_name",
}


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
    """POST rows to Supabase PostgREST with ON CONFLICT DO NOTHING semantics.

    For tables in TABLE_CONFLICT_COLUMNS, passes ``on_conflict=<columns>`` as a
    query parameter so PostgREST generates ``ON CONFLICT (col) DO NOTHING`` on
    the correct unique constraint.  Without this, PostgREST targets the primary
    key only, and a conflict on a different unique column (e.g. sha256_hash)
    still raises a 409 error.

    Tables not in TABLE_CONFLICT_COLUMNS fall back to PK-based conflict handling,
    which is correct for additive syncs where new rows have new IDs.
    """
    params: dict[str, str] = {}
    if table in TABLE_CONFLICT_COLUMNS:
        params["on_conflict"] = TABLE_CONFLICT_COLUMNS[table]

    resp = client.post(
        f"{base_url}/rest/v1/{table}",
        params=params or None,
        json=rows,
        headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
    )
    return resp


def clear_supabase_tables(
    client: httpx.Client, base_url: str, tables: list[str]
) -> None:
    """DELETE all rows from Supabase tables in reverse dependency order.

    PostgREST requires at least one filter on DELETE. We use ``id=gte.0``
    which matches every row for integer-id tables (all current sync tables use
    serial integer PKs). Tables are cleared in reverse order so child rows are
    deleted first and FK constraints are satisfied.
    """
    print("Clearing Supabase tables (reverse dependency order)...")
    for table_name in reversed(tables):
        resp = client.delete(
            f"{base_url}/rest/v1/{table_name}",
            params={"id": "gte.0"},
            headers={"Prefer": "return=minimal"},
        )
        if resp.status_code in (200, 204):
            print(f"  {table_name}: cleared")
        elif resp.status_code == 404:
            # Table doesn't exist on Supabase — skip silently
            print(f"  {table_name}: not found (skipped)")
        else:
            print(
                f"  {table_name}: DELETE failed "
                f"({resp.status_code} {resp.text[:200]})"
            )
    print()


def _get_cursor(conn, table_name: str) -> int | None:
    """Read last_synced_id from sync_cursors for a table. Returns None if no cursor."""
    try:
        row = conn.execute(
            text(
                "SELECT last_synced_id FROM sync_cursors "
                "WHERE table_name = :t AND destination = :d"
            ),
            {"t": table_name, "d": SYNC_DESTINATION},
        ).first()
        return row[0] if row else None
    except Exception:
        return None


def _update_cursor(conn, table_name: str, last_id: int, rows_synced: int) -> None:
    """Upsert sync_cursors with the new last_synced_id."""
    try:
        conn.execute(
            text(
                "INSERT INTO sync_cursors (table_name, destination, last_synced_id, "
                "last_synced_at, rows_synced, updated_at) "
                "VALUES (:t, :d, :lid, now(), :rs, now()) "
                "ON CONFLICT (table_name, destination) DO UPDATE SET "
                "last_synced_id = EXCLUDED.last_synced_id, "
                "last_synced_at = EXCLUDED.last_synced_at, "
                "rows_synced = sync_cursors.rows_synced + EXCLUDED.rows_synced, "
                "updated_at = now()"
            ),
            {"t": table_name, "d": SYNC_DESTINATION, "lid": last_id, "rs": rows_synced},
        )
        conn.commit()
    except Exception as exc:
        print(f"    WARNING: could not update sync cursor for {table_name}: {exc}")


def sync_tables(
    source_url: str,
    supabase_url: str,
    supabase_key: str,
    dry_run: bool = False,
    clear: bool = False,
    incremental: bool = False,
) -> dict:
    """Read from local DB, push to Supabase REST API.

    When incremental=True, reads last_synced_id from the sync_cursors table
    and only fetches rows with id > last_synced_id (RR6e).
    """
    source_engine = create_engine(source_url)

    client = httpx.Client(
        timeout=60.0,
        headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
        },
    )

    if clear and not dry_run:
        clear_supabase_tables(client, supabase_url, SYNC_TABLES)

    summary = {}
    total_rows = 0

    with source_engine.connect() as conn:
        for table_name in SYNC_TABLES:
            # Determine ID window for incremental mode
            cursor_id: int | None = None
            if incremental and not clear:
                cursor_id = _get_cursor(conn, table_name)

            where_clause = ""
            count_params: dict = {}
            if cursor_id is not None:
                where_clause = f" WHERE id > {cursor_id}"

            # Count source rows (in window)
            source_count = conn.execute(
                text(f"SELECT COUNT(*) FROM {table_name}{where_clause}")  # noqa: S608
            ).scalar()

            if dry_run:
                mode_note = f" (incremental, cursor={cursor_id})" if cursor_id is not None else ""
                resp = client.get(
                    f"{supabase_url}/rest/v1/{table_name}",
                    params={"select": "count", "head": "true"},
                    headers={**client.headers, "Prefer": "count=exact"},
                )
                target_count = 0
                if resp.status_code == 200:
                    content_range = resp.headers.get("content-range", "")
                    if "/" in content_range:
                        target_count = int(content_range.split("/")[1])

                print(f"  {table_name}: {source_count} new rows{mode_note} (target has {target_count})")
                summary[table_name] = {"source": source_count, "target": target_count}
                total_rows += source_count
                continue

            if source_count == 0:
                print(f"  {table_name}: no new rows, skipping")
                summary[table_name] = 0
                continue

            # Fetch rows from source (sort self-referential tables for FK order)
            order_clause = " ORDER BY id"
            if table_name == "applicability_conditions":
                order_clause = " ORDER BY parent_id NULLS FIRST, id"
            rows = conn.execute(
                text(f"SELECT * FROM {table_name}{where_clause}{order_clause}")  # noqa: S608
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

            # Update cursor to the max id we just synced (only when incremental)
            if incremental and inserted > 0:
                last_row_id = serialized[-1].get("id")
                if last_row_id is not None:
                    _update_cursor(conn, table_name, int(last_row_id), inserted)

            mode_note = f" (incremental, from id>{cursor_id})" if cursor_id is not None else ""
            print(f"  {table_name}: {inserted} rows pushed ({errors} batch errors){mode_note}")
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
    parser.add_argument(
        "--clear",
        action="store_true",
        help="DELETE all rows from Supabase sync tables before syncing",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Only sync rows added since the last run (uses sync_cursors table)",
    )
    args = parser.parse_args()

    supabase_url = (
        args.supabase_url
        or os.environ.get("REGS_SUPABASE_PROJECT_URL")
        or os.environ.get("REGS_SUPABASE_URL")
    )
    supabase_key = (
        args.supabase_key
        or os.environ.get("REGS_SUPABASE_ANON_KEY")
        or os.environ.get("REGS_SUPABASE_KEY")
    )

    if not supabase_url:
        print("Error: No Supabase URL. Set --supabase-url or REGS_SUPABASE_URL env var.")
        sys.exit(1)
    if not supabase_key:
        print("Error: No Supabase key. Set --supabase-key or REGS_SUPABASE_KEY env var.")
        sys.exit(1)

    # Strip trailing slash
    supabase_url = supabase_url.rstrip("/")

    print(f"Source:   {args.source_url}")
    print(f"Target:   {supabase_url}")
    print(f"Mode:     {'DRY RUN' if args.dry_run else 'LIVE SYNC'}")
    print(f"Sync:     {'incremental (cursor-based)' if args.incremental else 'full'}")
    print(f"Clear:    {'YES (delete all rows first)' if args.clear else 'no'}")
    print(f"Method:   REST API (PostgREST)\n")

    summary = sync_tables(
        args.source_url, supabase_url, supabase_key,
        dry_run=args.dry_run, clear=args.clear, incremental=args.incremental,
    )

    print(f"\n{'=' * 60}")
    if args.dry_run:
        print(f"Would sync {summary['_total']} total rows across {len(SYNC_TABLES)} tables")
    else:
        print(f"Synced {summary['_total']} total rows")
    print("Done.")


if __name__ == "__main__":
    main()
