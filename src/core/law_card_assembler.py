"""Law Card assembler — builds the read-model card JSON for one law (LC-1c).

Assembles a DocumentFamily's current DocumentVersion + its NormalizedSourceRecords'
Extractions + BillLevelExtractions into the card-JSON contract described in
docs/law_card_dashboard_plan.md §3.2. Every extraction field is annotated with
its src/core/field_catalog.py entry (label/help/widget/material) so the
template layer (LC-2) never has to know a raw schema key.

Design-rule compliance (docs/law_card_design_rules.md):
  - Rule 1 (honest-unknown): a null field's `value` is None; the template
    decides how to render that as a gap badge — this module never substitutes
    a default.
  - Rule 3 (verbatim honesty): each field's `evidence` list carries the
    verification tier straight from Extraction.evidence_spans; nothing here
    upgrades an unverified span to look verified.
  - Rule 7 (stub routing): `render_hint` is computed here, not left to the
    caller, so a caller can never accidentally request the "full" look for a
    law with zero extractions.

D-1 note: run retention isn't implemented yet (LC-4/EAR gated), so
`run_id=None` reads whatever Extraction rows currently exist for the
document version rather than filtering to a "serving run" — there is only
ever one live extraction set today. Passing an explicit `run_id` already
filters correctly and needs no changes when LC-4 lands multi-run retention.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.field_catalog import (
    CATALOG,
    LIST_NESTED,
    NESTED,
    READONLY,
    get_entry,
    nested_model_name,
)
from src.db.models import (
    BillLevelExtraction,
    DocumentFamily,
    DocumentVersion,
    Extraction,
    ExtractionRun,
    FieldEditStatus,
    LawCardState,
    NormalizedSourceRecord,
)


@dataclass
class AssemblerResult:
    """Card JSON plus a flag distinguishing "law not found" from "law found,
    nothing extracted yet" — callers (LC-1d's API) need to tell these apart
    to return 404 vs. a valid stub card."""

    found: bool
    card: dict[str, Any] | None = None
    errors: list[str] = dc_field(default_factory=list)


# ---------------------------------------------------------------------------
# Version resolution
# ---------------------------------------------------------------------------


def _resolve_current_version(db: Session, family_id: int) -> DocumentVersion | None:
    """Return the head of the family's version chain.

    Versions form a linked list via `predecessor_id` (each new ingestion
    points back at what it replaced). The current version is whichever one
    is NOT referenced as any other version's predecessor. Falls back to the
    highest-id version if the family somehow has no versions at all reaching
    that state (defensive; shouldn't happen for a family with any ingested
    content) or if the chain has forked (multiple heads) — highest id wins,
    matching "most recently created" as the tie-break.
    """
    predecessor_ids = select(DocumentVersion.predecessor_id).where(
        DocumentVersion.family_id == family_id,
        DocumentVersion.predecessor_id.isnot(None),
    )
    return db.scalars(
        select(DocumentVersion)
        .where(
            DocumentVersion.family_id == family_id,
            DocumentVersion.id.notin_(predecessor_ids),
        )
        .order_by(DocumentVersion.id.desc())
        .limit(1)
    ).first()


# ---------------------------------------------------------------------------
# Law object
# ---------------------------------------------------------------------------


def _build_law_section(family: DocumentFamily, version: DocumentVersion) -> dict[str, Any]:
    source = family.source
    meta = family.metadata_ or {}
    status_value = (
        version.temporal_status.value
        if hasattr(version.temporal_status, "value")
        else str(version.temporal_status)
    )
    return {
        "canonical_key": family.canonical_key,
        "title": family.canonical_title,
        "short_cite": family.short_cite,
        "jurisdiction": source.jurisdiction_code if source else None,
        "jurisdiction_name": source.jurisdiction_name if source else None,
        "status": status_value,
        "effective_date": version.effective_date.isoformat() if version.effective_date else None,
        "sunset_date": version.sunset_date.isoformat() if version.sunset_date else None,
        "source_urls": {
            "primary": family.primary_source_url,
            "orrick": family.orrick_reference_url,
            "iapp": family.iapp_reference_url,
        },
        # Tracker-status surface (LC-0d finding: these are their own fields,
        # not folded into a generic metadata blob).
        "tracker": {
            "orrick_key_requirements": meta.get("key_requirements") or meta.get("orrick_summary"),
            "orrick_enforcement_summary": meta.get("enforcement_penalties"),
            "orrick_source": meta.get("orrick_source"),
            "ai_scope": meta.get("ai_scope_summary"),
            "iapp_bill_number": meta.get("iapp_bill_number"),
            "iapp_status": meta.get("iapp_status"),
            "iapp_ai_topic": meta.get("iapp_ai_topic"),
        },
    }


# ---------------------------------------------------------------------------
# Extraction -> card entry
# ---------------------------------------------------------------------------


def _extraction_type_model_name(extraction_type: str) -> str:
    """Map an ExtractionType value to its field_catalog model name.

    Mirrors EXTRACTION_TYPE_SCHEMAS in src/schemas/extraction.py — the
    sub-types produced by consolidated agents (enforcement/timeline from
    obligation; actor_mapping/framework_ref from definition_actor;
    exception from threshold_exception) share their parent payload's schema.
    """
    mapping = {
        "obligation": "ObligationPayload",
        "enforcement": "ObligationPayload",
        "timeline": "ObligationPayload",
        "definition": "DefinitionActorPayload",
        "actor_mapping": "DefinitionActorPayload",
        "framework_ref": "DefinitionActorPayload",
        "threshold": "ThresholdExceptionPayload",
        "exception": "ThresholdExceptionPayload",
        "rights_protection": "RightsProtectionPayload",
        "compliance_mechanism": "ComplianceMechanismPayload",
        "preemption_signal": "PreemptionSignalPayload",
        "ambiguity": "AmbiguityPayload",
    }
    return mapping.get(extraction_type, extraction_type)


def _field_dict(
    *, path: str, label: str, value: Any, widget: str, material: bool,
    help_text: str | None, unit: str | None, choices: list[str] | None,
    glossary: str | None, evidence: list[dict], edit_id: int | None = None,
    catalog_gap: bool = False, group: str | None = None,
) -> dict[str, Any]:
    """One field's card-JSON entry. `editable` mirrors edit_service's own
    scope (src/core/edit_service.py's `validate_edit`): READONLY/NESTED/
    LIST_NESTED fields cannot be saved through the field editor (LC-3), so
    the template never offers an edit control it can't actually submit.
    `edited`/`edit_id` are precise per-field (an applied ExtractionFieldEdit
    for THIS exact path), not "this extraction has some edit somewhere" —
    a card must never show an EDITED badge on a field that wasn't actually
    changed, and `edit_id` is what LC-3's revert button targets."""
    return {
        "path": path,
        "label": label,
        "value": value,
        "widget": widget,
        "material": material,
        "help": help_text,
        "unit": unit,
        "choices": choices,
        "glossary": glossary,
        "evidence": evidence,
        "edited": edit_id is not None,
        "edit_id": edit_id,
        "catalog_gap": catalog_gap,
        "editable": (not catalog_gap) and widget not in (READONLY, NESTED, LIST_NESTED),
        "group": group,
    }


def _fields_for_extraction(extraction: Extraction) -> list[dict[str, Any]]:
    """Build the card's per-field list for one extraction.

    Reads current_payload (LC-1e's edit-aware read path) so a card shows
    edited values, not stale originals, from the moment an edit is applied
    — even before LC-1e's consumer sweep touches sync/rollup/concepts.

    NESTED fields (e.g. "enforcement", "timeline") are flattened into their
    leaf sub-fields here — path "enforcement.max_civil_penalty_usd", not one
    field row holding a raw dict — so (a) a material leaf like a penalty
    amount or a compliance deadline gets proper honest-unknown/evidence
    treatment instead of being buried in a JSON dump, and (b) LC-3's field
    editor can target it directly: edit_service's field_path scope is
    exactly "top-level scalar" or "one level into a NESTED field", so this
    flattening produces exactly the paths edit_service accepts. Evidence
    lookup for a flattened leaf uses the dotted path ("enforcement.
    max_civil_penalty_usd"), matching how agents actually tag nested-field
    evidence spans (see tests/integration/test_law_card_e2e.py's seeded
    fixture). LIST_NESTED fields (e.g. "exceptions") are NOT flattened —
    edit_service explicitly doesn't support per-item list edits in this
    version — and render as a single read-only field row.
    """
    model_name = _extraction_type_model_name(
        extraction.extraction_type.value
        if hasattr(extraction.extraction_type, "value")
        else str(extraction.extraction_type)
    )
    payload = extraction.current_payload or {}
    active_edit_by_path: dict[str, int] = {
        e.field_path: e.id for e in extraction.field_edits
        if e.status == FieldEditStatus.applied
    }
    evidence_by_field: dict[str, list[dict]] = {}
    for span in extraction.evidence_spans or []:
        if not isinstance(span, dict):
            continue
        key = span.get("field_name") or "_unassigned"
        evidence_by_field.setdefault(key, []).append({
            "text": span.get("text"),
            "verified": bool(span.get("verified")),
            "match_tier": span.get("match_tier"),
            "loose_match": bool(span.get("loose_match", False)),
            "char_start": span.get("char_start"),
            "char_end": span.get("char_end"),
        })

    fields: list[dict[str, Any]] = []
    for field_name, value in payload.items():
        if field_name.startswith("_"):
            continue  # internal metadata keys (_prompt_hash, _model_id, ...)
        try:
            entry = get_entry(model_name, field_name)
        except KeyError:
            # A payload key with no catalog entry is a real gap (schema/
            # catalog drift) — surface it rather than silently dropping the
            # field, but don't crash the whole card over one field.
            fields.append(_field_dict(
                path=field_name, label=field_name, value=value, widget="text",
                material=False, help_text=None, unit=None, choices=None, glossary=None,
                evidence=evidence_by_field.get(field_name, []),
                edit_id=active_edit_by_path.get(field_name),
                catalog_gap=True,
            ))
            continue

        if entry.widget == NESTED:
            nested_name = nested_model_name(model_name, field_name)
            nested_entries = CATALOG.get(nested_name, {}) if nested_name else {}
            if not nested_entries:
                # No catalog for the nested model (shouldn't happen — LC-1b's
                # coverage test guards this) — fall back to a single raw row
                # rather than silently dropping the field.
                fields.append(_field_dict(
                    path=field_name, label=entry.label, value=value, widget=entry.widget,
                    material=entry.material, help_text=entry.help, unit=entry.unit,
                    choices=list(entry.choices) if entry.choices else None,
                    glossary=entry.glossary, evidence=evidence_by_field.get(field_name, []),
                    edit_id=active_edit_by_path.get(field_name),
                ))
                continue
            nested_value = value if isinstance(value, dict) else {}
            for leaf_name, leaf_entry in nested_entries.items():
                leaf_path = f"{field_name}.{leaf_name}"
                fields.append(_field_dict(
                    path=leaf_path, label=leaf_entry.label, value=nested_value.get(leaf_name),
                    widget=leaf_entry.widget, material=leaf_entry.material,
                    help_text=leaf_entry.help, unit=leaf_entry.unit,
                    choices=list(leaf_entry.choices) if leaf_entry.choices else None,
                    glossary=leaf_entry.glossary, evidence=evidence_by_field.get(leaf_path, []),
                    edit_id=active_edit_by_path.get(leaf_path), group=entry.label,
                ))
            continue

        fields.append(_field_dict(
            path=field_name, label=entry.label, value=value, widget=entry.widget,
            material=entry.material, help_text=entry.help, unit=entry.unit,
            choices=list(entry.choices) if entry.choices else None,
            glossary=entry.glossary, evidence=evidence_by_field.get(field_name, []),
            edit_id=active_edit_by_path.get(field_name),
        ))
    return fields


def _extraction_card_entry(extraction: Extraction) -> dict[str, Any]:
    breakdown = (extraction.metadata_ or {}).get("confidence_breakdown", {})
    return {
        "id": extraction.id,
        "type": (
            extraction.extraction_type.value
            if hasattr(extraction.extraction_type, "value")
            else str(extraction.extraction_type)
        ),
        "agent": extraction.agent_name,
        "section_path": (
            extraction.source_record.section_path if extraction.source_record else None
        ),
        "confidence": {
            "tier": (
                extraction.confidence_tier.value
                if hasattr(extraction.confidence_tier, "value")
                else str(extraction.confidence_tier)
            ),
            "score": extraction.confidence_score,
            "breakdown": breakdown,
        },
        "review": {
            "status": (
                extraction.review_status.value
                if hasattr(extraction.review_status, "value")
                else str(extraction.review_status)
            ),
            "human_review_state": extraction.human_review_state,
        },
        "flags": {
            "truncated": bool((extraction.metadata_ or {}).get("truncated")),
            "was_repaired": bool((extraction.metadata_ or {}).get("was_repaired")),
            "numeric_mismatch": bool(
                (extraction.metadata_ or {}).get("numeric_grounding", {}).get("has_mismatch")
            ) if isinstance((extraction.metadata_ or {}).get("numeric_grounding"), dict) else False,
        },
        "provenance": {
            "model_id": extraction.model_id,
            "template_version": extraction.template_version,
            "prompt_hash": extraction.prompt_hash,
            "run_id": extraction.run_id,
        },
        "fields": _fields_for_extraction(extraction),
    }


# ---------------------------------------------------------------------------
# Bill-level section
# ---------------------------------------------------------------------------

_BILL_LEVEL_AGENT_KEYS = {
    "enforcement_agent": "enforcement",
    "applicability_agent": "applicability",
    "compliance_timeline_agent": "compliance_timeline",
}


def _build_bill_level_section(rows: list[BillLevelExtraction]) -> dict[str, Any]:
    section: dict[str, Any] = {key: None for key in _BILL_LEVEL_AGENT_KEYS.values()}
    for row in rows:
        key = _BILL_LEVEL_AGENT_KEYS.get(row.agent_name)
        if key is None:
            continue
        payload = dict(row.payload or {})
        section[key] = {
            "payload": payload,
            "confidence_score": row.confidence_score,
            "model_id": row.model_id,
            "truncated": row.truncated,
            "input_truncated": bool(payload.get("_input_truncated")),
        }
    return section


# ---------------------------------------------------------------------------
# Gaps (honest-unknown surface)
# ---------------------------------------------------------------------------


def _compute_gaps(
    extraction_count: int,
    law_section: dict[str, Any],
    extractions: list[dict[str, Any]],
) -> list[str]:
    gaps: list[str] = []
    if extraction_count == 0:
        gaps.append("no_extractions")
    tracker = law_section["tracker"]
    has_tracker_data = any(v for k, v in tracker.items() if k != "iapp_status")
    if not has_tracker_data and not tracker.get("iapp_status"):
        gaps.append("tracker_silent")
    if extraction_count > 0 and not any(e["type"] == "preemption_signal" for e in extractions):
        gaps.append("no_preemption_extractions")
    return gaps


# ---------------------------------------------------------------------------
# List — shared by the JSON API (law_card_api.py) and the HTML page
# (law_card_routes.py) so the query logic exists in exactly one place.
# ---------------------------------------------------------------------------


@dataclass
class LawSummary:
    """One row of the law-card list — cheap to build (no full card assembly)."""

    canonical_key: str
    title: str
    short_cite: str | None
    jurisdiction: str | None
    extraction_count: int | None
    edited_count: int | None
    tier_counts: dict[str, int] | None
    human_review_state: str | None


def list_law_summaries(
    db: Session,
    q: str | None = None,
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[LawSummary], int]:
    """List laws with canonical_key set (the ones a card can be built for).

    Prefers law_card_states rollup counts when available (avoids assembling
    every card just to show a list); falls back to bare family info when no
    rollup row exists yet for a law (LC-6a's backfill hasn't run, or this is
    a freshly-ingested law) — the list still shows it, just without counts.

    Returns (summaries_for_this_page, total_matching_count).
    """
    base_query = select(DocumentFamily).where(DocumentFamily.canonical_key.isnot(None))
    if q:
        like = f"%{q}%"
        base_query = base_query.where(
            (DocumentFamily.canonical_title.ilike(like)) | (DocumentFamily.short_cite.ilike(like))
        )

    total = db.scalar(select(func.count()).select_from(base_query.subquery())) or 0

    page_query = (
        base_query.order_by(DocumentFamily.canonical_title)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    families = db.scalars(page_query).all()

    keys = [f.canonical_key for f in families]
    rollups = {
        r.canonical_key: r
        for r in db.scalars(
            select(LawCardState).where(LawCardState.canonical_key.in_(keys))
        ).all()
    } if keys else {}

    summaries = []
    for family in families:
        rollup = rollups.get(family.canonical_key)
        summaries.append(LawSummary(
            canonical_key=family.canonical_key,
            title=family.canonical_title,
            short_cite=family.short_cite,
            jurisdiction=family.source.jurisdiction_code if family.source else None,
            extraction_count=rollup.extraction_count if rollup else None,
            edited_count=rollup.edited_count if rollup else None,
            tier_counts=rollup.tier_counts if rollup else None,
            human_review_state=rollup.human_review_state if rollup else None,
        ))
    return summaries, total


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def assemble_card(
    db: Session,
    canonical_key: str,
    run_id: int | None = None,
) -> AssemblerResult:
    """Build the full card JSON for one law.

    Returns AssemblerResult(found=False) when no DocumentFamily matches the
    canonical_key at all (a real 404, distinct from a found-but-empty law).
    """
    family = db.scalars(
        select(DocumentFamily).where(DocumentFamily.canonical_key == canonical_key)
    ).first()
    if family is None:
        return AssemblerResult(
            found=False, errors=[f"No law found for canonical_key={canonical_key!r}"],
        )

    version = _resolve_current_version(db, family.id)
    if version is None:
        # A family with no ingested version at all — a real (rare) gap, not
        # an error. Build the law section from family data alone rather than
        # faking a DocumentVersion object that was never persisted.
        source = family.source
        return AssemblerResult(
            found=True,
            card={
                "law": {
                    "canonical_key": family.canonical_key,
                    "title": family.canonical_title,
                    "short_cite": family.short_cite,
                    "jurisdiction": source.jurisdiction_code if source else None,
                    "jurisdiction_name": source.jurisdiction_name if source else None,
                    "status": None,
                    "effective_date": None,
                    "sunset_date": None,
                    "source_urls": {
                        "primary": family.primary_source_url,
                        "orrick": family.orrick_reference_url,
                        "iapp": family.iapp_reference_url,
                    },
                    "tracker": {},
                },
                "run": None,
                "bill_level": _build_bill_level_section([]),
                "extractions": [],
                "gaps": ["no_document_version"],
                "render_hint": "stub",
            },
        )

    law_section = _build_law_section(family, version)

    ext_query = (
        select(Extraction)
        .join(NormalizedSourceRecord, Extraction.source_record_id == NormalizedSourceRecord.id)
        .where(NormalizedSourceRecord.document_version_id == version.id)
    )
    if run_id is not None:
        ext_query = ext_query.where(Extraction.run_id == run_id)
    extraction_rows = db.scalars(ext_query).all()
    extractions = [_extraction_card_entry(e) for e in extraction_rows]

    bill_query = select(BillLevelExtraction).where(
        BillLevelExtraction.document_version_id == version.id
    )
    if run_id is not None:
        bill_query = bill_query.where(BillLevelExtraction.run_id == run_id)
    bill_rows = db.scalars(bill_query).all()

    run_section = None
    if run_id is not None:
        run = db.get(ExtractionRun, run_id)
        if run is not None:
            run_section = {
                "id": run.id,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "is_serving": run.is_serving,
                "git_sha": run.git_sha,
            }

    gaps = _compute_gaps(len(extractions), law_section, extractions)

    return AssemblerResult(
        found=True,
        card={
            "law": law_section,
            "run": run_section,
            "bill_level": _build_bill_level_section(bill_rows),
            "extractions": extractions,
            "gaps": gaps,
            # Design Rule 7 — computed here, never left for the caller to
            # decide, so a caller can't accidentally request "full" for a
            # law with nothing extracted.
            "render_hint": "stub" if len(extractions) == 0 else "full",
        },
    )
