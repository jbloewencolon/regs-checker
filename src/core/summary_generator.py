"""Abstraction Presentation Layer — generates human-readable summaries from verified extractions.

This module is the FINAL step in the pipeline, running AFTER extraction and review.
It takes the verified, structured JSON payload (booleans, integers, verbatim quotes)
and generates a plain-English summary strictly for UI consumption.

CRITICAL DESIGN PRINCIPLE:
  The extraction pipeline extracts DETERMINISTIC DATA (booleans, integers, quotes).
  This module generates LOSSY SUMMARIES for human readers.
  The summary is NEVER used as input to downstream systems — only the raw payload is.
  If the summary and the raw payload disagree, the raw payload is authoritative.

Two generation strategies:
  1. TEMPLATE: For simple/structured types (thresholds, enforcement, compliance flags),
     a deterministic string template produces the summary. No LLM needed.
  2. LLM: For complex narrative types (obligations, rights, ambiguity, preemption),
     the verified JSON is passed to a local LLM with a strict instruction to
     summarize the PAYLOAD (not the source text) in 1-3 sentences.

Usage:
    from src.core.summary_generator import generate_summary, generate_summaries_batch
    summary = generate_summary(extraction_type, payload, jurisdiction)
    # Returns: "Colorado requires AI developers to conduct annual bias audits
    #           (SB 24-205 § 6-1-1703). Enforced by AG with up to $20,000
    #           penalty per violation. 60-day cure period."
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()

# SFH-1i (audit B8): count summary-generation failures so a reviewer staring
# at a "(summary generation failed)" placeholder is a known, run-level
# quantity rather than a mystery. Presentation-only by design — the counter
# never affects extraction data. Surfaced via get_and_reset_generation_failures().
_generation_failures = 0


def get_and_reset_generation_failures() -> int:
    """Return the count of summary-generation failures and reset it."""
    global _generation_failures
    count = _generation_failures
    _generation_failures = 0
    return count


# ---------------------------------------------------------------------------
# Template-based summaries (deterministic, no LLM)
# ---------------------------------------------------------------------------


def _summarize_obligation(p: dict, jur: str | None) -> str:
    """Template summary for obligation extractions."""
    subject = p.get("subject_normalized") or p.get("subject") or "Entity"
    modality = p.get("modality", "must")
    action = p.get("action", "")
    condition = p.get("condition")
    jur_label = f" ({jur})" if jur else ""

    parts = [f"{subject.title()} {modality} {action}"]

    if condition:
        parts[0] += f", {condition}"

    # Timeline
    timeline = p.get("timeline")
    if isinstance(timeline, dict):
        if timeline.get("effective_date"):
            parts.append(f"Effective: {timeline['effective_date']}.")
        if timeline.get("compliance_deadline"):
            parts.append(f"Compliance deadline: {timeline['compliance_deadline']}.")
        if timeline.get("phase_in_period"):
            parts.append(f"Phase-in: {timeline['phase_in_period']}.")

    # Enforcement
    enforcement = p.get("enforcement")
    if isinstance(enforcement, dict):
        enf_parts = []
        if enforcement.get("enforcing_body"):
            enf_parts.append(f"Enforced by {enforcement['enforcing_body']}")
        if enforcement.get("max_civil_penalty_usd"):
            enf_parts.append(f"up to ${enforcement['max_civil_penalty_usd']:,} penalty")
        if enforcement.get("private_right_of_action"):
            enf_parts.append("private right of action")
        if enforcement.get("cure_period_days"):
            enf_parts.append(f"{enforcement['cure_period_days']}-day cure period")
        if enf_parts:
            parts.append(". ".join(enf_parts) + ".")

    section = p.get("section_reference")
    if section:
        parts.append(f"[§ {section}]{jur_label}")

    return " ".join(parts).strip()


def _summarize_threshold(p: dict, jur: str | None) -> str:
    """Template summary for threshold/exception extractions."""
    parts = []

    tval = p.get("threshold_value", "")
    tunit = p.get("threshold_unit", "")
    condition = p.get("threshold_condition", "")

    if tval and tunit:
        parts.append(f"Threshold: {tval} {tunit}.")
    elif tval:
        parts.append(f"Threshold: {tval}.")
    elif condition:
        parts.append(f"Applies when: {condition[:150]}.")

    # Compute thresholds
    if p.get("compute_flops"):
        parts.append(f"Compute threshold: {p['compute_flops']:.0e} FLOPS.")
    if p.get("compute_description"):
        parts.append(p["compute_description"])

    # Sectors
    sectors = p.get("sector_applicability")
    if sectors and isinstance(sectors, list):
        parts.append(f"Sectors: {', '.join(sectors)}.")

    applies = p.get("applies_to_obligation")
    if applies:
        parts.append(f"Modifies: {applies[:100]}.")

    # Exceptions
    exceptions = p.get("exceptions")
    if isinstance(exceptions, list) and exceptions:
        exc_strs = []
        for exc in exceptions[:3]:
            if isinstance(exc, dict):
                exc_strs.append(exc.get("description", "")[:80])
            elif isinstance(exc, str):
                exc_strs.append(exc[:80])
        if exc_strs:
            parts.append(f"Exceptions: {'; '.join(exc_strs)}.")

    return " ".join(parts).strip() or "Threshold extraction (no summary details)."


def _summarize_definition(p: dict, jur: str | None) -> str:
    """Template summary for definition/actor/framework extractions."""
    term = p.get("term", "Unknown term")
    defn = p.get("definition_text", "")
    scope = p.get("scope")

    # Truncate definition to 200 chars for summary
    if len(defn) > 200:
        defn = defn[:197] + "..."

    parts = [f'"{term}" means {defn}']
    if scope:
        parts.append(f"Scope: {scope}.")

    actors = p.get("actors")
    if isinstance(actors, list) and actors:
        actor_names = []
        for a in actors[:3]:
            if isinstance(a, dict):
                actor_names.append(a.get("actor_name", ""))
            elif isinstance(a, str):
                actor_names.append(a)
        if actor_names:
            parts.append(f"Actors: {', '.join(actor_names)}.")

    frefs = p.get("framework_refs")
    if isinstance(frefs, list) and frefs:
        fref_names = []
        for f in frefs[:2]:
            if isinstance(f, dict):
                fref_names.append(f.get("framework_name", ""))
        if fref_names:
            parts.append(f"References: {', '.join(fref_names)}.")

    return " ".join(parts).strip()


def _summarize_ambiguity(p: dict, jur: str | None) -> str:
    """Template summary for ambiguity extractions."""
    atype = (p.get("ambiguity_type") or "").replace("_", " ")
    severity = p.get("severity", "unknown")
    text = p.get("ambiguous_text", "")[:120]
    notes = p.get("interpretation_notes")

    parts = [f"{severity.upper()} {atype}: \"{text}\""]
    if notes:
        parts.append(notes[:150])
    suggestion = p.get("suggested_clarification")
    if suggestion:
        parts.append(f"Suggested fix: {suggestion[:150]}")

    return " ".join(parts).strip()


def _summarize_rights(p: dict, jur: str | None) -> str:
    """Template summary for rights_protection extractions."""
    holder = p.get("right_holder_normalized") or p.get("right_holder", "Individual")
    rtype = (p.get("right_type") or "").replace("_", " ")
    desc = p.get("right_description", "")[:200]
    bearer = p.get("duty_bearer")
    trigger = p.get("trigger_condition")

    parts = [f"{holder.title()} right to {rtype}: {desc}"]
    if bearer:
        parts.append(f"Duty bearer: {bearer}.")
    if trigger:
        parts.append(f"Triggered when: {trigger[:100]}.")

    remedies = p.get("remedies")
    if isinstance(remedies, list) and remedies:
        for r in remedies[:2]:
            if isinstance(r, dict):
                rtype_r = r.get("remedy_type", "")
                rdesc = r.get("description", "")[:80]
                parts.append(f"Remedy ({rtype_r}): {rdesc}.")

    return " ".join(parts).strip()


def _summarize_compliance(p: dict, jur: str | None) -> str:
    """Template summary for compliance_mechanism extractions."""
    mtype = (p.get("mechanism_type") or "").replace("_", " ").title()
    party = p.get("responsible_party_normalized") or p.get("responsible_party", "Entity")
    desc = p.get("description", "")[:150]

    parts = [f"{mtype}: {party} must {desc}"]

    if p.get("reporting_frequency"):
        parts.append(f"Frequency: {p['reporting_frequency']}.")
    if p.get("reporting_recipient"):
        parts.append(f"Reports to: {p['reporting_recipient']}.")
    if p.get("is_bias_testing"):
        parts.append("Includes bias testing.")
    if p.get("is_red_teaming"):
        parts.append("Includes red teaming.")
    if p.get("is_third_party_audit"):
        parts.append("Requires third-party audit.")
    if p.get("assessment_frequency_months"):
        parts.append(f"Assessment every {p['assessment_frequency_months']} months.")
    if p.get("incident_reporting_hours"):
        parts.append(f"Incidents reported within {p['incident_reporting_hours']} hours.")

    nist = p.get("nist_measure_refs")
    if nist and isinstance(nist, list):
        parts.append(f"NIST: {', '.join(nist[:3])}.")

    return " ".join(parts).strip()


def _summarize_preemption(p: dict, jur: str | None) -> str:
    """Template summary for preemption_signal extractions."""
    ctype = (p.get("conflict_type") or "other").replace("_", " ").title()
    severity = p.get("severity", "unknown").upper()
    desc = p.get("description", "")[:200]
    auth = p.get("related_authority")
    lang = p.get("preemption_language")

    parts = [f"[{severity}] {ctype}: {desc}"]
    if auth:
        parts.append(f"Authority: {auth}.")
    if lang:
        parts.append(f'Preemption language: "{lang[:120]}".')

    return " ".join(parts).strip()


def _summarize_enforcement(p: dict, jur: str | None) -> str:
    """Template summary for standalone enforcement extractions."""
    parts = []
    if isinstance(p.get("enforcement"), dict):
        enf = p["enforcement"]
        if enf.get("enforcing_body"):
            parts.append(f"Enforced by {enf['enforcing_body']}.")
        if enf.get("max_civil_penalty_usd"):
            parts.append(f"Max penalty: ${enf['max_civil_penalty_usd']:,}.")
        if enf.get("cure_period_days"):
            parts.append(f"Cure period: {enf['cure_period_days']} days.")
        if enf.get("private_right_of_action"):
            parts.append("Private right of action available.")
    elif p.get("enforcing_body"):
        parts.append(f"Enforced by {p['enforcing_body']}.")

    return " ".join(parts).strip() or "Enforcement mechanism (see payload for details)."


# ---------------------------------------------------------------------------
# Generator registry
# ---------------------------------------------------------------------------

_TEMPLATE_GENERATORS: dict[str, callable] = {
    "obligation": _summarize_obligation,
    "definition": _summarize_definition,
    "actor_mapping": _summarize_definition,
    "framework_ref": _summarize_definition,
    "threshold": _summarize_threshold,
    "exception": _summarize_threshold,
    "ambiguity": _summarize_ambiguity,
    "rights_protection": _summarize_rights,
    "compliance_mechanism": _summarize_compliance,
    "preemption_signal": _summarize_preemption,
    "enforcement": _summarize_enforcement,
    "timeline": _summarize_obligation,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_summary(
    extraction_type: str,
    payload: dict[str, Any],
    jurisdiction: str | None = None,
) -> str:
    """Generate a plain-English summary from a verified extraction payload.

    This is a LOSSY presentation-layer transformation. The raw payload
    remains the authoritative data source. Summaries are for human
    readers only.

    Args:
        extraction_type: The extraction type (obligation, threshold, etc.)
        payload: The verified extraction payload dict.
        jurisdiction: Optional jurisdiction code for context.

    Returns:
        A 1-3 sentence plain-English summary.
    """
    # Strip internal metadata fields
    clean_payload = {
        k: v for k, v in payload.items()
        if not k.startswith("_")
    }

    generator = _TEMPLATE_GENERATORS.get(extraction_type)
    if generator:
        try:
            return generator(clean_payload, jurisdiction)
        except Exception as e:
            global _generation_failures
            _generation_failures += 1
            logger.warning(
                "summary_generation_failed",
                extraction_type=extraction_type,
                error=str(e),
            )
            return f"{extraction_type.replace('_', ' ').title()} extraction (summary generation failed)."

    return f"{extraction_type.replace('_', ' ').title()} extraction (see payload for details)."


def generate_summaries_batch(
    db,
    limit: int | None = None,
    overwrite: bool = False,
) -> dict[str, int]:
    """Generate summaries for all extractions that don't have one yet.

    Reads extractions from the database, generates summaries, and stores
    them in the extraction's metadata_['plain_summary'] field.

    Args:
        db: SQLAlchemy session.
        limit: Max extractions to process (None = all).
        overwrite: If True, regenerate summaries even if one exists.

    Returns:
        Summary dict with counts.
    """
    from sqlalchemy import select

    from src.db.models import (
        DocumentFamily,
        DocumentVersion,
        Extraction,
        NormalizedSourceRecord,
        Source,
    )

    query = (
        select(Extraction)
        .join(NormalizedSourceRecord, Extraction.source_record_id == NormalizedSourceRecord.id)
        .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
        .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
        .join(Source, DocumentFamily.source_id == Source.id)
    )

    if not overwrite:
        # Only process extractions without a summary.
        # JSONB path returns SQL NULL when key is missing, so use
        # has_key negation to catch both missing keys and null values.
        query = query.where(
            ~Extraction.metadata_.has_key("plain_summary")
        )

    if limit:
        query = query.limit(limit)

    extractions = db.scalars(query).all()

    generated = 0
    failed = 0

    for ext in extractions:
        try:
            # Get jurisdiction for context
            nsr = ext.source_record
            dv = nsr.document_version if nsr else None
            df = dv.family if dv else None
            src = df.source if df else None
            jur = src.jurisdiction_code if src else None

            ext_type = ext.extraction_type.value if hasattr(ext.extraction_type, "value") else str(ext.extraction_type)
            summary = generate_summary(ext_type, ext.payload or {}, jur)

            # Store in metadata
            meta = dict(ext.metadata_) if ext.metadata_ else {}
            meta["plain_summary"] = summary
            ext.metadata_ = meta
            generated += 1

        except Exception as e:
            logger.warning(
                "batch_summary_failed",
                extraction_id=ext.id,
                error=str(e),
            )
            failed += 1

    if generated > 0:
        db.commit()

    logger.info(
        "batch_summaries_complete",
        generated=generated,
        failed=failed,
        total=len(extractions),
    )

    return {
        "total": len(extractions),
        "generated": generated,
        "failed": failed,
    }
