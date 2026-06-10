"""Post-extraction verification pipeline — split from extractor.py (RR7a).

Runs three independent verification layers after primary extraction:
  1. Cross-Validation: second LLM reviews each extraction against source passage
  2. Gap Detection: identifies obligations the primary extraction missed
  3. Citation Verification: validates section_reference / cross_reference fields

Also runs IAPP alignment (Phase 4b) to annotate extractions with tracker data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select

from src.core.confidence import compute_confidence
from src.core.orrick_validation import validate_extraction_against_orrick
from src.db.models import (
    ConfidenceTier,
    DocumentVersion,
    Extraction,
    ExtractionVerificationStatus,
    NormalizedSourceRecord,
    SectionTriageResult,
    TriageDecision,
    VerificationRunSummary,
)
from src.schemas.extraction import EXTRACTION_TYPE_SCHEMAS

logger = structlog.get_logger()

MIN_PASSAGE_LENGTH = 150  # keep in sync with extractor.py


@dataclass
class VerificationResult:
    """Combined result from all verification agents for a document."""

    document_version_id: int
    document_label: str

    cross_validation_passages: int
    cross_validation_valid: int
    cross_validation_flagged: int
    cross_validation_avg_accuracy: float
    cross_validation_issues: list[dict[str, Any]]

    gap_detection_passages: int
    gaps_found: int
    high_confidence_gaps: int
    gap_candidates: list[dict[str, Any]]

    citations_checked: int
    citations_verified: int
    citations_unverified: int
    citation_issues: list[dict[str, Any]]

    total_input_tokens: int
    total_output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


def _iapp_has_data_for_ctx(ctx: dict) -> bool:
    """Return True if the IAPP tracker has an entry for this law."""
    from src.core.iapp_alignment import get_iapp_entry_for_context
    try:
        entry = get_iapp_entry_for_context(ctx)
        return entry is not None and entry.has_data
    except Exception:
        return False


def _orrick_status_from_breakdown(breakdown: dict) -> str:
    """Derive orrick_status code from a stored confidence breakdown dict."""
    if breakdown.get("orrick_gated"):
        return "gated"
    if (breakdown.get("orrick_alignment") or 0.0) > 0.0:
        return "aligned"
    return "tracker_silent"


def _grounding_status_from_breakdown(breakdown: dict) -> str:
    """Derive combined grounding_status from a stored confidence breakdown dict."""
    if breakdown.get("orrick_gated"):
        return "tracker_silent"
    if (breakdown.get("orrick_alignment") or 0.0) > 0.0:
        return "orrick_grounded"
    return "tracker_silent"


# IAPP alignment status → 0.0–1.0 score for tracker_alignment_score dimension.
# "tracker_silent" and None are excluded (treated as no data, not penalized).
_IAPP_STATUS_TO_SCORE: dict[str, float] = {
    "aligned": 1.0,
    "scope_mismatch": 0.3,
}


def _recompute_confidence_with_cv(
    db,
    extraction,
    record,
    ctx: dict,
    cv_score: float,
) -> bool:
    """Recompute an extraction's confidence with a cross-validation score.

    Phase 2b: cross-validation runs post-extraction, so the original
    confidence was computed without the cross_validation component (its
    0.25 weight was redistributed). This re-runs compute_confidence with
    the now-available accuracy score and writes the updated
    confidence_score / confidence_tier back to the extraction.

    Returns True if the tier changed (for logging/metrics).
    """
    ext_type_val = (
        extraction.extraction_type.value
        if hasattr(extraction.extraction_type, "value")
        else str(extraction.extraction_type)
    )
    schema_class = EXTRACTION_TYPE_SCHEMAS.get(ext_type_val)
    if schema_class is None:
        return False

    payload = extraction.payload or {}
    evidence = extraction.evidence_spans or []

    meta = extraction.metadata_ or {}
    breakdown = meta.get("confidence_breakdown", {})
    parse_quality = breakdown.get("source_quality")

    orrick_sim = validate_extraction_against_orrick(payload, ctx)

    # Phase 4b: look up IAPP alignment for this specific extraction's actor.
    iapp_alignment_score: float | None = None
    try:
        from src.core.iapp_alignment import get_iapp_entry_for_context
        iapp_entry = get_iapp_entry_for_context(ctx)
        if iapp_entry is not None and iapp_entry.has_data:
            from src.core.iapp_alignment import check_iapp_alignment
            status = check_iapp_alignment(payload.get("subject_normalized"), iapp_entry)
            iapp_alignment_score = _IAPP_STATUS_TO_SCORE.get(status)
    except Exception:
        pass  # IAPP lookup failure must not block confidence recompute

    new_conf = compute_confidence(
        schema_valid=True,
        evidence_spans=evidence,
        extraction_payload=payload,
        schema_class=schema_class,
        parse_quality_score=parse_quality,
        orrick_similarity=orrick_sim,
        cross_validation_score=cv_score,
        passage_text=record.text_content,
        iapp_has_data=_iapp_has_data_for_ctx(ctx),
        iapp_alignment_score=iapp_alignment_score,
    )

    old_tier = (
        extraction.confidence_tier.value
        if hasattr(extraction.confidence_tier, "value")
        else str(extraction.confidence_tier)
    )
    tier_changed = old_tier != new_conf.tier

    extraction.confidence_score = new_conf.total_score
    extraction.confidence_tier = ConfidenceTier(new_conf.tier)

    updated_meta = dict(meta)
    updated_meta["confidence_breakdown"] = {
        "schema_validity": new_conf.schema_validity,
        "evidence_grounding": new_conf.evidence_grounding,
        "completeness": new_conf.completeness,
        "source_quality": new_conf.source_quality,
        "orrick_alignment": new_conf.orrick_alignment,
        "cross_validation": new_conf.cross_validation,
        "orrick_gated": new_conf.orrick_gated,
        "recomputed_with_cross_validation": True,
        "source_grounding_score": new_conf.source_grounding_score,
        "tracker_alignment_score": new_conf.tracker_alignment_score,
        "schema_completeness_score": new_conf.schema_completeness_score,
    }
    extraction.metadata_ = updated_meta

    return tier_changed


def _run_iapp_alignment_for_dv(
    db,
    dv_id: int,
    verification_run_id: int,
    bill_ctx: dict,
) -> None:
    """Phase 4b: run IAPP alignment for all extractions in a document version."""
    from src.core.iapp_alignment import (
        IAPPEntry,
        check_iapp_alignment,
        get_iapp_entry_for_context,
    )

    dv = db.get(DocumentVersion, dv_id)
    if not dv:
        return
    df = dv.family
    s = df.source if df else None
    ctx_for_iapp = {
        "jurisdiction": s.jurisdiction_code if s else None,
        "jurisdiction_name": s.jurisdiction_name if s else None,
        "short_cite": df.short_cite if df else None,
        "bill_id": (df.metadata_ or {}).get("bill_id") if df else None,
    }
    iapp_entry: IAPPEntry | None = get_iapp_entry_for_context(ctx_for_iapp)
    iapp_present = iapp_entry is not None and iapp_entry.has_data

    if not iapp_present:
        return

    extractions = db.scalars(
        select(Extraction)
        .where(
            Extraction.source_record_id.in_(
                select(NormalizedSourceRecord.id).where(
                    NormalizedSourceRecord.document_version_id == dv_id
                )
            )
        )
    ).all()

    for extraction in extractions:
        payload = extraction.payload or {}
        subject_normalized = payload.get("subject_normalized")

        iapp_status = check_iapp_alignment(subject_normalized, iapp_entry)

        evs = db.scalars(
            select(ExtractionVerificationStatus)
            .where(ExtractionVerificationStatus.extraction_id == extraction.id)
            .where(ExtractionVerificationStatus.verification_run_id == verification_run_id)
        ).first()

        breakdown = (extraction.metadata_ or {}).get("confidence_breakdown", {})

        if evs is None:
            evs = ExtractionVerificationStatus(
                extraction_id=extraction.id,
                verification_run_id=verification_run_id,
                document_version_id=dv_id,
                orrick_score=breakdown.get("orrick_alignment"),
                orrick_gated=bool(breakdown.get("orrick_gated", False)),
                orrick_status=_orrick_status_from_breakdown(breakdown),
            )
            db.add(evs)

        evs.iapp_status = iapp_status

        orrick_grounded = (breakdown.get("orrick_alignment") or 0.0) > 0.0
        if orrick_grounded:
            evs.grounding_status = "orrick_grounded"
        elif iapp_status == "aligned":
            evs.grounding_status = "iapp_grounded"
        elif iapp_status == "scope_mismatch":
            evs.grounding_status = "iapp_scope_mismatch"
        else:
            evs.grounding_status = "tracker_silent"


def run_verification_pass(
    db,
    document_version_id: int | None = None,
    skip_cross_validation: bool = False,
    skip_gap_detection: bool = False,
    skip_citation_verification: bool = False,
    on_progress=None,
) -> list[VerificationResult]:
    """Run post-extraction verification agents on completed extractions.

    Three verification layers:
      1. Cross-Validation: Second LLM reviews each extraction against source passage.
      2. Gap Detection: Identifies obligations the primary extraction missed.
      3. Citation Verification: Validates section_reference and cross_reference fields.

    Args:
        db: SQLAlchemy session
        document_version_id: Run on a single document (None = all with extractions)
        skip_cross_validation: Skip the cross-validation layer
        skip_gap_detection: Skip the gap detection layer
        skip_citation_verification: Skip citation verification
        on_progress: Optional callback for status messages

    Returns:
        List of VerificationResult, one per document version.
    """
    from sqlalchemy import distinct

    from src.agents.citation_verifier import verify_citations
    from src.agents.cross_validation import run_cross_validation
    from src.agents.gap_detector import run_gap_detection
    from src.core.bill_context import get_or_build_bill_context
    from src.ingestion.extractor import _build_context, is_cancelled

    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    dv_query = select(DocumentVersion.id)
    if document_version_id:
        dv_query = dv_query.where(DocumentVersion.id == document_version_id)
    else:
        dv_query = dv_query.where(
            DocumentVersion.id.in_(
                select(distinct(NormalizedSourceRecord.document_version_id))
                .where(
                    NormalizedSourceRecord.id.in_(
                        select(distinct(Extraction.source_record_id))
                    )
                )
            )
        )

    dv_ids = db.scalars(dv_query).all()
    _log(f"Running verification on {len(dv_ids)} document version(s)...")

    results: list[VerificationResult] = []

    for dv_id in dv_ids:
        if is_cancelled():
            _log("Verification cancelled by user.")
            break

        dv = db.get(DocumentVersion, dv_id)
        if not dv:
            continue

        label = "unknown"
        if dv.family:
            jur = dv.family.source.jurisdiction_code if dv.family.source else "??"
            label = f"{jur} - {dv.family.short_cite or dv.family.canonical_title}"

        _log(f"\n[{label}] Starting verification pass...")

        vrs = VerificationRunSummary(document_version_id=dv_id)
        db.add(vrs)
        db.flush()

        total_input_tokens = 0
        total_output_tokens = 0

        records = db.scalars(
            select(NormalizedSourceRecord)
            .where(NormalizedSourceRecord.document_version_id == dv_id)
            .where(
                NormalizedSourceRecord.id.in_(
                    select(SectionTriageResult.source_record_id)
                    .where(SectionTriageResult.decision.in_([
                        TriageDecision.relevant,
                        TriageDecision.uncertain,
                    ]))
                )
            )
            .order_by(NormalizedSourceRecord.ordinal)
        ).all()

        all_records = db.scalars(
            select(NormalizedSourceRecord)
            .where(NormalizedSourceRecord.document_version_id == dv_id)
            .order_by(NormalizedSourceRecord.ordinal)
        ).all()

        bill_ctx = get_or_build_bill_context(db, dv_id, records=all_records)

        # --- Layer 1: Cross-Validation ---
        cv_passages = 0
        cv_valid = 0
        cv_flagged = 0
        cv_accuracy_sum = 0.0
        cv_tier_changes = 0
        cv_failed = 0
        cv_issues: list[dict[str, Any]] = []

        if not skip_cross_validation:
            _log("  [1/3] Cross-validation...")
            for record in records:
                if len(record.text_content) < MIN_PASSAGE_LENGTH:
                    continue

                extractions = db.scalars(
                    select(Extraction).where(
                        Extraction.source_record_id == record.id
                    )
                ).all()

                if not extractions:
                    continue

                ext_payloads = [e.payload for e in extractions]
                ext_ids = [e.id for e in extractions]
                ctx = _build_context(db, record, bill_context=bill_ctx)

                cv_result = run_cross_validation(
                    passage_text=record.text_content,
                    extractions=ext_payloads,
                    passage_record_id=record.id,
                    extraction_ids=ext_ids,
                    context=ctx,
                )

                total_input_tokens += cv_result.input_tokens
                total_output_tokens += cv_result.output_tokens

                if cv_result.status == "failed":
                    cv_failed += 1
                    continue
                if cv_result.status != "completed":
                    continue

                cv_passages += 1
                cv_valid += cv_result.extractions_valid
                cv_flagged += cv_result.extractions_flagged
                cv_accuracy_sum += cv_result.avg_accuracy_score

                for r in cv_result.results:
                    is_valid = r.get("is_valid", True)

                    if not is_valid:
                        cv_issues.append({
                            "record_id": record.id,
                            "section_path": record.section_path,
                            **r,
                        })

                    ext_id = r.get("extraction_id")
                    if not ext_id:
                        continue
                    extraction = db.get(Extraction, ext_id)
                    if not extraction:
                        continue

                    accuracy_score = float(r.get("accuracy_score", 1.0))

                    conf_before = extraction.confidence_score
                    tier_before = (
                        extraction.confidence_tier.value
                        if hasattr(extraction.confidence_tier, "value")
                        else str(extraction.confidence_tier or "")
                    )

                    meta = dict(extraction.metadata_ or {})
                    meta["cross_validation"] = {
                        "is_valid": is_valid,
                        "accuracy_score": accuracy_score,
                        "issues": r.get("issues", []),
                    }
                    extraction.metadata_ = meta

                    tier_changed = False
                    try:
                        tier_changed = _recompute_confidence_with_cv(
                            db, extraction, record, ctx, accuracy_score,
                        )
                        if tier_changed:
                            cv_tier_changes += 1
                    except Exception as _e:
                        logger.warning(
                            "cross_validation_confidence_recompute_failed",
                            extraction_id=ext_id,
                            error=str(_e),
                        )

                    breakdown = (extraction.metadata_ or {}).get(
                        "confidence_breakdown", {}
                    )
                    tier_after = (
                        extraction.confidence_tier.value
                        if hasattr(extraction.confidence_tier, "value")
                        else str(extraction.confidence_tier or "")
                    )
                    evs = ExtractionVerificationStatus(
                        extraction_id=ext_id,
                        verification_run_id=vrs.id,
                        document_version_id=dv_id,
                        cv_score=accuracy_score,
                        cv_is_valid=is_valid,
                        cv_flagged=not is_valid,
                        confidence_before=conf_before,
                        confidence_after=extraction.confidence_score,
                        tier_before=tier_before[:1] if tier_before else None,
                        tier_after=tier_after[:1] if tier_after else None,
                        tier_changed=tier_changed,
                        orrick_score=breakdown.get("orrick_alignment"),
                        orrick_gated=bool(breakdown.get("orrick_gated", False)),
                        orrick_status=_orrick_status_from_breakdown(breakdown),
                        grounding_status=_grounding_status_from_breakdown(breakdown),
                    )
                    db.add(evs)

            cv_avg = cv_accuracy_sum / cv_passages if cv_passages > 0 else 1.0
            fail_note = f", {cv_failed} FAILED" if cv_failed else ""
            _log(
                f"    {cv_passages} passages checked, {cv_valid} valid, "
                f"{cv_flagged} flagged, avg accuracy: {cv_avg:.3f}, "
                f"{cv_tier_changes} tier change(s) after recompute{fail_note}"
            )
        else:
            cv_avg = 1.0

        # --- Layer 2: Gap Detection ---
        gd_passages = 0
        gd_gaps = 0
        gd_high = 0
        gd_failed = 0
        gd_candidates: list[dict[str, Any]] = []

        if not skip_gap_detection:
            _log("  [2/3] Gap detection...")
            for record in records:
                if len(record.text_content) < MIN_PASSAGE_LENGTH:
                    continue

                extractions = db.scalars(
                    select(Extraction).where(
                        Extraction.source_record_id == record.id
                    )
                ).all()

                ext_payloads = [e.payload for e in extractions]
                ctx = _build_context(db, record, bill_context=bill_ctx)

                gd_result = run_gap_detection(
                    passage_text=record.text_content,
                    existing_extractions=ext_payloads,
                    passage_record_id=record.id,
                    context=ctx,
                )

                total_input_tokens += gd_result.input_tokens
                total_output_tokens += gd_result.output_tokens

                if gd_result.status == "failed":
                    gd_failed += 1
                    continue

                gd_passages += 1
                gd_gaps += gd_result.gaps_found
                gd_high += gd_result.high_confidence_gaps

                for candidate in gd_result.candidates:
                    gd_candidates.append({
                        "record_id": record.id,
                        "section_path": record.section_path,
                        **candidate,
                    })

            gd_fail_note = f", {gd_failed} FAILED" if gd_failed else ""
            _log(
                f"    {gd_passages} passages checked, "
                f"{gd_gaps} gaps found ({gd_high} high confidence){gd_fail_note}"
            )

        # --- Layer 3: Citation Verification ---
        cit_checked = 0
        cit_verified = 0
        cit_unverified = 0
        cit_issues: list[dict[str, Any]] = []

        if not skip_citation_verification:
            _log("  [3/3] Citation verification...")
            cit_result = verify_citations(db, dv_id)
            cit_checked = cit_result.total_citations_checked
            cit_verified = cit_result.citations_verified
            cit_unverified = cit_result.citations_unverified
            cit_issues = [
                {
                    "extraction_id": issue.extraction_id,
                    "field_name": issue.field_name,
                    "cited_value": issue.cited_value,
                    "issue_type": issue.issue_type,
                    "closest_match": issue.closest_match,
                }
                for issue in cit_result.issues
            ]
            _log(
                f"    {cit_checked} citations checked, "
                f"{cit_verified} verified, {cit_unverified} unverified"
            )

        # Phase 4b: IAPP alignment pass
        _run_iapp_alignment_for_dv(db, dv_id, vrs.id, bill_ctx)

        # Phase 4a: finalize VerificationRunSummary
        vrs.cv_passages_checked = cv_passages
        vrs.cv_passages_failed = cv_failed
        vrs.cv_extractions_valid = cv_valid
        vrs.cv_extractions_flagged = cv_flagged
        vrs.cv_avg_accuracy = round(cv_avg, 4) if cv_passages > 0 else None
        vrs.gd_passages_checked = gd_passages
        vrs.gd_passages_failed = gd_failed
        vrs.gd_gaps_found = gd_gaps
        vrs.gd_high_confidence = gd_high
        vrs.gap_candidates = gd_candidates
        vrs.citations_checked = cit_checked
        vrs.citations_verified = cit_verified
        vrs.citations_unverified = cit_unverified
        vrs.citation_issues = cit_issues
        vrs.input_tokens = total_input_tokens
        vrs.output_tokens = total_output_tokens

        db.commit()

        results.append(VerificationResult(
            document_version_id=dv_id,
            document_label=label,
            cross_validation_passages=cv_passages,
            cross_validation_valid=cv_valid,
            cross_validation_flagged=cv_flagged,
            cross_validation_avg_accuracy=round(cv_avg, 4),
            cross_validation_issues=cv_issues,
            gap_detection_passages=gd_passages,
            gaps_found=gd_gaps,
            high_confidence_gaps=gd_high,
            gap_candidates=gd_candidates,
            citations_checked=cit_checked,
            citations_verified=cit_verified,
            citations_unverified=cit_unverified,
            citation_issues=cit_issues,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
        ))

        _log(
            f"  Verification complete for [{label}]: "
            f"{total_input_tokens + total_output_tokens:,} tokens used"
        )

    _log(f"\nVerification pass complete: {len(results)} documents processed")
    return results
