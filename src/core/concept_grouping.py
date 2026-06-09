"""Phase 5b — deterministic compliance-concept grouping.

Groups normalized extraction fragments into business-facing compliance concepts
(§7 of the unified plan).  The product unit is a concept, not a raw extraction
row: the May 2026 run averaged ~9.5 extractions per law, unusable directly.

Grouping is fully deterministic (no fuzzy matching, consistent with Phase 4):
a concept is keyed on (document_version_id, concept_type, regulated_actor_family).

  - obligation          → concept_type classified from action via the ratified
                          obligation_family alias table; actor from subject
  - compliance_mechanism→ concept_type = normalized obligation_family of the
                          mechanism_type; actor from responsible_party
  - rights_protection   → concept_type = "right_" + normalized rights code;
                          actor = duty_bearer; right_holder_family = holder
  - enforcement / threshold-exception extractions attach law-wide as
    enforcement_refs / exceptions on every concept in the law

Confidence is the mean of anchor member scores.  Grounding is derived from the
law's tracker presence (Orrick / IAPP) — tracker_grounded when a tracker covers
the law and no member is in conflict; ungrounded when neither tracker has data.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import structlog
from sqlalchemy import select

from src.core.vocab_loader import get_canonical_codes, normalize
from src.db.models import (
    ComplianceConcept,
    ConceptExtractionLink,
    ConceptReviewStatus,
    ConceptTrackerLink,
    DocumentVersion,
    Extraction,
    ExtractionType,
    ExtractionVerificationStatus,
    NormalizedSourceRecord,
)

logger = structlog.get_logger()

# Extraction types that anchor a concept (define a requirement)
_ANCHOR_TYPES = {
    ExtractionType.obligation,
    ExtractionType.compliance_mechanism,
    ExtractionType.rights_protection,
}

# Tier thresholds mirror src.core.confidence (kept local to avoid a circular import)
_TIER_A, _TIER_B, _TIER_C = 0.85, 0.70, 0.50

# Grounding states that mark a member as conflicting with a tracker
_CONFLICT_STATES = {"tracker_conflict", "iapp_scope_mismatch"}


def _tier_for_score(score: float) -> str:
    if score >= _TIER_A:
        return "A"
    if score >= _TIER_B:
        return "B"
    if score >= _TIER_C:
        return "C"
    return "D"


# Cached obligation_family alias → code map, built once from the ratified table.
_OBLIGATION_FAMILY_ALIASES: Optional[list[tuple[str, str]]] = None


def _obligation_family_alias_pairs() -> list[tuple[str, str]]:
    """Return (raw_term_lower, canonical_code) pairs for obligation_family.

    Sorted longest-term-first so multi-word terms win over substrings.
    Built from the same alias CSV that vocab_loader reads, so the classifier
    stays grounded in the ratified vocabulary.
    """
    global _OBLIGATION_FAMILY_ALIASES
    if _OBLIGATION_FAMILY_ALIASES is not None:
        return _OBLIGATION_FAMILY_ALIASES

    import csv
    import pathlib

    pairs: list[tuple[str, str]] = []
    path = (
        pathlib.Path(__file__).parent.parent.parent
        / "data" / "lookups" / "obligation_family_aliases.csv"
    )
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                term = (row.get("raw_term") or "").strip().lower()
                code = (row.get("proposed_code") or "").strip()
                # Skip REVIEW_* / modality placeholders — not real families
                if not term or not code or code.startswith("REVIEW_"):
                    continue
                if code not in set(get_canonical_codes("obligation_family")):
                    continue
                pairs.append((term, code))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    _OBLIGATION_FAMILY_ALIASES = pairs
    return pairs


def reload_alias_cache() -> None:
    """Reset the obligation_family alias cache (for tests)."""
    global _OBLIGATION_FAMILY_ALIASES
    _OBLIGATION_FAMILY_ALIASES = None


def _classify_obligation_family(action: str) -> str:
    """Deterministically classify an obligation action into an obligation_family.

    Scans the action text for any ratified obligation_family alias term
    (longest-first).  Returns the matched canonical code, or "obligation_general"
    when no alias term appears.
    """
    if not action:
        return "obligation_general"
    text = action.lower()
    for term, code in _obligation_family_alias_pairs():
        if term in text:
            return code
    return "obligation_general"


@dataclass
class _ConceptBucket:
    """Accumulator for one (concept_type, actor_family) group within a law."""

    concept_type: str
    regulated_actor_family: Optional[str]
    right_holder_family: Optional[str] = None
    anchor_ids: list[int] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    trigger_conditions: list[str] = field(default_factory=list)
    deadlines: list[str] = field(default_factory=list)
    has_conflict: bool = False
    has_d_tier: bool = False


@dataclass
class ConceptGroupingResult:
    """Summary returned per document version."""

    document_version_id: int
    concepts_created: int
    concepts_flagged: int
    anchors_grouped: int


def _actor_family(raw: Optional[str], fallback: Optional[str] = None) -> Optional[str]:
    """Normalize an actor string to a canonical family, or None when empty."""
    val = (raw or fallback or "").strip()
    if not val:
        return None
    return normalize("actor", val)


def _grounding_for_extraction(
    db, extraction_id: int
) -> Optional[str]:
    """Return the most recent grounding_status for an extraction, if recorded."""
    return db.scalars(
        select(ExtractionVerificationStatus.grounding_status)
        .where(ExtractionVerificationStatus.extraction_id == extraction_id)
        .order_by(ExtractionVerificationStatus.id.desc())
        .limit(1)
    ).first()


def _tracker_refs_for_law(db, dv: DocumentVersion) -> list[ConceptTrackerLink]:
    """Build (unpersisted) tracker links for a law from Orrick + IAPP presence."""
    from src.core.iapp_alignment import get_iapp_entry_for_context

    df = dv.family
    s = df.source if df else None
    jur = (s.jurisdiction_code if s else "") or "??"
    bill = (df.short_cite if df else "") or ""
    ref = f"{jur}/{bill}".strip("/")

    links: list[ConceptTrackerLink] = []

    # Orrick presence — any non-empty key_requirements / enforcement / summary
    meta = (df.metadata_ if df else None) or {}
    orrick_present = bool(
        (meta.get("key_requirements") or "").strip()
        or (meta.get("enforcement_penalties") or "").strip()
        or (meta.get("orrick_summary") or "").strip()
    )
    if orrick_present:
        links.append(
            ConceptTrackerLink(
                tracker_source="orrick",
                tracker_ref=ref,
                match_status="tracker_grounded",
            )
        )

    # IAPP presence
    ctx = {
        "jurisdiction": jur,
        "jurisdiction_name": (s.jurisdiction_name if s else None),
        "short_cite": bill,
        "bill_id": meta.get("bill_id"),
    }
    try:
        entry = get_iapp_entry_for_context(ctx)
    except Exception:
        entry = None
    if entry is not None and entry.has_data:
        links.append(
            ConceptTrackerLink(
                tracker_source="iapp",
                tracker_ref=ref,
                match_status="tracker_grounded",
            )
        )

    return links


def group_concepts_for_dv(
    db,
    dv_id: int,
    run_id: int | None = None,
) -> ConceptGroupingResult:
    """Group all extractions for one document version into compliance concepts.

    Idempotent: deletes any existing concepts for the law before regrouping,
    so re-runs converge on the same deterministic set.
    """
    dv = db.get(DocumentVersion, dv_id)
    if dv is None:
        return ConceptGroupingResult(dv_id, 0, 0, 0)

    # Clear prior concepts for this law (cascade removes links).
    existing = db.scalars(
        select(ComplianceConcept).where(
            ComplianceConcept.document_version_id == dv_id
        )
    ).all()
    for c in existing:
        db.delete(c)
    db.flush()

    extractions = db.scalars(
        select(Extraction).where(
            Extraction.source_record_id.in_(
                select(NormalizedSourceRecord.id).where(
                    NormalizedSourceRecord.document_version_id == dv_id
                )
            )
        )
    ).all()

    if not extractions:
        return ConceptGroupingResult(dv_id, 0, 0, 0)

    buckets: dict[tuple[str, Optional[str]], _ConceptBucket] = {}
    enforcement_refs: list[dict[str, Any]] = []
    enforcement_ids: list[int] = []
    exception_refs: list[dict[str, Any]] = []
    exception_ids: list[int] = []
    anchors_grouped = 0

    for ext in extractions:
        et = ext.extraction_type
        payload = ext.payload or {}

        # --- Supporting: enforcement (standalone + embedded) ---
        if et == ExtractionType.enforcement:
            enforcement_refs.append({
                "extraction_id": ext.id,
                "penalty_type": payload.get("penalty_type"),
                "enforcing_body": payload.get("enforcing_body"),
            })
            enforcement_ids.append(ext.id)
            continue

        # --- Supporting: thresholds / exceptions ---
        if et in (ExtractionType.threshold, ExtractionType.exception):
            sub_type = payload.get("threshold_sub_type")
            if et == ExtractionType.exception or sub_type == "exemption":
                exception_refs.append({
                    "extraction_id": ext.id,
                    "text": payload.get("threshold_value")
                    or payload.get("exception_description")
                    or payload.get("description"),
                })
                exception_ids.append(ext.id)
            continue

        # --- Anchors ---
        if et not in _ANCHOR_TYPES:
            continue

        if et == ExtractionType.obligation:
            concept_type = _classify_obligation_family(payload.get("action", ""))
            actor = _actor_family(
                payload.get("subject_normalized"), payload.get("subject")
            )
            holder = None
            action = payload.get("action") or ""
            trigger = payload.get("condition")
            timeline = payload.get("timeline") or {}
            deadline = None
            if isinstance(timeline, dict):
                deadline = timeline.get("effective_date") or timeline.get("compliance_date")
            # Embedded enforcement → law-wide enforcement ref
            emb = payload.get("enforcement")
            if isinstance(emb, dict) and (emb.get("penalty_type") or emb.get("max_civil_penalty_usd")):
                enforcement_refs.append({
                    "extraction_id": ext.id,
                    "penalty_type": emb.get("penalty_type"),
                    "enforcing_body": emb.get("enforcing_body"),
                })

        elif et == ExtractionType.compliance_mechanism:
            mech = payload.get("mechanism_type") or ""
            concept_type = normalize("obligation_family", mech)
            actor = _actor_family(
                payload.get("responsible_party_normalized"),
                payload.get("responsible_party"),
            )
            holder = None
            action = payload.get("description") or mech
            trigger = None
            deadline = None

        else:  # rights_protection
            right_code = normalize("rights", payload.get("right_type") or "")
            concept_type = f"right_{right_code}"
            actor = _actor_family(payload.get("duty_bearer"))
            holder = _actor_family(
                payload.get("right_holder_normalized"), payload.get("right_holder")
            )
            action = payload.get("right_description") or payload.get("right_type") or ""
            trigger = payload.get("trigger_condition")
            deadline = None

        key = (concept_type, actor)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _ConceptBucket(
                concept_type=concept_type,
                regulated_actor_family=actor,
                right_holder_family=holder,
            )
            buckets[key] = bucket

        bucket.anchor_ids.append(ext.id)
        bucket.confidences.append(ext.confidence_score or 0.0)
        if action:
            bucket.actions.append(action)
        if trigger:
            bucket.trigger_conditions.append(trigger)
        if deadline:
            bucket.deadlines.append(str(deadline))
        if holder and not bucket.right_holder_family:
            bucket.right_holder_family = holder

        # Conflict / tier signals from persisted verification status
        gstatus = _grounding_for_extraction(db, ext.id)
        if gstatus in _CONFLICT_STATES:
            bucket.has_conflict = True
        tier = (
            ext.confidence_tier.value
            if hasattr(ext.confidence_tier, "value")
            else str(ext.confidence_tier or "")
        )
        if tier == "D":
            bucket.has_d_tier = True

        anchors_grouped += 1

    if not buckets:
        return ConceptGroupingResult(dv_id, 0, 0, 0)

    # Tracker references for the law (shared by all concepts).
    tracker_links_proto = _tracker_refs_for_law(db, dv)
    tracker_ref_ids = [
        f"{l.tracker_source}:{l.tracker_ref}" for l in tracker_links_proto
    ]
    has_tracker = bool(tracker_links_proto)

    concepts_created = 0
    concepts_flagged = 0

    for (concept_type, actor), bucket in buckets.items():
        mean_conf = (
            sum(bucket.confidences) / len(bucket.confidences)
            if bucket.confidences else 0.0
        )
        tier = _tier_for_score(mean_conf)

        # Grounding (§7 principle 6)
        if bucket.has_conflict:
            grounding = "tracker_conflict"
        elif has_tracker:
            grounding = "tracker_grounded"
        else:
            grounding = "ungrounded"

        # Review status: flag conflicts and D-tier requirements for an analyst.
        if bucket.has_conflict or bucket.has_d_tier or tier == "D":
            review_status = ConceptReviewStatus.flagged
            concepts_flagged += 1
        else:
            review_status = ConceptReviewStatus.pending

        actor_label = actor or "any party"
        title = f"{actor_label} — {concept_type.replace('_', ' ')}"
        summary = _dedup_join(bucket.actions, limit=5)
        required_action = bucket.actions[0] if bucket.actions else None
        trigger_condition = bucket.trigger_conditions[0] if bucket.trigger_conditions else None
        deadline = sorted(bucket.deadlines)[0] if bucket.deadlines else None

        source_ids = list(dict.fromkeys(
            bucket.anchor_ids + enforcement_ids + exception_ids
        ))

        concept = ComplianceConcept(
            document_version_id=dv_id,
            concept_type=concept_type,
            regulated_actor_family=actor,
            right_holder_family=bucket.right_holder_family,
            title=title,
            summary=summary,
            trigger_condition=trigger_condition,
            required_action=required_action,
            deadline=deadline,
            exceptions=exception_refs,
            enforcement_refs=enforcement_refs,
            source_extraction_ids=source_ids,
            tracker_ref_ids=tracker_ref_ids,
            confidence_score=round(mean_conf, 4),
            confidence_tier=tier,
            grounding_status=grounding,
            review_status=review_status,
            member_count=len(bucket.anchor_ids),
            run_id=run_id,
        )
        db.add(concept)
        db.flush()  # obtain concept.id

        # Links: anchors + law-wide enforcement / exception members
        for eid in bucket.anchor_ids:
            db.add(ConceptExtractionLink(
                concept_id=concept.id, extraction_id=eid, role="anchor",
            ))
        for eid in enforcement_ids:
            db.add(ConceptExtractionLink(
                concept_id=concept.id, extraction_id=eid, role="enforcement",
            ))
        for eid in exception_ids:
            db.add(ConceptExtractionLink(
                concept_id=concept.id, extraction_id=eid, role="exception",
            ))

        # Tracker links — clone the prototypes per concept, marking conflict.
        for proto in tracker_links_proto:
            status = "tracker_conflict" if bucket.has_conflict else proto.match_status
            db.add(ConceptTrackerLink(
                concept_id=concept.id,
                tracker_source=proto.tracker_source,
                tracker_ref=proto.tracker_ref,
                match_status=status,
            ))

        concepts_created += 1

    return ConceptGroupingResult(
        document_version_id=dv_id,
        concepts_created=concepts_created,
        concepts_flagged=concepts_flagged,
        anchors_grouped=anchors_grouped,
    )


def _dedup_join(items: list[str], limit: int = 5, sep: str = " | ") -> str:
    """Dedup while preserving order; join up to `limit` items."""
    seen: list[str] = []
    for it in items:
        norm = it.strip()
        if norm and norm not in seen:
            seen.append(norm)
        if len(seen) >= limit:
            break
    return sep.join(seen)


def run_concept_grouping(
    db,
    document_version_id: int | None = None,
    run_id: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> list[ConceptGroupingResult]:
    """Run the concept-grouping pass across one or all document versions.

    Args:
        db: SQLAlchemy session.
        document_version_id: limit to a single law (None = all laws with extractions).
        run_id: optional ExtractionRun id to stamp on created concepts.
        on_progress: optional progress callback.

    Returns a ConceptGroupingResult per processed document version.
    """
    from sqlalchemy import distinct

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    if document_version_id is not None:
        dv_ids = [document_version_id]
    else:
        dv_ids = db.scalars(
            select(distinct(NormalizedSourceRecord.document_version_id)).where(
                NormalizedSourceRecord.id.in_(
                    select(distinct(Extraction.source_record_id))
                )
            )
        ).all()

    _log(f"Concept grouping across {len(dv_ids)} document version(s)...")
    results: list[ConceptGroupingResult] = []
    for dv_id in dv_ids:
        result = group_concepts_for_dv(db, dv_id, run_id=run_id)
        db.commit()
        if result.concepts_created:
            _log(
                f"  dv={dv_id}: {result.concepts_created} concepts "
                f"({result.concepts_flagged} flagged) "
                f"from {result.anchors_grouped} anchors"
            )
        results.append(result)

    total_concepts = sum(r.concepts_created for r in results)
    total_flagged = sum(r.concepts_flagged for r in results)
    _log(
        f"Concept grouping complete: {total_concepts} concepts "
        f"({total_flagged} flagged for review) across {len(results)} laws"
    )
    return results
