"""Incremental extraction sync: Regs Checker Supabase → Policy Navigator Supabase.

Reads extractions from the Regs Checker pipeline database and writes them to
the Policy Navigator product database's `synced_extractions` table. Uses the
`law_document_bridge` table in Policy Navigator to resolve document_family IDs
to Policy Navigator law_ids.

Key design decisions:
  - Incremental cursor: MAX(system_a_extraction_id) in synced_extractions.
    No external state file or metadata table needed.
  - Bridge resolution: Loads law_document_bridge into memory and maps
    system_a_doc_family_id → law_id for each extraction.
  - Idempotent upserts: ON CONFLICT (system_a_extraction_id) DO NOTHING
    so the script is safe to re-run.
  - sync_to_supabase.py is NOT replaced — it handles local Docker →
    Regs Checker Supabase. This script handles the next leg:
    Regs Checker Supabase → Policy Navigator Supabase.

Usage:
    # Dry run — show what would be synced:
    python -m src.scripts.sync_extractions --dry-run

    # Sync extractions:
    python -m src.scripts.sync_extractions

    # Explicit URLs (overrides env vars):
    python -m src.scripts.sync_extractions \\
        --source-url "postgresql://..." \\
        --target-url "postgresql://..."

Environment variables:
    REGS_SUPABASE_URL         — Regs Checker Supabase (source)
    REGS_POLICY_NAVIGATOR_URL — Policy Navigator Supabase (target)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def _load_bridge(target_session) -> dict[int, int]:
    """Load law_document_bridge from Policy Navigator into memory.

    Returns:
        Mapping of system_a_doc_family_id → law_id.
    """
    rows = target_session.execute(
        text("SELECT system_a_doc_family_id, law_id FROM law_document_bridge")
    ).fetchall()
    bridge = {row[0]: row[1] for row in rows}
    return bridge


def _get_cursor(target_session) -> int:
    """Get the high-water mark: MAX(system_a_extraction_id) already synced.

    Returns 0 if synced_extractions is empty (first run).
    """
    result = target_session.execute(
        text("SELECT COALESCE(MAX(system_a_extraction_id), 0) FROM synced_extractions")
    ).scalar()
    return result


def _serialize_value(v):
    """Serialize Python dicts/lists to JSON strings for JSONB columns."""
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v


def sync_extractions(
    source_url: str,
    target_url: str,
    dry_run: bool = False,
    batch_size: int = 500,
) -> dict:
    """Incrementally sync extractions from Regs Checker to Policy Navigator.

    Args:
        source_url: Regs Checker Supabase connection string.
        target_url: Policy Navigator Supabase connection string.
        dry_run: If True, report counts without writing.
        batch_size: Number of rows per INSERT batch.

    Returns:
        Summary dict with counts and diagnostics.
    """
    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)

    source_session = sessionmaker(bind=source_engine)()
    target_session = sessionmaker(bind=target_engine)()

    try:
        # Step 1: Load bridge mapping
        bridge = _load_bridge(target_session)
        print(f"Bridge: {len(bridge)} document family → law_id mappings loaded")

        if not bridge:
            print("WARNING: law_document_bridge is empty — no extractions can be mapped.")
            return {
                "cursor_start": 0,
                "cursor_end": 0,
                "source_pending": 0,
                "synced": 0,
                "skipped_no_bridge": 0,
                "bridge_entries": 0,
            }

        # Step 2: Get incremental cursor
        cursor = _get_cursor(target_session)
        print(f"Cursor: syncing extractions with id > {cursor}")

        # Step 3: Count pending extractions in source
        source_pending = source_session.execute(
            text(
                "SELECT COUNT(*) FROM extractions WHERE id > :cursor"
            ),
            {"cursor": cursor},
        ).scalar()

        print(f"Source: {source_pending} extractions pending sync")

        if source_pending == 0:
            print("Nothing to sync — already up to date.")
            return {
                "cursor_start": cursor,
                "cursor_end": cursor,
                "source_pending": 0,
                "synced": 0,
                "skipped_no_bridge": 0,
                "bridge_entries": len(bridge),
            }

        if dry_run:
            # In dry run, show breakdown by bridge coverage
            bridged = source_session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM extractions e
                    JOIN normalized_source_records nsr ON e.source_record_id = nsr.id
                    JOIN document_versions dv ON nsr.document_version_id = dv.id
                    WHERE e.id > :cursor
                    AND dv.family_id IN :family_ids
                    """
                ),
                {"cursor": cursor, "family_ids": tuple(bridge.keys())},
            ).scalar()
            print(f"\n  With bridge mapping:    {bridged}")
            print(f"  Without bridge mapping: {source_pending - bridged}")
            return {
                "cursor_start": cursor,
                "cursor_end": cursor,
                "source_pending": source_pending,
                "synced": 0,
                "skipped_no_bridge": source_pending - bridged,
                "bridge_entries": len(bridge),
            }

        # Step 4: Fetch and sync in batches
        synced = 0
        skipped_no_bridge = 0
        max_id = cursor

        # Fetch extractions joined with their document_family_id
        rows = source_session.execute(
            text(
                """
                SELECT
                    e.id AS extraction_id,
                    e.extraction_type,
                    e.payload,
                    e.evidence_spans,
                    e.confidence_score,
                    e.confidence_tier,
                    e.review_status,
                    e.model_id,
                    e.created_at,
                    dv.family_id AS doc_family_id,
                    nsr.section_path,
                    nsr.text_content AS passage_text
                FROM extractions e
                JOIN normalized_source_records nsr ON e.source_record_id = nsr.id
                JOIN document_versions dv ON nsr.document_version_id = dv.id
                WHERE e.id > :cursor
                ORDER BY e.id
                """
            ),
            {"cursor": cursor},
        ).mappings().all()

        # Build insert batches
        insert_batch = []

        for row in rows:
            extraction_id = row["extraction_id"]
            doc_family_id = row["doc_family_id"]

            # Resolve bridge
            law_id = bridge.get(doc_family_id)
            if law_id is None:
                skipped_no_bridge += 1
                max_id = max(max_id, extraction_id)
                continue

            insert_batch.append({
                "system_a_extraction_id": extraction_id,
                "law_id": law_id,
                "extraction_type": row["extraction_type"],
                "payload": _serialize_value(row["payload"]),
                "evidence_spans": _serialize_value(row["evidence_spans"]),
                "confidence_score": row["confidence_score"],
                "confidence_tier": row["confidence_tier"],
                "review_status": row["review_status"],
                "model_id": row["model_id"],
                "section_path": row["section_path"],
                "passage_text": row["passage_text"],
                "source_created_at": row["created_at"],
                "synced_at": datetime.now(timezone.utc),
            })

            max_id = max(max_id, extraction_id)

            # Flush batch
            if len(insert_batch) >= batch_size:
                _insert_batch(target_session, insert_batch)
                synced += len(insert_batch)
                print(f"  Synced {synced} rows (up to id={max_id})...")
                insert_batch = []

        # Flush remaining
        if insert_batch:
            _insert_batch(target_session, insert_batch)
            synced += len(insert_batch)

        target_session.commit()

        print(f"\nSync complete:")
        print(f"  Synced:            {synced}")
        print(f"  Skipped (no bridge): {skipped_no_bridge}")
        print(f"  Cursor:            {cursor} → {max_id}")

        return {
            "cursor_start": cursor,
            "cursor_end": max_id,
            "source_pending": source_pending,
            "synced": synced,
            "skipped_no_bridge": skipped_no_bridge,
            "bridge_entries": len(bridge),
        }

    finally:
        source_session.close()
        target_session.close()


