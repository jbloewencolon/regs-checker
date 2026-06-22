"""Idempotent re-grounding of evidence spans using the updated 4-tier matcher.

Re-runs src.core.text_grounding.verify_evidence_spans against stored passage
text for every extraction that has at least one unverified span.  Updates
the evidence_spans column in-place (no LLM calls).

After running this script, run src.scripts.recompute_confidence to update
confidence_score / confidence_tier to reflect the improved grounding.

Usage:
    # Dry run — report how many spans would flip (no writes):
    python -m src.scripts.reground_spans --dry-run

    # Apply re-grounding (writes updated evidence_spans to DB):
    python -m src.scripts.reground_spans

    # Limit to N extractions (for testing):
    python -m src.scripts.reground_spans --limit 100

    # Target only a specific extraction type:
    python -m src.scripts.reground_spans --type obligation
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DEFAULT_DB_URL = "postgresql://regs:regs@localhost:5434/regs_checker"


def _reground_batch(
    session: Session,
    batch: list[dict],
    dry_run: bool,
) -> dict:
    """Process one batch of extractions; returns per-batch statistics."""
    from src.core.text_grounding import verify_evidence_spans

    stats = {
        "processed": 0,
        "updated": 0,
        "spans_flipped": 0,
        "spans_total": 0,
    }

    for row in batch:
        extraction_id = row["id"]
        passage_text = row["text_content"] or ""
        spans = row["evidence_spans"] or []

        if not spans or not passage_text:
            continue

        stats["processed"] += 1
        stats["spans_total"] += len(spans)

        re_verified = verify_evidence_spans(
            spans, passage_text, agent_name=f"reground:{row['extraction_type']}"
        )

        old_verified = {s.get("text"): s.get("verified", False) for s in spans}
        flipped = sum(
            1
            for s in re_verified
            if s.get("verified") and not old_verified.get(s.get("text"), True)
        )

        if flipped == 0:
            continue

        stats["updated"] += 1
        stats["spans_flipped"] += flipped

        if not dry_run:
            session.execute(
                text(
                    "UPDATE extractions SET evidence_spans = :spans::jsonb, updated_at = now() "
                    "WHERE id = :id"
                ),
                {"spans": json.dumps(re_verified), "id": extraction_id},
            )

    if not dry_run:
        session.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-ground evidence spans using updated 4-tier matcher"
    )
    parser.add_argument(
        "--db-url",
        default=DEFAULT_DB_URL,
        help="SQLAlchemy DB URL (default: local Docker postgres)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report flippable spans without writing to DB",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N extractions (default: all)",
    )
    parser.add_argument(
        "--type",
        dest="extraction_type",
        default=None,
        help="Filter to a specific extraction_type (e.g. 'obligation')",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="DB fetch batch size (default: 200)",
    )
    args = parser.parse_args()

    engine = create_engine(args.db_url)

    where_clauses = [
        "e.evidence_spans IS NOT NULL",
        "jsonb_array_length(e.evidence_spans) > 0",
        "EXISTS (SELECT 1 FROM jsonb_array_elements(e.evidence_spans) s "
        "WHERE (s->>'verified')::boolean IS NOT TRUE)",
    ]
    if args.extraction_type:
        where_clauses.append(f"e.extraction_type = '{args.extraction_type}'")  # noqa: S608

    where_sql = " AND ".join(where_clauses)
    limit_sql = f"LIMIT {args.limit}" if args.limit else ""

    select_sql = f"""
        SELECT e.id, e.extraction_type, e.evidence_spans, e.payload,
               nsr.text_content
        FROM extractions e
        JOIN normalized_source_records nsr ON nsr.id = e.source_record_id
        WHERE {where_sql}
        ORDER BY e.id
        {limit_sql}
    """  # noqa: S608

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"Mode:       {mode}")
    print(f"DB:         {args.db_url}")
    print(f"Type filter:{args.extraction_type or ' all'}")
    print()

    totals: dict[str, int] = {
        "processed": 0,
        "updated": 0,
        "spans_flipped": 0,
        "spans_total": 0,
    }

    with Session(engine) as session:
        result = session.execute(text(select_sql))
        batch: list[dict] = []
        batch_num = 0

        for row in result.mappings():
            batch.append(dict(row))

            if len(batch) >= args.batch_size:
                batch_num += 1
                stats = _reground_batch(session, batch, args.dry_run)
                for k in totals:
                    totals[k] += stats[k]
                print(
                    f"  Batch {batch_num}: processed={stats['processed']} "
                    f"updated={stats['updated']} "
                    f"spans_flipped={stats['spans_flipped']}"
                )
                batch = []

        if batch:
            batch_num += 1
            stats = _reground_batch(session, batch, args.dry_run)
            for k in totals:
                totals[k] += stats[k]
            print(
                f"  Batch {batch_num}: processed={stats['processed']} "
                f"updated={stats['updated']} "
                f"spans_flipped={stats['spans_flipped']}"
            )

    print()
    print("=" * 60)
    print(f"Extractions processed : {totals['processed']}")
    print(f"Extractions updated   : {totals['updated']}")
    print(f"Spans examined        : {totals['spans_total']}")
    print(f"Spans flipped to True : {totals['spans_flipped']}")
    if args.dry_run:
        print("\n(Dry run — no changes written)")
    else:
        print(
            "\nNext step: run python -m src.scripts.recompute_confidence "
            "to update confidence tiers."
        )
    print("Done.")


if __name__ == "__main__":
    main()
