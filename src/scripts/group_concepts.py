"""Run the Phase 5 compliance-concept grouping pass.

Groups normalized extraction fragments into business-facing compliance concepts
(§7 of the unified plan) and persists them to compliance_concepts +
concept_extraction_links + concept_tracker_links.

Usage:
    python -m src.scripts.group_concepts                 # all laws
    python -m src.scripts.group_concepts --dv-id 42      # one law
    python -m src.scripts.group_concepts --review-summary

Run after extraction + verification so member confidence and grounding status
are populated.
"""

from __future__ import annotations

import argparse

from src.core.concept_grouping import run_concept_grouping
from src.core.concept_review import concept_review_counts
from src.db.engine import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5 concept grouping")
    parser.add_argument(
        "--dv-id", type=int, default=None,
        help="Limit to a single document_version id (default: all laws)",
    )
    parser.add_argument(
        "--review-summary", action="store_true",
        help="Print concept review-queue counts after grouping",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        results = run_concept_grouping(
            db,
            document_version_id=args.dv_id,
            on_progress=lambda m: print(m),
        )
        total = sum(r.concepts_created for r in results)
        flagged = sum(r.concepts_flagged for r in results)
        print(f"\n{'=' * 60}")
        print(f"  laws processed:    {len(results)}")
        print(f"  concepts created:  {total}")
        print(f"  flagged for review:{flagged}")

        if args.review_summary:
            counts = concept_review_counts(db)
            print("\n  Review queue summary:")
            for k, v in counts.items():
                print(f"    {k}: {v}")
        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