def _insert_batch(session, batch: list[dict]) -> None:
    """Insert a batch of rows into synced_extractions with idempotent upsert."""
    if not batch:
        return

    columns = list(batch[0].keys())
    col_list = ", ".join(columns)
    param_list = ", ".join(f":{c}" for c in columns)

    session.execute(
        text(
            f"INSERT INTO synced_extractions ({col_list}) "  # noqa: S608
            f"VALUES ({param_list}) "
            f"ON CONFLICT (system_a_extraction_id) DO NOTHING"
        ),
        batch,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Sync extractions: Regs Checker → Policy Navigator"
    )
    parser.add_argument(
        "--source-url",
        default=None,
        help="Regs Checker Supabase URL (or set REGS_SUPABASE_URL)",
    )
    parser.add_argument(
        "--target-url",
        default=None,
        help="Policy Navigator Supabase URL (or set REGS_POLICY_NAVIGATOR_URL)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be synced without writing",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows per INSERT batch (default: 500)",
    )
    args = parser.parse_args()

    source_url = args.source_url or os.environ.get("REGS_SUPABASE_URL")
    target_url = args.target_url or os.environ.get("REGS_POLICY_NAVIGATOR_URL")

    if not source_url:
        print(
            "Error: No source URL. Set --source-url or REGS_SUPABASE_URL.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not target_url:
        print(
            "Error: No target URL. Set --target-url or REGS_POLICY_NAVIGATOR_URL.",
            file=sys.stderr,
        )
        sys.exit(1)

    if source_url == target_url:
        print("Error: Source and target URLs are the same!", file=sys.stderr)
        sys.exit(1)

    print(f"Source (Regs Checker):     {source_url[:60]}...")
    print(f"Target (Policy Navigator): {target_url[:60]}...")
    print(f"Mode:   {'DRY RUN' if args.dry_run else 'LIVE SYNC'}\n")

    summary = sync_extractions(
        source_url, target_url, dry_run=args.dry_run, batch_size=args.batch_size
    )

    print(f"\n{'=' * 60}")
    print(f"Bridge entries:      {summary['bridge_entries']}")
    print(f"Source pending:      {summary['source_pending']}")
    print(f"Synced:              {summary['synced']}")
    print(f"Skipped (no bridge): {summary['skipped_no_bridge']}")
    print(f"Cursor:              {summary['cursor_start']} → {summary['cursor_end']}")


if __name__ == "__main__":
    main()
