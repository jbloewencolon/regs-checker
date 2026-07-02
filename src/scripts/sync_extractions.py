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
  - Publish gate (P2-1): only extractions with review_status='approved' and
    confidence_tier at or above REGS_CONFIDENCE_PUBLISH_MIN_TIER are eligible
    to sync at all — unapproved or below-floor extractions never reach the
    product database.
  - Two-leg sync (P2-1 + P2-6): sync_extractions() discovers brand-new
    extractions via the id cursor. sync_updates() (run right after, in the
    same CLI invocation) separately re-checks recently-changed rows by
    updated_at, so a review decision or confidence recompute on a
    previously-synced-or-skipped extraction still reaches Policy Navigator
    even though the id cursor never looks backward.

Usage:
    # Dry run — show what would be synced/updated:
    python -m src.scripts.sync_extractions --dry-run

    # Sync new extractions + propagate updates:
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
from datetime import UTC, datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.core.config import settings
from src.core.payload_adapter import adapt_payload_for_sync
from src.core.sync_exclusions import is_excluded

# P2-1: only extractions that have cleared human review, at or above the
# configured publish tier, may reach the product database. Mirrors
# src/api/routes/v1.py::_tiers_at_or_above so both surfaces agree on what
# "eligible" means.
_TIER_ORDER = ["A", "B", "C", "D"]


def _eligible_tiers(min_tier: str) -> list[str]:
    """Return confidence tiers at or above min_tier (best=A first)."""
    min_tier = (min_tier or "C").upper()
    if min_tier not in _TIER_ORDER:
        return list(_TIER_ORDER)
    idx = _TIER_ORDER.index(min_tier)
    return _TIER_ORDER[: idx + 1]


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

        # P2-1: only approved extractions at or above the publish tier are
        # eligible to reach the product database. Applied consistently across
        # every count/fetch query below so dry-run reporting matches what a
        # live sync actually inserts.
        eligible_tiers = _eligible_tiers(settings.confidence_publish_min_tier)
        print(f"Publish filter: review_status='approved' AND confidence_tier IN {eligible_tiers}")

        # Step 3: Count pending extractions in source
        source_pending = source_session.execute(
            text(
                """
                SELECT COUNT(*) FROM extractions
                WHERE id > :cursor
                  AND review_status = 'approved'
                  AND confidence_tier::text = ANY(:tiers)
                """
            ),
            {"cursor": cursor, "tiers": eligible_tiers},
        ).scalar()

        print(f"Source: {source_pending} approved, tier-eligible extractions pending sync")

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
                    AND e.review_status = 'approved'
                    AND e.confidence_tier::text = ANY(:tiers)
                    AND dv.family_id IN :family_ids
                    """
                ),
                {"cursor": cursor, "tiers": eligible_tiers, "family_ids": tuple(bridge.keys())},
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

        # Design note: this id-cursor leg's job is discovering NEW extractions.
        # It intentionally does not try to be "skip-proof" — the cursor is a
        # MAX(id) watermark, so once a higher id has synced, a lower id that
        # was ineligible at the time (still pending review) would otherwise be
        # unreachable via `WHERE id > cursor` even after a reviewer later
        # approves it. That case is the explicit job of the separate
        # updated_at-based leg (see sync_updates(), P2-6) run right after this
        # one — it re-checks by change time, not id position, so it always
        # catches an extraction whose review_status/tier changed after the
        # fact regardless of where its id sits relative to the cursor.

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
        skipped_excluded = 0

        for row in rows:
            extraction_id = row["extraction_id"]
            doc_family_id = row["doc_family_id"]

            # Resolve bridge
            law_id = bridge.get(doc_family_id)
            if law_id is None:
                skipped_no_bridge += 1
                max_id = max(max_id, extraction_id)
                continue

            # Check sync exclusion list (known bad law_ids)
            if is_excluded(law_id):
                skipped_excluded += 1
                max_id = max(max_id, extraction_id)
                continue

            # Adapt payload to Policy Navigator's expected format
            raw_payload = row["payload"]
            if isinstance(raw_payload, str):
                raw_payload = json.loads(raw_payload)
            adapted_payload = adapt_payload_for_sync(
                row["extraction_type"], raw_payload or {}
            )

            insert_batch.append({
                "system_a_extraction_id": extraction_id,
                "law_id": law_id,
                "extraction_type": row["extraction_type"],
                "payload": _serialize_value(adapted_payload),
                "evidence_spans": _serialize_value(row["evidence_spans"]),
                "confidence_score": row["confidence_score"],
                "confidence_tier": row["confidence_tier"],
                "review_status": row["review_status"],
                "model_id": row["model_id"],
                "section_path": row["section_path"],
                "passage_text": row["passage_text"],
                "source_created_at": row["created_at"],
                "synced_at": datetime.now(UTC),
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

        print("\nSync complete:")
        print(f"  Synced:              {synced}")
        print(f"  Skipped (no bridge): {skipped_no_bridge}")
        print(f"  Skipped (excluded):  {skipped_excluded}")
        print(f"  Cursor:              {cursor} → {max_id}")

        return {
            "cursor_start": cursor,
            "cursor_end": max_id,
            "source_pending": source_pending,
            "synced": synced,
            "skipped_no_bridge": skipped_no_bridge,
            "skipped_excluded": skipped_excluded,
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
