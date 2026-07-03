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
  - Publish gate (P3-1, supersedes P2-1): only confidence_tier at or above
    REGS_CONFIDENCE_PUBLISH_MIN_TIER is required to sync — below-floor (D)
    extractions never reach the product database. review_status is no
    longer a publish precondition (Phase 2's approved-only gate was
    deliberately removed in Phase 3 — see docs/phase3_completion_log.md);
    review_status still travels with each synced row for visibility, and
    Policy Navigator's own post-sync review workflow still applies once a
    row exists there.
  - Two-leg sync (P2-6, tier-only as of P3-3): sync_extractions() discovers
    brand-new extractions via the id cursor. sync_updates() (run right
    after, in the same CLI invocation) separately re-checks recently-changed
    rows by updated_at, so a confidence recompute on a previously-synced-or-
    skipped extraction still reaches Policy Navigator even though the id
    cursor never looks backward.

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
from src.core.vocab_loader import get_canonical_codes

# P3-1 (supersedes P2-1's review_status='approved' requirement): only
# confidence tier at or above the configured publish floor determines
# sync eligibility now. Mirrors src/api/routes/v1.py::_tiers_at_or_above
# so both surfaces agree on what "eligible" means.
_TIER_ORDER = ["A", "B", "C", "D"]


def _eligible_tiers(min_tier: str) -> list[str]:
    """Return confidence tiers at or above min_tier (best=A first)."""
    min_tier = (min_tier or "C").upper()
    if min_tier not in _TIER_ORDER:
        return list(_TIER_ORDER)
    idx = _TIER_ORDER.index(min_tier)
    return _TIER_ORDER[: idx + 1]


def _extract_canonical_actor_code(payload: dict[str, any]) -> str | None:
    """Extract canonical actor code from normalized subject field in payload.

    For obligation payloads, maps subject_normalized to the canonical actor
    code (developer, provider, deployer, etc.). Returns None if not an obligation
    or if subject_normalized is not present.
    """
    subject_normalized = payload.get("subject_normalized")
    if not subject_normalized:
        return None

    # Load the actor canonical codes and aliases
    try:
        canonical_codes = get_canonical_codes("actor")
        if subject_normalized in canonical_codes:
            return subject_normalized
    except Exception:
        # If vocab loading fails, gracefully skip enrichment
        pass

    return None


