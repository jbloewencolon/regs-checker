"""RR0.3 — Backfill/invalidate verification data from the pre-RR0.1 broken path.

The pre-RR0.1 CV agent rewrote confidence scores on every verification pass
without the purge gate, silently lowering scores on passages that had already
been extracted correctly.  ExtractionVerificationStatus rows capture the
before/after values; this script restores the originals and clears all
verification state so the next verify pass starts from a clean baseline.

Usage:
    python -m src.scripts.backfill_verification [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import delete, select

from src.db.engine import SessionLocal
from src.db.models import (
    ConfidenceTier,
    Extraction,
    ExtractionVerificationStatus,
    VerificationRunSummary,
)


def run(dry_run: bool = False) -> None:
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(ExtractionVerificationStatus).where(
                ExtractionVerificationStatus.confidence_before.is_not(None),
                ExtractionVerificationStatus.extraction_id.is_not(None),
            )
        ).all()

        restored = 0
        skipped = 0
        for row in rows:
            extraction = db.get(Extraction, row.extraction_id)
            if extraction is None:
                skipped += 1
                continue

            if not dry_run:
                extraction.confidence_score = row.confidence_before
                if row.tier_before:
                    try:
                        extraction.confidence_tier = ConfidenceTier(row.tier_before)
                    except ValueError:
                        pass
            restored += 1

        evs_count = db.scalar(
            select(ExtractionVerificationStatus.__table__.c["id"].count())
        ) or 0
        vrs_count = db.scalar(
            select(VerificationRunSummary.__table__.c["id"].count())
        ) or 0

        if not dry_run:
            db.execute(delete(ExtractionVerificationStatus))
            db.execute(delete(VerificationRunSummary))
            db.commit()

        print(f"Confidence restored : {restored}")
        print(f"Extractions missing : {skipped}")
        print(f"ExtractionVerificationStatus rows deleted: {evs_count}")
        print(f"VerificationRunSummary rows deleted      : {vrs_count}")
        if dry_run:
            print("(dry-run — no changes committed)")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be changed without committing.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
