"""Detect and optionally merge duplicate canonical law IDs (Phase 3).

Two DocumentFamily records are "duplicates" when they refer to the same law
but were seeded with different canonical IDs — typically one from the formal
bill identifier (US-TX-HB149) and one from a tracker (TMP-TX-AITEXASRESPONS).

The script:
  1. Queries ingestion jobs for (canonical_id, bill_number, jurisdiction)
  2. Groups by normalized (jurisdiction, bill_number)
  3. Prints groups that have 2+ distinct canonical IDs (potential duplicates)
  4. With --apply, merges: re-points DocumentVersion + IngestionJob from the
     duplicate family to the preferred family, then deletes the empty duplicate

Usage:
    # Dry run — list duplicate pairs (no writes):
    python -m src.scripts.consolidate_duplicates

    # Apply merges (irreversible — take a DB backup first):
    python -m src.scripts.consolidate_duplicates --apply

    # Filter to one jurisdiction:
    python -m src.scripts.consolidate_duplicates --jurisdiction TX
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.core.citation_normalizer import find_duplicate_canonicals

DEFAULT_DB_URL = "postgresql://regs:regs@localhost:5434/regs_checker"

_QUERY = """
SELECT
    ij.metadata_->>'canonical_law_id'          AS canonical_id,
    src.jurisdiction_code                       AS jurisdiction,
    dv.bill_number,
    df.canonical_title                          AS title,
    COUNT(DISTINCT e.id) > 0                    AS has_text,
    COUNT(e.id)                                 AS extraction_count,
    df.id                                       AS family_id,
    dv.id                                       AS version_id,
    ij.id                                       AS job_id
FROM ingestion_jobs ij
JOIN document_versions dv  ON dv.id  = ij.document_version_id
JOIN document_families df  ON df.id  = dv.family_id
JOIN sources src            ON src.id = df.source_id
LEFT JOIN normalized_source_records nsr ON nsr.document_version_id = dv.id
LEFT JOIN extractions e ON e.source_record_id = nsr.id
WHERE ij.metadata_->>'canonical_law_id' IS NOT NULL
GROUP BY
    ij.metadata_->>'canonical_law_id',
    src.jurisdiction_code,
    dv.bill_number,
    df.canonical_title,
    df.id, dv.id, ij.id
ORDER BY src.jurisdiction_code, dv.bill_number NULLS LAST
"""  # noqa: S608


def _load_records(session: Session, jurisdiction_filter: str | None) -> list[dict]:
    rows = session.execute(text(_QUERY)).mappings().all()
    records = [dict(r) for r in rows]
    if jurisdiction_filter:
        jf = jurisdiction_filter.upper()
        records = [r for r in records if (r.get("jurisdiction") or "").upper() == jf]
    return records


def _apply_merge(
    session: Session,
    preferred_id: str,
    duplicate_id: str,
    records: list[dict],
) -> None:
    """Merge duplicate_id into preferred_id at the DB level.

    Steps:
      1. Find preferred family_id and duplicate family_id
      2. Re-point duplicate's document_versions.family_id → preferred family
      3. Delete the now-empty duplicate family
    """
    preferred_rec = next((r for r in records if r["canonical_id"] == preferred_id), None)
    dup_rec = next((r for r in records if r["canonical_id"] == duplicate_id), None)

    if not preferred_rec or not dup_rec:
        print(f"    WARN: could not find DB records for {preferred_id!r} or {duplicate_id!r}")
        return

    pref_family_id = preferred_rec["family_id"]
    dup_family_id = dup_rec["family_id"]

    if pref_family_id == dup_family_id:
        print(f"    SKIP: same family_id {pref_family_id} — already merged")
        return

    # Re-point versions
    result = session.execute(
        text(
            "UPDATE document_versions SET family_id = :pref WHERE family_id = :dup"
        ),
        {"pref": pref_family_id, "dup": dup_family_id},
    )
    moved = result.rowcount

    # Delete the now-empty duplicate family (FK on document_versions should be satisfied)
    session.execute(
        text("DELETE FROM document_families WHERE id = :dup"),
        {"dup": dup_family_id},
    )

    print(
        f"    Merged: {duplicate_id!r} (family {dup_family_id}) "
        f"→ {preferred_id!r} (family {pref_family_id}), "
        f"{moved} version(s) re-pointed"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect and optionally merge duplicate canonical law IDs"
    )
    parser.add_argument("--db-url", default=DEFAULT_DB_URL)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually merge duplicates (default: dry run / report only)",
    )
    parser.add_argument(
        "--jurisdiction",
        default=None,
        help="Limit to a single jurisdiction code (e.g. TX)",
    )
    args = parser.parse_args()

    engine = create_engine(args.db_url)

    with Session(engine) as session:
        records = _load_records(session, args.jurisdiction)
        if not records:
            print("No records found (check DB URL or --jurisdiction filter).")
            sys.exit(0)

        dup_groups = find_duplicate_canonicals(records)

        if not dup_groups:
            print("No duplicate canonical IDs detected.")
            sys.exit(0)

        print(f"Found {len(dup_groups)} duplicate group(s):\n")
        for i, group in enumerate(dup_groups, 1):
            print(
                f"  [{i}] {group['jurisdiction']} bill={group['bill_number_norm']!r}"
            )
            print(f"       Preferred  : {group['preferred_id']!r}")
            for dup in group["duplicate_ids"]:
                print(f"       Duplicate  : {dup!r}")
            print(f"       Reason     : {group['reason']}")
            print()

        if not args.apply:
            print("(Dry run — pass --apply to merge)")
            sys.exit(0)

        print("Applying merges...\n")
        for group in dup_groups:
            preferred_id = group["preferred_id"]
            all_records = group["group"]
            for dup_id in group["duplicate_ids"]:
                _apply_merge(session, preferred_id, dup_id, all_records)

        session.commit()
        print("\nDone.")


if __name__ == "__main__":
    main()
