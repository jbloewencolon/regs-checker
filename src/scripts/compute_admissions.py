"""Stamp admission_status into Extraction.metadata_ for all extractions (Phase 4).

Reads every extraction, applies the admission gate logic from
src.core.admission, and writes the result into
``metadata_["admission_status"]``.  Idempotent — reruns stamp the current
decision over any prior value.

Usage:
    # Dry run — count what would be admitted vs needs_review:
    python -m src.scripts.compute_admissions --dry-run

    # Apply (writes to local Docker postgres):
    python -m src.scripts.compute_admissions

    # Custom DB URL:
    python -m src.scripts.compute_admissions --db-url postgresql://...

    # After re-grounding spans (run reground_spans.py first):
    python -m src.scripts.reground_spans && python -m src.scripts.compute_admissions
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.core.admission import compute_admission_status

DEFAULT_DB_URL = "postgresql://regs:regs@localhost:5434/regs_checker"

_SELECT_SQL = """
    SELECT id, evidence_spans, confidence_tier, metadata_
    FROM extractions
    ORDER BY id
"""

_BATCH_SIZE = 500


def _process_batch(
    session: Session,
    batch: list[dict],
    dry_run: bool,
    counts: dict[str, int],
) -> None:
    for row in batch:
        status = compute_admission_status(
            row["evidence_spans"] or [],
            (row["confidence_tier"] or "D"),
        )
        counts[status] = counts.get(status, 0) + 1
        counts["total"] = counts.get("total", 0) + 1

        if dry_run:
            continue

        meta = dict(row["metadata_"] or {})
        if meta.get("admission_status") == status:
            continue  # already correct — skip write

        meta["admission_status"] = status
        session.execute(
            text(
                "UPDATE extractions "
                "SET metadata_ = :meta::jsonb, updated_at = now() "
                "WHERE id = :id"
            ),
            {"meta": json.dumps(meta), "id": row["id"]},
        )

    if not dry_run:
        session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stamp admission_status into Extraction.metadata_"
    )
    parser.add_argument("--db-url", default=DEFAULT_DB_URL)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count decisions without writing to DB",
    )
    args = parser.parse_args()

    engine = create_engine(args.db_url)
    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"Mode: {mode}")
    print(f"DB:   {args.db_url}")
    print()

    counts: dict[str, int] = {}

    with Session(engine) as session:
        result = session.execute(text(_SELECT_SQL))
        batch: list[dict] = []
        batch_num = 0

        for row in result.mappings():
            batch.append(dict(row))
            if len(batch) >= _BATCH_SIZE:
                batch_num += 1
                _process_batch(session, batch, args.dry_run, counts)
                batch = []

        if batch:
            batch_num += 1
            _process_batch(session, batch, args.dry_run, counts)

    print("=" * 50)
    total = counts.get("total", 0)
    admitted = counts.get("admitted", 0)
    needs_review = counts.get("needs_review", 0)
    excluded = counts.get("excluded", 0)

    print(f"Total extractions : {total}")
    print(f"  admitted        : {admitted} ({admitted/max(total,1)*100:.1f}%)")
    print(f"  needs_review    : {needs_review} ({needs_review/max(total,1)*100:.1f}%)")
    print(f"  excluded        : {excluded} ({excluded/max(total,1)*100:.1f}%)")

    if args.dry_run:
        print("\n(Dry run — no changes written)")
    else:
        print("\nadmission_status written to metadata_ on all extractions.")
        print(
            "Next: run python -m src.scripts.recompute_confidence to update tiers, "
            "or visit /dashboard/api/admitted/export.csv to download the accepted set."
        )
    print("Done.")


if __name__ == "__main__":
    main()
