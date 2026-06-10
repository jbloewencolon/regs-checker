"""Phase 5c — compliance-concept review queue.

Concepts (not raw extraction rows) are the unit handed to a human reviewer and,
downstream, to the deferred law-card builder.  This module surfaces the concepts
that need analyst attention and records review decisions.

Priority ordering (deterministic, highest first):
  1. tracker_conflict grounding  — the concept contradicts Orrick/IAPP
  2. flagged + D-tier            — low-confidence requirement on a card
  3. flagged (other)             — e.g. a D-tier member dragged it down
  4. ungrounded                  — no tracker confirms it
Within a priority band, lower confidence_score sorts first.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from src.db.models import (
    ComplianceConcept,
    ConceptReviewStatus,
    DocumentVersion,
)


# Priority bands — lower number = reviewed first.
def _priority_band(concept: ComplianceConcept) -> int:
    if concept.grounding_status == "tracker_conflict":
        return 0
    if concept.review_status == ConceptReviewStatus.flagged and concept.confidence_tier == "D":
        return 1
    if concept.review_status == ConceptReviewStatus.flagged:
        return 2
    if concept.grounding_status == "ungrounded":
        return 3
    return 4


@dataclass
class ConceptReviewItem:
    """A concept surfaced for review, with its computed priority."""

    concept_id: int
    document_version_id: int
    jurisdiction: str | None
    concept_type: str
    regulated_actor_family: str | None
    title: str
    confidence_tier: str | None
    confidence_score: float | None
    grounding_status: str
    review_status: str
    member_count: int
    priority_band: int


def get_concept_review_queue(
    db,
    limit: int | None = 100,
    jurisdiction: str | None = None,
    include_pending: bool = False,
) -> list[ConceptReviewItem]:
    """Return concepts needing review, ordered by priority then low confidence.

    Args:
        db: SQLAlchemy session.
        limit: max items to return (None = all).
        jurisdiction: optional jurisdiction_code filter (e.g. "CO").
        include_pending: when True, also include plain `pending` concepts
            (default surfaces only flagged / conflicted / ungrounded ones).

    Returns deterministically ordered ConceptReviewItem list.
    """
    stmt = select(ComplianceConcept)

    if not include_pending:
        # Default queue: flagged review_status OR a grounding problem.
        stmt = stmt.where(
            (ComplianceConcept.review_status == ConceptReviewStatus.flagged)
            | (ComplianceConcept.grounding_status.in_(
                ["tracker_conflict", "ungrounded"]
            ))
        )

    concepts = db.scalars(stmt).all()

    # Resolve jurisdiction labels (and optional filter) via the law's source.
    items: list[ConceptReviewItem] = []
    for c in concepts:
        jur = _jurisdiction_for_concept(db, c)
        if jurisdiction and jur != jurisdiction:
            continue
        items.append(ConceptReviewItem(
            concept_id=c.id,
            document_version_id=c.document_version_id,
            jurisdiction=jur,
            concept_type=c.concept_type,
            regulated_actor_family=c.regulated_actor_family,
            title=c.title,
            confidence_tier=c.confidence_tier,
            confidence_score=c.confidence_score,
            grounding_status=c.grounding_status,
            review_status=(
                c.review_status.value
                if hasattr(c.review_status, "value") else str(c.review_status)
            ),
            member_count=c.member_count,
            priority_band=_priority_band(c),
        ))

    # Deterministic sort: priority band, then lowest confidence, then id.
    items.sort(key=lambda i: (
        i.priority_band,
        i.confidence_score if i.confidence_score is not None else 0.0,
        i.concept_id,
    ))

    if limit is not None:
        items = items[:limit]
    return items


def _jurisdiction_for_concept(db, concept: ComplianceConcept) -> str | None:
    """Look up the jurisdiction_code for a concept's law."""
    dv = db.get(DocumentVersion, concept.document_version_id)
    if dv is None or dv.family is None or dv.family.source is None:
        return None
    return dv.family.source.jurisdiction_code


def resolve_concept(
    db,
    concept_id: int,
    status: ConceptReviewStatus,
) -> bool:
    """Record an analyst's review decision on a concept.

    Returns True if the concept was found and updated, False otherwise.
    """
    concept = db.get(ComplianceConcept, concept_id)
    if concept is None:
        return False
    concept.review_status = status
    db.flush()
    return True


def concept_review_counts(db) -> dict[str, int]:
    """Return summary counts for a review dashboard.

    Keys: total, pending, flagged, approved, rejected, tracker_conflict,
    ungrounded.
    """
    concepts = db.scalars(select(ComplianceConcept)).all()
    counts = {
        "total": len(concepts),
        "pending": 0,
        "flagged": 0,
        "approved": 0,
        "rejected": 0,
        "tracker_conflict": 0,
        "ungrounded": 0,
    }
    for c in concepts:
        rs = c.review_status.value if hasattr(c.review_status, "value") else str(c.review_status)
        if rs in counts:
            counts[rs] += 1
        if c.grounding_status == "tracker_conflict":
            counts["tracker_conflict"] += 1
        elif c.grounding_status == "ungrounded":
            counts["ungrounded"] += 1
    return counts
