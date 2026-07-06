"""Payload format adapter — normalizes extraction payloads to Policy Navigator schema.

The Regs Checker extraction pipeline produces payloads with a richer structure
(e.g., nested TimelineInfo, EnforcementInfo, ActorMapping objects). The Policy
Navigator product expects a flatter schema per extraction_type as defined in
the Sync Team Onboarding Guide Section 5.

This module converts between the two formats, ensuring all expected keys are
present (even if null) so the Policy Navigator front-end doesn't render empty
UI panels.

Mapping Summary:
  - obligation: Flatten timeline/enforcement nested objects into top-level keys
  - threshold: Rename threshold_exception fields, flatten exceptions list
  - definition: Rename definition_actor fields to match expected schema
  - ambiguity: Nearly 1:1, just ensure all keys present

PNE-1a (2026-07-06): the adapters are whitelists, and they were silently
stripping fields the pipeline already extracts. Obligation now passes through
object / safe_harbor / consent_requirements / interpretation_risks /
preemption_signals and ships the structured timeline dict as
``timeline_structured`` (with date_parse_status) alongside the flattened
string; rights_protection passes through interpretation_risks. When adding a
field to an extraction schema, add it to the adapter too or it never syncs.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.core.pn_crosswalk import (
    derive_actor_role,
    derive_obligation_type,
    derive_trigger,
)

logger = structlog.get_logger()

# PNE-2c: timeline sub-fields that carry an actual date, in the order PN's
# deadline model expects them. Mapped to PN deadline_type values.
_TIMELINE_DEADLINE_TYPES: list[tuple[str, str]] = [
    ("effective_date", "effective"),
    ("compliance_deadline", "compliance"),
    ("sunset_date", "sunset"),
]


def _derive_deadlines(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    """PNE-2c (PN Ask 3a): structured deadlines[] from a TimelineInfo dict.

    Emits one entry per date field that actually parsed to ISO-8601. The
    authority for "parsed" is ``date_parse_status`` (set by TimelineInfo's
    validator: 'parsed' = normalize_date produced YYYY-MM-DD, 'unparsed' =
    raw model prose passed through). Unparsed fields are skipped entirely —
    never emitted as a deadline_date — so PN never does date arithmetic on
    free text. Per-cohort phasing (min_employees etc.) is deferred to the
    EA1-gated extraction change (PNE-4a); today every entry is whole-law.
    """
    status = timeline.get("date_parse_status")
    status = status if isinstance(status, dict) else {}
    deadlines: list[dict[str, Any]] = []
    for field_name, deadline_type in _TIMELINE_DEADLINE_TYPES:
        value = timeline.get(field_name)
        if not value or not isinstance(value, str):
            continue
        # Only emit fields the validator confirmed as real ISO dates. If the
        # status dict is absent (legacy row), fall back to trusting the value
        # as-is only when it already looks like a bare ISO date is unsafe —
        # so require an explicit 'parsed' marker.
        if status.get(field_name) != "parsed":
            continue
        deadlines.append({"deadline_type": deadline_type, "deadline_date": value})
    return deadlines


def adapt_payload_for_sync(
    extraction_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Convert a Regs Checker extraction payload to Policy Navigator format.

    Args:
        extraction_type: One of obligation, threshold, definition, ambiguity.
        payload: The raw extraction payload from Regs Checker.

    Returns:
        Payload dict matching Policy Navigator's expected schema.
    """
    adapter = _ADAPTERS.get(extraction_type)
    if adapter:
        return adapter(payload)

    # For types without a specific adapter, pass through as-is
    return payload


