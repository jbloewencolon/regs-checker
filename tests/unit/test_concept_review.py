"""Unit tests for Phase 5c concept review priority logic.

The DB-backed queue (get_concept_review_queue / resolve_concept) is exercised in
integration against live Postgres.  These unit tests pin the deterministic
priority-band ordering that decides which concepts an analyst sees first.
"""

from __future__ import annotations

from src.core.concept_review import _priority_band
from src.db.models import ConceptReviewStatus


class _FakeConcept:
    def __init__(self, grounding_status, review_status, confidence_tier):
        self.grounding_status = grounding_status
        self.review_status = review_status
        self.confidence_tier = confidence_tier


class TestPriorityBand:
    def test_tracker_conflict_is_highest(self):
        c = _FakeConcept("tracker_conflict", ConceptReviewStatus.pending, "B")
        assert _priority_band(c) == 0

    def test_flagged_d_tier_second(self):
        c = _FakeConcept("tracker_grounded", ConceptReviewStatus.flagged, "D")
        assert _priority_band(c) == 1

    def test_flagged_other_third(self):
        c = _FakeConcept("tracker_grounded", ConceptReviewStatus.flagged, "B")
        assert _priority_band(c) == 2

    def test_ungrounded_fourth(self):
        c = _FakeConcept("ungrounded", ConceptReviewStatus.pending, "B")
        assert _priority_band(c) == 3

    def test_clean_concept_lowest(self):
        c = _FakeConcept("tracker_grounded", ConceptReviewStatus.approved, "A")
        assert _priority_band(c) == 4

    def test_conflict_outranks_flagged_d(self):
        conflict = _FakeConcept("tracker_conflict", ConceptReviewStatus.flagged, "D")
        flagged_d = _FakeConcept("tracker_grounded", ConceptReviewStatus.flagged, "D")
        assert _priority_band(conflict) < _priority_band(flagged_d)

    def test_full_ordering_is_strict(self):
        bands = [
            _priority_band(_FakeConcept("tracker_conflict", ConceptReviewStatus.pending, "B")),
            _priority_band(_FakeConcept("tracker_grounded", ConceptReviewStatus.flagged, "D")),
            _priority_band(_FakeConcept("tracker_grounded", ConceptReviewStatus.flagged, "C")),
            _priority_band(_FakeConcept("ungrounded", ConceptReviewStatus.pending, "B")),
            _priority_band(_FakeConcept("tracker_grounded", ConceptReviewStatus.approved, "A")),
        ]
        assert bands == sorted(bands)
        assert len(set(bands)) == 5  # all distinct