def _extract_obligation_family(payload: dict[str, any]) -> str | None:
    """Extract obligation family code from payload if present.

    Returns the obligation_family field from the payload, or None if not found.
    """
    return payload.get("obligation_family")


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

        # P3-1: confidence tier at or above the publish floor is the sole
        # sync eligibility check now — review_status is no longer required
        # (Phase 2's approved-only gate was deliberately removed; see
        # docs/phase3_completion_log.md). Applied consistently across every
        # count/fetch query below so dry-run reporting matches what a live
        # sync actually inserts.
        eligible_tiers = _eligible_tiers(settings.confidence_publish_min_tier)
        print(f"Publish filter: confidence_tier IN {eligible_tiers}")

        # Step 3: Count pending extractions in source
        source_pending = source_session.execute(
            text(
                """
                SELECT COUNT(*) FROM extractions
                WHERE id > :cursor
                  AND confidence_tier::text = ANY(:tiers)
                """
            ),
            {"cursor": cursor, "tiers": eligible_tiers},
        ).scalar()

        print(f"Source: {source_pending} tier-eligible extractions pending sync")

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
        # was ineligible at the time (below the tier floor) would otherwise be
        # unreachable via `WHERE id > cursor` even after a later confidence
        # recompute raises its tier. That case is the explicit job of the
        # separate updated_at-based leg (see sync_updates(), P2-6/P3-3) run
        # right after this one — it re-checks by change time, not id position,
        # so it always catches an extraction whose tier changed after the fact
        # regardless of where its id sits relative to the cursor.

        # Fetch extractions joined with their document_family_id and canonical_key
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
                    dv.canonical_key,
                    nsr.section_path,
                    nsr.text_content AS passage_text
                FROM extractions e
                JOIN normalized_source_records nsr ON e.source_record_id = nsr.id
                JOIN document_versions dv ON nsr.document_version_id = dv.id
                WHERE e.id > :cursor
                  AND e.confidence_tier::text = ANY(:tiers)
                ORDER BY e.id
                """
            ),
            {"cursor": cursor, "tiers": eligible_tiers},
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

            # Extract enrichment fields for Policy Navigator (DI-1, P3 pre-reload fixes)
            canonical_actor_code = _extract_canonical_actor_code(raw_payload)
            obligation_family = _extract_obligation_family(raw_payload)

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
                # P2-6 bugfix (pre-existing, unrelated to the P2-1 filter change):
                # _insert_batch() builds the INSERT column list directly from these
                # dict keys, but synced_extractions' real columns are
                # section_reference/source_text_excerpt, not section_path/
                # passage_text — this INSERT has likely never actually succeeded;
                # the rows previously in the table came from the bulk_sync_extractions/
                # sync_from_rc_chunk RPCs (see docs/phase0_completion_log.md), not
                # this path.
                "section_reference": row["section_path"],
                "source_text_excerpt": row["passage_text"],
                # Same bug class: the real column is system_a_created_at, not
                # source_created_at.
                "system_a_created_at": row["created_at"],
                "synced_at": datetime.now(UTC),
                # DI-1: Stable join key for Policy Navigator (pre-reload enrichment)
                "canonical_key": row["canonical_key"],
                # P3 pre-reload enrichment: actor code and obligation classification
                "canonical_actor_code": canonical_actor_code,
                "obligation_family": obligation_family,
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


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def sync_updates(
    source_url: str,
    target_url: str,
    dry_run: bool = False,
) -> dict:
    """P2-6, tier-only as of P3-3: propagate changes on already-scanned extractions, by change time.

    sync_extractions() discovers brand-new rows via a MAX(id) cursor, which
    by construction never looks backward — once a higher id has synced, a
    lower id that was below the tier floor at the time becomes unreachable
    to that leg even after a later confidence recompute raises its tier.
    This leg closes that gap by re-checking rows whose `updated_at` has
    advanced past a durable watermark, tracked in Regs Checker's own
    `sync_cursors` table (table_name='extractions',
    destination='policy_navigator_updates') — the same RR6e mechanism
    sync_to_supabase.py already uses for its own leg. Unlike the id cursor,
    this watermark is safe to advance past a still-ineligible row: if that
    row changes again later, Extraction.updated_at's onupdate=func.now()
    guarantees a fresh timestamp that exceeds the watermark, so it will
    naturally be re-checked on a future run.

    Design decision: once a row has been published to Policy Navigator, this
    leg refreshes its CONTENT (payload, evidence_spans, confidence_score,
    confidence_tier, section_reference, source_text_excerpt) on every RC-side
    change, but never touches review_status/consensus_status or any of PN's
    own review columns — those are Policy Navigator's domain once a row
    exists there (its post-sync review workflow can still flag/reject
    independently of RC's review_status). A row that isn't yet eligible
    (tier D) and doesn't yet exist in Policy Navigator is skipped, same as
    the id-cursor leg.
    """
    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)
    source_session = sessionmaker(bind=source_engine)()
    target_session = sessionmaker(bind=target_engine)()

    try:
        bridge = _load_bridge(target_session)
        eligible_tiers = _eligible_tiers(settings.confidence_publish_min_tier)

        watermark_row = source_session.execute(
            text(
                "SELECT last_synced_at FROM sync_cursors "
                "WHERE table_name = 'extractions' AND destination = 'policy_navigator_updates'"
            )
        ).first()
        watermark = watermark_row[0] if watermark_row and watermark_row[0] else _EPOCH

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
                    e.updated_at,
                    dv.family_id AS doc_family_id,
                    dv.canonical_key,
                    nsr.section_path,
                    nsr.text_content AS passage_text
                FROM extractions e
                JOIN normalized_source_records nsr ON e.source_record_id = nsr.id
                JOIN document_versions dv ON nsr.document_version_id = dv.id
                WHERE e.updated_at > :watermark
                ORDER BY e.updated_at
                """
            ),
            {"watermark": watermark},
        ).mappings().all()

        print(f"Update-propagation: {len(rows)} extraction(s) changed since {watermark}")

        if not rows:
            return {
                "updates_checked": 0, "updates_applied": 0,
                "updates_skipped_ineligible": 0, "updates_skipped_no_bridge": 0,
                "watermark_start": watermark, "watermark_end": watermark,
            }

        applied = 0
        skipped_ineligible = 0
        skipped_no_bridge = 0
        max_updated_at = watermark

        for row in rows:
            max_updated_at = max(max_updated_at, row["updated_at"])

            is_eligible = row["confidence_tier"] in eligible_tiers
            if not is_eligible:
                skipped_ineligible += 1
                continue

            law_id = bridge.get(row["doc_family_id"])
            if law_id is None:
                skipped_no_bridge += 1
                continue

            if is_excluded(law_id):
                continue

            if dry_run:
                applied += 1
                continue

            raw_payload = row["payload"]
            if isinstance(raw_payload, str):
                raw_payload = json.loads(raw_payload)
            adapted_payload = adapt_payload_for_sync(row["extraction_type"], raw_payload or {})

            # Extract enrichment fields for Policy Navigator (DI-1, P3 pre-reload fixes)
            canonical_actor_code = _extract_canonical_actor_code(raw_payload)
            obligation_family = _extract_obligation_family(raw_payload)

            target_session.execute(
                text("""
                    INSERT INTO synced_extractions (
                        system_a_extraction_id, law_id, extraction_type, payload,
                        evidence_spans, confidence_score, confidence_tier, review_status,
                        model_id, section_reference, source_text_excerpt, system_a_created_at, synced_at,
                        canonical_key, canonical_actor_code, obligation_family
                    ) VALUES (
                        :eid, :law_id, :etype, :payload,
                        :spans, :score, :tier, :status,
                        :model, :section, :passage, :updated_at, :synced_at,
                        :ckey, :actor_code, :obl_family
                    )
                    ON CONFLICT (system_a_extraction_id) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        evidence_spans = EXCLUDED.evidence_spans,
                        confidence_score = EXCLUDED.confidence_score,
                        confidence_tier = EXCLUDED.confidence_tier,
                        section_reference = EXCLUDED.section_reference,
                        source_text_excerpt = EXCLUDED.source_text_excerpt,
                        synced_at = EXCLUDED.synced_at,
                        canonical_key = EXCLUDED.canonical_key,
                        canonical_actor_code = EXCLUDED.canonical_actor_code,
                        obligation_family = EXCLUDED.obligation_family
                """),
                {
                    "eid": row["extraction_id"],
                    "law_id": law_id,
                    "etype": row["extraction_type"],
                    "payload": _serialize_value(adapted_payload),
                    "spans": _serialize_value(row["evidence_spans"]),
                    "score": row["confidence_score"],
                    "tier": row["confidence_tier"],
                    "status": row["review_status"],
                    "model": row["model_id"],
                    "section": row["section_path"],
                    "passage": row["passage_text"],
                    "updated_at": row["updated_at"],
                    "synced_at": datetime.now(UTC),
                    "ckey": row["canonical_key"],
                    "actor_code": canonical_actor_code,
                    "obl_family": obligation_family,
                },
            )
            applied += 1

        if not dry_run:
            target_session.commit()
            source_session.execute(
                text(
                    """
                    INSERT INTO sync_cursors
                        (table_name, destination, last_synced_at, rows_synced, updated_at)
                    VALUES ('extractions', 'policy_navigator_updates', :watermark, :rows, now())
                    ON CONFLICT (table_name, destination) DO UPDATE SET
                        last_synced_at = EXCLUDED.last_synced_at,
                        rows_synced = sync_cursors.rows_synced + EXCLUDED.rows_synced,
                        updated_at = now()
                    """
                ),
                {"watermark": max_updated_at, "rows": applied},
            )
            source_session.commit()

        print(
            f"Update-propagation: {applied} applied, {skipped_ineligible} not yet eligible, "
            f"{skipped_no_bridge} no bridge mapping"
        )

        return {
            "updates_checked": len(rows),
            "updates_applied": applied,
            "updates_skipped_ineligible": skipped_ineligible,
            "updates_skipped_no_bridge": skipped_no_bridge,
            "watermark_start": watermark,
            "watermark_end": max_updated_at,
        }

    finally:
        source_session.close()
        target_session.close()


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
    parser.add_argument(
        "--skip-updates",
        action="store_true",
        help="Run only the new-extraction leg; skip the P2-6 update-propagation leg",
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

    if not args.skip_updates:
        print(f"\n{'=' * 60}")
        print("Update-propagation leg (P2-6)...\n")
        update_summary = sync_updates(source_url, target_url, dry_run=args.dry_run)
        print(f"\n{'=' * 60}")
        print(f"Updates checked:           {update_summary['updates_checked']}")
        print(f"Updates applied:           {update_summary['updates_applied']}")
        print(f"Skipped (not eligible):    {update_summary['updates_skipped_ineligible']}")
        print(f"Skipped (no bridge):       {update_summary['updates_skipped_no_bridge']}")
        print(f"Watermark:                 {update_summary['watermark_start']} → {update_summary['watermark_end']}")


if __name__ == "__main__":
    main()
