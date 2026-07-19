"""Edit service — propose/validate/apply/revert lifecycle for human corrections
to extraction fields (LC-1c). This is the engine G-1's fix runs on: the
Extraction.payload column is never mutated after creation; every correction
flows through an ExtractionFieldEdit row and gets materialized onto
Extraction.effective_payload.

See docs/law_card_dashboard_plan.md §3.3 and docs/law_card_decisions.md
(D-3, D-5, D-6) for the design this implements.

Scope for this pass (documented, not silently limited): field_path supports
a top-level scalar field ("subject") or exactly one level of nesting into a
NESTED object field ("enforcement.max_civil_penalty_usd"). Editing an item
inside a LIST_NESTED field (e.g. one exception in a list) or replacing a
whole nested object at once is out of scope — ValueError with a clear
message, not a silent no-op or wrong write.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.date_normalizer import normalize_date
from src.core.field_catalog import (
    BOOLEAN,
    DATE,
    LIST_NESTED,
    LIST_TEXT,
    NESTED,
    NUMBER,
    READONLY,
    FieldCatalogEntry,
    FieldCatalogError,
    get_entry,
    nested_model_name,
)
from src.core.numeric_grounding import (
    NESTED_FIELD_PARENT,
    NUMERIC_FIELD_UNITS,
    check_numeric_grounding,
)
from src.db.models import (
    Extraction,
    ExtractionFieldEdit,
    FieldEditStatus,
)

_UNSET = object()


@dataclass
class ValidationReport:
    """Result of validating a proposed edit. `valid=False` means a hard type
    error — the edit cannot be applied as-is. Warnings never block apply;
    they're informational (shown to the editor, stored for audit)."""

    valid: bool
    normalized_value: Any
    errors: list[str] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


class EditServiceError(Exception):
    """Raised for structural problems (bad field_path, missing row, lock
    conflict) — distinct from ValidationReport's per-value warnings/errors,
    which are expected, recoverable outcomes of normal use."""


class LockConflictError(EditServiceError):
    """The extraction changed since the editor last loaded it."""


# ---------------------------------------------------------------------------
# Field-path resolution
# ---------------------------------------------------------------------------


def _extraction_type_model_name(extraction: Extraction) -> str:
    # Mirrors law_card_assembler._extraction_type_model_name — kept as a
    # separate small copy rather than a shared import to avoid a
    # core-module-to-core-module dependency for four lines; if this drifts,
    # tests/unit/test_edit_service.py::test_model_name_mapping_matches_assembler
    # catches it.
    mapping = {
        "obligation": "ObligationPayload", "enforcement": "ObligationPayload",
        "timeline": "ObligationPayload", "definition": "DefinitionActorPayload",
        "actor_mapping": "DefinitionActorPayload", "framework_ref": "DefinitionActorPayload",
        "threshold": "ThresholdExceptionPayload", "exception": "ThresholdExceptionPayload",
        "rights_protection": "RightsProtectionPayload",
        "compliance_mechanism": "ComplianceMechanismPayload",
        "preemption_signal": "PreemptionSignalPayload", "ambiguity": "AmbiguityPayload",
    }
    ext_type = (
        extraction.extraction_type.value
        if hasattr(extraction.extraction_type, "value")
        else str(extraction.extraction_type)
    )
    return mapping.get(ext_type, ext_type)