def _adapt_obligation(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt obligation payload: flatten nested timeline and enforcement objects.

    Regs Checker format:
        {subject, modality, action, timeline: {effective_date, ...}, enforcement: {...},
         obligation_family: <code>}

    Policy Navigator expected format:
        {subject, subject_normalized, modality, action, condition, jurisdiction,
         timeline, enforcement, obligation_family}
    where timeline and enforcement are string summaries, and obligation_family is
    the canonical code for the obligation type (documentation, registration, etc.).
    """
    result: dict[str, Any] = {
        "subject": payload.get("subject"),
        "subject_normalized": payload.get("subject_normalized"),
        "modality": payload.get("modality"),
        "action": payload.get("action"),
        # PNE-1a: the fields below were extracted and stored all along but
        # stripped here at sync time. "object" is the model_dump(by_alias=True)
        # key for ObligationPayload.object_.
        "object": payload.get("object"),
        "condition": payload.get("condition"),
        "jurisdiction": payload.get("jurisdiction"),
        "safe_harbor": payload.get("safe_harbor"),
        "consent_requirements": payload.get("consent_requirements"),
        "interpretation_risks": payload.get("interpretation_risks") or [],
        "preemption_signals": payload.get("preemption_signals") or [],
        "timeline": None,
        # PNE-1a (PN Ask 3, deterministic half): the structured TimelineInfo
        # dict — including date_parse_status, which marks each date field
        # "parsed" (real ISO-8601) or "unparsed" (free text passed through).
        # Consumers must skip "unparsed" fields for date arithmetic. The
        # flattened "timeline" string below is kept for backward compat.
        "timeline_structured": None,
        # PNE-2c (PN Ask 3a): structured deadlines derived from parsed dates.
        "deadlines": [],
        "enforcement": None,
        "obligation_family": payload.get("obligation_family"),
    }

    # PNE-2a: derive actor role (RC code + alias-aware PN value).
    result["actor_role_rc"], result["actor_role"] = derive_actor_role(
        payload.get("subject"), payload.get("subject_normalized")
    )

    # PNE-2b: derive obligation family (RC) + type (PN) from the action text.
    result["obligation_family"], result["obligation_type"] = derive_obligation_type(
        payload.get("action")
    )

    # Flatten timeline object into a string summary
    timeline = payload.get("timeline")
    if isinstance(timeline, dict):
        result["timeline_structured"] = timeline
        result["deadlines"] = _derive_deadlines(timeline)
        parts = []
        if timeline.get("effective_date"):
            parts.append(f"Effective: {timeline['effective_date']}")
        if timeline.get("compliance_deadline"):
            parts.append(f"Deadline: {timeline['compliance_deadline']}")
        if timeline.get("sunset_date"):
            parts.append(f"Sunset: {timeline['sunset_date']}")
        if timeline.get("phase_in_period"):
            parts.append(f"Phase-in: {timeline['phase_in_period']}")
        if timeline.get("timeline_text"):
            parts.append(timeline["timeline_text"])
        result["timeline"] = "; ".join(parts) if parts else None
    elif isinstance(timeline, str):
        result["timeline"] = timeline

    # Flatten enforcement object into a string summary, preserving structured fields
    enforcement = payload.get("enforcement")
    if isinstance(enforcement, dict):
        parts = []
        if enforcement.get("enforcing_body"):
            parts.append(f"Enforced by: {enforcement['enforcing_body']}")
        if enforcement.get("penalty_type"):
            parts.append(f"Penalty: {enforcement['penalty_type']}")
        if enforcement.get("penalty_description"):
            parts.append(enforcement["penalty_description"])
        if enforcement.get("private_right_of_action") is True:
            parts.append("Private right of action: Yes")
        if enforcement.get("enforcement_text"):
            parts.append(enforcement["enforcement_text"])
        result["enforcement"] = "; ".join(parts) if parts else None
        # Preserve structured enforcement fields for matrix rollup
        result["private_right_of_action"] = enforcement.get("private_right_of_action")
        result["max_civil_penalty_usd"] = enforcement.get("max_civil_penalty_usd")
        result["cure_period_days"] = enforcement.get("cure_period_days")
        # PNE-2a: the enforcer, kept separate from actor_role (Ask 1's core point).
        result["enforcement_authority"] = enforcement.get("enforcing_body")
    elif isinstance(enforcement, str):
        result["enforcement"] = enforcement

    return result


def _adapt_threshold(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt threshold payload: flatten exceptions into a single field.

    Regs Checker format:
        {threshold_type, threshold_value, threshold_unit, threshold_condition,
         applies_to_obligation, exceptions: [{exception_type, description, ...}]}

    Policy Navigator expected format:
        {threshold_type, threshold_value, threshold_unit, threshold_condition,
         applies_to_obligation, exceptions}
    where exceptions is a string summary of carve-outs.
    """
    result: dict[str, Any] = {
        "threshold_type": payload.get("threshold_type"),
        "threshold_value": payload.get("threshold_value"),
        "threshold_unit": payload.get("threshold_unit"),
        "threshold_condition": payload.get("threshold_condition"),
        "applies_to_obligation": payload.get("applies_to_obligation"),
        "exceptions": None,
        # Matrix fields — preserved for rollup
        "compute_flops": payload.get("compute_flops"),
        "compute_description": payload.get("compute_description"),
        "sector_applicability": payload.get("sector_applicability"),
        # PNE-2d (PN Ask 4b): machine-comparable predicate derived from the
        # threshold fields — {trigger_type, trigger_operator, trigger_value,
        # trigger_condition_raw}. None when there's no threshold signal.
        "trigger": derive_trigger(payload),
    }

    exceptions = payload.get("exceptions")
    if isinstance(exceptions, list) and exceptions:
        summaries = []
        for exc in exceptions:
            if isinstance(exc, dict):
                desc = exc.get("description", "")
                etype = exc.get("exception_type", "")
                summaries.append(f"{etype}: {desc}" if etype else desc)
            elif isinstance(exc, str):
                summaries.append(exc)
        result["exceptions"] = "; ".join(summaries) if summaries else None
    elif isinstance(exceptions, str):
        result["exceptions"] = exceptions

    return result


def _adapt_definition(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt definition payload: flatten actors and framework_refs.

    Regs Checker format:
        {term, definition_text, scope, cross_references: [...],
         actors: [{actor_name, ...}], framework_refs: [{framework_name, ...}]}

    Policy Navigator expected format:
        {term, definition_text, scope, actors, cross_references, framework_refs}
    where actors/cross_references/framework_refs are string summaries.
    """
    result: dict[str, Any] = {
        "term": payload.get("term"),
        "definition_text": payload.get("definition_text"),
        "scope": payload.get("scope"),
        "actors": None,
        "cross_references": None,
        "framework_refs": None,
    }

    # Flatten actors list
    actors = payload.get("actors")
    if isinstance(actors, list) and actors:
        actor_strs = []
        for a in actors:
            if isinstance(a, dict):
                name = a.get("actor_name", "")
                atype = a.get("actor_type", "")
                actor_strs.append(f"{name} ({atype})" if atype else name)
            elif isinstance(a, str):
                actor_strs.append(a)
        result["actors"] = "; ".join(actor_strs) if actor_strs else None
    elif isinstance(actors, str):
        result["actors"] = actors

    # Flatten cross_references list
    xrefs = payload.get("cross_references")
    if isinstance(xrefs, list) and xrefs:
        result["cross_references"] = "; ".join(str(x) for x in xrefs)
    elif isinstance(xrefs, str):
        result["cross_references"] = xrefs

    # Flatten framework_refs list
    frefs = payload.get("framework_refs")
    if isinstance(frefs, list) and frefs:
        fref_strs = []
        for f in frefs:
            if isinstance(f, dict):
                name = f.get("framework_name", "")
                section = f.get("section_or_standard", "")
                fref_strs.append(f"{name} ({section})" if section else name)
            elif isinstance(f, str):
                fref_strs.append(f)
        result["framework_refs"] = "; ".join(fref_strs) if fref_strs else None
    elif isinstance(frefs, str):
        result["framework_refs"] = frefs

    return result


def _adapt_ambiguity(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt ambiguity payload: ensure all expected keys present.

    Nearly 1:1 mapping. Just ensure optional keys are present as null
    rather than missing, to prevent empty UI panels in Policy Navigator.
    """
    return {
        "ambiguous_text": payload.get("ambiguous_text"),
        "ambiguity_type": payload.get("ambiguity_type"),
        "severity": payload.get("severity"),
        "affected_obligations": payload.get("affected_obligations"),
        "interpretation_notes": payload.get("interpretation_notes"),
        "suggested_clarification": payload.get("suggested_clarification"),
    }


def _adapt_preemption_signal(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt preemption signal payload for Policy Navigator.

    Phase 2d: layer a typed ``legal_context_type`` (true_preemption,
    constitutional_limit, interstate_conflict, agency_jurisdiction,
    cross_law_reference, unclassified) on top of the raw ``conflict_type``,
    and carry a ``display`` flag so low-value rows can be hidden.
    """
    from src.core.legal_context import classify_legal_context

    ctx = classify_legal_context(payload)
    return {
        "conflict_type": payload.get("conflict_type"),
        "legal_context_type": ctx["legal_context_type"],
        "display": ctx["display"],
        "description": payload.get("description"),
        "related_authority": payload.get("related_authority"),
        "severity": payload.get("severity"),
        "preemption_language": payload.get("preemption_language"),
        "section_reference": payload.get("section_reference"),
        "jurisdiction": payload.get("jurisdiction"),
    }


def _adapt_rights_protection(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt rights_protection payload: flatten remedies list."""
    result: dict[str, Any] = {
        "right_holder": payload.get("right_holder"),
        "right_holder_normalized": payload.get("right_holder_normalized"),
        "right_type": payload.get("right_type"),
        "right_description": payload.get("right_description"),
        "trigger_condition": payload.get("trigger_condition"),
        "duty_bearer": payload.get("duty_bearer"),
        "section_reference": payload.get("section_reference"),
        "jurisdiction": payload.get("jurisdiction"),
        # PNE-1a: extracted and stored all along, previously stripped here.
        # Ambiguity findings live embedded on the rights row they affect
        # (the ambiguity agent is retired — see DI-4).
        "interpretation_risks": payload.get("interpretation_risks") or [],
        "remedies": None,
    }

    remedies = payload.get("remedies")
    if isinstance(remedies, list) and remedies:
        summaries = []
        for r in remedies:
            if isinstance(r, dict):
                rtype = r.get("remedy_type", "")
                desc = r.get("description", "")
                summaries.append(f"{rtype}: {desc}" if rtype else desc)
            elif isinstance(r, str):
                summaries.append(r)
        result["remedies"] = "; ".join(summaries) if summaries else None
    elif isinstance(remedies, str):
        result["remedies"] = remedies

    return result


def _adapt_compliance_mechanism(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt compliance_mechanism payload: flatten audits, preserve matrix flags."""
    result: dict[str, Any] = {
        "mechanism_type": payload.get("mechanism_type"),
        "description": payload.get("description"),
        "responsible_party": payload.get("responsible_party"),
        "responsible_party_normalized": payload.get("responsible_party_normalized"),
        "record_retention_period": payload.get("record_retention_period"),
        "reporting_frequency": payload.get("reporting_frequency"),
        "reporting_recipient": payload.get("reporting_recipient"),
        "section_reference": payload.get("section_reference"),
        "jurisdiction": payload.get("jurisdiction"),
        "audits": None,
        # Matrix flags — preserved for rollup
        "is_bias_testing": payload.get("is_bias_testing", False),
        "is_red_teaming": payload.get("is_red_teaming", False),
        "nist_measure_refs": payload.get("nist_measure_refs"),
        "assessment_frequency_months": payload.get("assessment_frequency_months"),
        "is_third_party_audit": payload.get("is_third_party_audit", False),
        "incident_reporting_hours": payload.get("incident_reporting_hours"),
    }

    audits = payload.get("audits")
    if isinstance(audits, list) and audits:
        summaries = []
        for a in audits:
            if isinstance(a, dict):
                atype = a.get("audit_type", "")
                freq = a.get("frequency", "")
                assessor = a.get("assessor", "")
                parts = [p for p in [atype, freq, assessor] if p]
                summaries.append(": ".join(parts))
            elif isinstance(a, str):
                summaries.append(a)
        result["audits"] = "; ".join(summaries) if summaries else None
    elif isinstance(audits, str):
        result["audits"] = audits

    return result


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

_ADAPTERS: dict[str, Any] = {
    "obligation": _adapt_obligation,
    "threshold": _adapt_threshold,
    "definition": _adapt_definition,
    "ambiguity": _adapt_ambiguity,
    "rights_protection": _adapt_rights_protection,
    "compliance_mechanism": _adapt_compliance_mechanism,
    "preemption_signal": _adapt_preemption_signal,
    # Sub-types map to their parent adapter
    "actor_mapping": _adapt_definition,
    "framework_ref": _adapt_definition,
    "exception": _adapt_threshold,
    "enforcement": _adapt_obligation,
    "timeline": _adapt_obligation,
}
