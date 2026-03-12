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
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


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
        {subject, modality, action, timeline: {effective_date, ...}, enforcement: {...}}

    Policy Navigator expected format:
        {subject, subject_normalized, modality, action, condition, jurisdiction,
         timeline, enforcement}
    where timeline and enforcement are string summaries.
    """
    result: dict[str, Any] = {
        "subject": payload.get("subject"),
        "subject_normalized": payload.get("subject_normalized"),
        "modality": payload.get("modality"),
        "action": payload.get("action"),
        "condition": payload.get("condition"),
        "jurisdiction": payload.get("jurisdiction"),
        "timeline": None,
        "enforcement": None,
    }

    # Flatten timeline object into a string summary
    timeline = payload.get("timeline")
    if isinstance(timeline, dict):
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

    # Flatten enforcement object into a string summary
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


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

_ADAPTERS: dict[str, Any] = {
    "obligation": _adapt_obligation,
    "threshold": _adapt_threshold,
    "definition": _adapt_definition,
    "ambiguity": _adapt_ambiguity,
    # Sub-types map to their parent adapter
    "actor_mapping": _adapt_definition,
    "framework_ref": _adapt_definition,
    "exception": _adapt_threshold,
    "enforcement": _adapt_obligation,
    "timeline": _adapt_obligation,
}