def _resolve_field_entry(extraction: Extraction, field_path: str) -> FieldCatalogEntry:
    """Resolve a dotted field_path to its catalog entry, enforcing the
    one-level-of-nesting scope documented in the module docstring."""
    segments = field_path.split(".")
    root_model = _extraction_type_model_name(extraction)

    if len(segments) == 1:
        try:
            return get_entry(root_model, segments[0])
        except FieldCatalogError as e:
            raise EditServiceError(str(e)) from e

    if len(segments) == 2:
        parent, leaf = segments
        try:
            parent_entry = get_entry(root_model, parent)
        except FieldCatalogError as e:
            raise EditServiceError(str(e)) from e
        if parent_entry.widget not in (NESTED, LIST_NESTED):
            raise EditServiceError(
                f"{field_path!r}: {root_model}.{parent} is not a nested field "
                "— dotted paths are only valid into NESTED/LIST_NESTED fields."
            )
        if parent_entry.widget == LIST_NESTED:
            raise EditServiceError(
                f"{field_path!r}: editing individual items inside a list field "
                f"({root_model}.{parent}) is not supported in this version."
            )
        nested_name = nested_model_name(root_model, parent)
        if nested_name is None:
            raise EditServiceError(f"{field_path!r}: could not resolve nested model for {parent}.")
        try:
            return get_entry(nested_name, leaf)
        except FieldCatalogError as e:
            raise EditServiceError(str(e)) from e

    raise EditServiceError(
        f"{field_path!r}: field paths deeper than one level of nesting are not supported."
    )


def _get_path(payload: dict, field_path: str) -> Any:
    segments = field_path.split(".")
    node: Any = payload
    for seg in segments:
        if not isinstance(node, dict):
            return None
        node = node.get(seg)
    return node


def _set_path(payload: dict, field_path: str, value: Any) -> dict:
    """Return a NEW payload dict with field_path set to value — never
    mutates the input, so callers always control exactly which dict object
    ends up on effective_payload."""
    result = copy.deepcopy(payload)
    segments = field_path.split(".")
    node = result
    for seg in segments[:-1]:
        if not isinstance(node.get(seg), dict):
            node[seg] = {}
        node = node[seg]
    node[segments[-1]] = value
    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_edit(
    extraction: Extraction,
    field_path: str,
    new_value: Any,
) -> ValidationReport:
    """Validate a proposed new value for one field. Never raises for a bad
    *value* (returns valid=False with errors instead) — only raises
    EditServiceError for a structurally invalid field_path, which is a
    programming/API-caller error, not a user-correctable one."""
    entry = _resolve_field_entry(extraction, field_path)
    errors: list[str] = []
    warnings: list[str] = []
    normalized = new_value

    if entry.widget == READONLY:
        errors.append("This field is system-generated evidence and cannot be edited directly.")
        return ValidationReport(valid=False, normalized_value=new_value, errors=errors)

    if entry.widget in (NESTED, LIST_NESTED):
        errors.append("This field is a group of values — edit its individual fields instead.")
        return ValidationReport(valid=False, normalized_value=new_value, errors=errors)

    if entry.widget == NUMBER:
        if new_value is None or new_value == "":
            normalized = None
        else:
            try:
                normalized = int(new_value) if float(new_value).is_integer() else float(new_value)
            except (TypeError, ValueError):
                errors.append(f"“{new_value}” isn't a number.")
                return ValidationReport(valid=False, normalized_value=new_value, errors=errors)

    elif entry.widget == DATE:
        if new_value is None or new_value == "":
            normalized = None
        else:
            parsed = normalize_date(str(new_value))
            if parsed is None:
                errors.append(
                    f"“{new_value}” isn't a date we recognize. Try a format like 2026-07-01."
                )
                return ValidationReport(valid=False, normalized_value=new_value, errors=errors)
            normalized = parsed

    elif entry.widget == BOOLEAN:
        if new_value in (None, ""):
            normalized = None
        elif isinstance(new_value, bool):
            normalized = new_value
        elif str(new_value).strip().lower() in ("true", "yes", "1"):
            normalized = True
        elif str(new_value).strip().lower() in ("false", "no", "0"):
            normalized = False
        else:
            errors.append(f"“{new_value}” isn't Yes, No, or Unknown.")
            return ValidationReport(valid=False, normalized_value=new_value, errors=errors)

    elif entry.widget == LIST_TEXT:
        if new_value is None:
            normalized = []
        elif isinstance(new_value, str):
            normalized = [new_value] if new_value.strip() else []
        elif isinstance(new_value, list):
            if not all(isinstance(v, str) for v in new_value):
                errors.append("Every item in this list must be text.")
                return ValidationReport(valid=False, normalized_value=new_value, errors=errors)
            normalized = new_value
        else:
            errors.append("This field expects a list of text values.")
            return ValidationReport(valid=False, normalized_value=new_value, errors=errors)

    elif entry.widget == "select" and entry.choices and new_value not in (None, "", *entry.choices):
        # Never blocks (EAR-5-1 spirit: pass through + flag for vocab review,
        # never reject legal data), but the editor should know it's non-standard.
        warnings.append(
            f"“{new_value}” isn't one of the standard values for this field — "
            "it will be flagged for vocabulary review, but your edit is saved."
        )

    leaf_field = field_path.split(".")[-1]
    if leaf_field in NUMERIC_FIELD_UNITS and normalized is not None:
        _warn_if_ungrounded(extraction, leaf_field, normalized, warnings)

    return ValidationReport(
        valid=not errors, normalized_value=normalized, errors=errors, warnings=warnings,
    )


def _warn_if_ungrounded(
    extraction: Extraction, leaf_field: str, normalized_value: Any, warnings: list[str]
) -> None:
    """Numeric-vs-evidence check (design §3.5): warn, never block, when an
    edited numeric value doesn't match any verified quote for this
    extraction. Reuses EA2-1's check_numeric_grounding rather than
    re-implementing number extraction."""
    trial_payload = copy.deepcopy(extraction.current_payload or {})
    parent = NESTED_FIELD_PARENT.get(leaf_field)
    if parent:
        trial_payload.setdefault(parent, {})[leaf_field] = normalized_value
    else:
        trial_payload[leaf_field] = normalized_value

    results = check_numeric_grounding(trial_payload, extraction.evidence_spans or [])
    result = results.get(leaf_field)
    if result is not None and result.status == "mismatch":
        found = ", ".join(str(c) for c in result.candidates_found) or "none"
        warnings.append(
            f"This value doesn't match the amount found in the quoted law text "
            f"(source text mentions: {found}). Double-check before saving."
        )


# ---------------------------------------------------------------------------
# Propose / apply / revert
# ---------------------------------------------------------------------------


def propose_edit(
    db: Session,
    extraction: Extraction,
    canonical_key: str,
    extraction_identity: str,
    field_path: str,
    new_value: Any,
    reason: str,
    editor: str,
    lock_token: str | None = None,
) -> ExtractionFieldEdit:
    """Create a proposed edit. Supersedes any existing active (proposed or
    applied) edit for the same field first — the DB's partial unique index
    (uq_field_edits_active_field) allows only one at a time, so this must
    happen before the new row is inserted, not as an afterthought."""
    if not reason or not reason.strip():
        raise EditServiceError("A reason is required for every edit.")
    if not editor or not editor.strip():
        raise EditServiceError("Editor identity is required for every edit.")

    _resolve_field_entry(extraction, field_path)  # raises EditServiceError on bad path

    existing = db.scalars(
        select(ExtractionFieldEdit).where(
            ExtractionFieldEdit.extraction_id == extraction.id,
            ExtractionFieldEdit.field_path == field_path,
            ExtractionFieldEdit.status.in_([FieldEditStatus.proposed, FieldEditStatus.applied]),
        )
    ).all()
    for row in existing:
        row.status = FieldEditStatus.superseded

    old_value = _get_path(extraction.current_payload or {}, field_path)
    edit = ExtractionFieldEdit(
        extraction_id=extraction.id,
        canonical_key=canonical_key,
        extraction_identity=extraction_identity,
        field_path=field_path,
        old_value=old_value,
        new_value=new_value,
        reason=reason.strip(),
        status=FieldEditStatus.proposed,
        editor=editor.strip(),
        lock_token=lock_token,
    )
    db.add(edit)
    db.flush()
    return edit


@dataclass
class ApplyResult:
    success: bool
    edit: ExtractionFieldEdit | None = None
    validation: ValidationReport | None = None
    error: str | None = None


def apply_edit(
    db: Session, edit_id: int, editor: str, lock_token: str | None = None,
) -> ApplyResult:
    """Validate (again — defense in depth against a stale proposal) and
    apply an edit, materializing effective_payload as the base payload with
    every currently-applied edit for this extraction replayed on top."""
    edit = db.get(ExtractionFieldEdit, edit_id)
    if edit is None:
        return ApplyResult(success=False, error=f"No edit found with id={edit_id}.")
    if edit.status != FieldEditStatus.proposed:
        return ApplyResult(success=False, error=f"Edit is {edit.status.value}, not proposed.")

    extraction = edit.extraction
    if lock_token is not None and edit.lock_token is not None and lock_token != edit.lock_token:
        return ApplyResult(
            success=False,
            error="This extraction changed since you started editing. Refresh and try again.",
        )

    report = validate_edit(extraction, edit.field_path, edit.new_value)
    edit.validation_report = report.to_dict()
    if not report.valid:
        return ApplyResult(success=False, edit=edit, validation=report, error="Validation failed.")

    edit.new_value = report.normalized_value
    edit.status = FieldEditStatus.applied
    edit.applied_at = datetime.now(UTC)
    edit.editor = editor.strip() if editor else edit.editor
    # SessionLocal is configured with autoflush=False (src/db/engine.py) —
    # without an explicit flush here, _recompute_effective_payload's own
    # query for status=="applied" edits would run against the DB's
    # pre-update state and miss the edit we just changed in memory above.
    db.flush()

    _recompute_effective_payload(db, extraction)
    extraction.human_review_state = "edited"
    db.flush()

    return ApplyResult(success=True, edit=edit, validation=report)


@dataclass
class RevertResult:
    success: bool
    error: str | None = None


def revert_edit(db: Session, edit_id: int) -> RevertResult:
    edit = db.get(ExtractionFieldEdit, edit_id)
    if edit is None:
        return RevertResult(success=False, error=f"No edit found with id={edit_id}.")
    if edit.status != FieldEditStatus.applied:
        return RevertResult(success=False, error=f"Edit is {edit.status.value}, not applied.")

    extraction = edit.extraction
    edit.status = FieldEditStatus.reverted
    db.flush()  # see apply_edit's comment — autoflush is off for this session

    _recompute_effective_payload(db, extraction)
    remaining_applied = db.scalars(
        select(ExtractionFieldEdit).where(
            ExtractionFieldEdit.extraction_id == extraction.id,
            ExtractionFieldEdit.status == FieldEditStatus.applied,
        )
    ).all()
    extraction.human_review_state = "edited" if remaining_applied else "unedited"
    db.flush()

    return RevertResult(success=True)


def _recompute_effective_payload(db: Session, extraction: Extraction) -> None:
    """Rebuild effective_payload from scratch: base payload + every
    currently-applied edit for this extraction, in application order.
    Rebuilding from the immutable base (rather than patching the existing
    effective_payload in place) means a revert can never leave a stray
    change behind from an edit applied and reverted out of order."""
    applied_edits = db.scalars(
        select(ExtractionFieldEdit)
        .where(
            ExtractionFieldEdit.extraction_id == extraction.id,
            ExtractionFieldEdit.status == FieldEditStatus.applied,
        )
        .order_by(ExtractionFieldEdit.applied_at)
    ).all()

    if not applied_edits:
        extraction.effective_payload = None
        return

    payload = copy.deepcopy(extraction.payload)
    for e in applied_edits:
        payload = _set_path(payload, e.field_path, e.new_value)
    extraction.effective_payload = payload
