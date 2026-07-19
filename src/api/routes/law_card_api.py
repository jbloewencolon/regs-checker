"""Law Card JSON API (LC-1d) — read model + edit endpoints over
law_card_assembler.py / edit_service.py.

New module, mounted directly in src/api/app.py (not folded into
dashboard.py — that file is deliberately never grown further, per the LC
plan). See docs/law_card_dashboard_plan.md §3.2 for the endpoint design.

Endpoint shape (three actions, matching the UI flow in the design doc):
  GET  /api/laws                                              — list
  GET  /api/laws/{canonical_key}/card                         — assembled card
  POST /api/laws/{canonical_key}/extractions/{id}/validate    — dry-run "Check"
  POST /api/laws/{canonical_key}/extractions/{id}/edits       — "Save" (propose+apply)
  POST /api/edits/{edit_id}/revert                            — revert an applied edit

"Save" runs propose_edit() then apply_edit() in one request rather than
exposing them as two round-trips: edit_service's propose/apply split exists
for the service's internal integrity (a proposal can be superseded before
ever being applied), not because the UI needs a separate "propose" step
today. A future multi-step review workflow can call propose_edit directly
without an API change to this module.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.edit_service import (
    EditServiceError,
    apply_edit,
    extraction_identity_string,
    propose_edit,
    revert_edit,
    validate_edit,
)
from src.core.law_card_assembler import assemble_card, list_law_summaries
from src.db.engine import get_db
from src.db.models import DocumentFamily, Extraction, NormalizedSourceRecord

router = APIRouter()


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("/api/laws")
def list_laws(
    q: str | None = Query(default=None, description="Substring match on title or short_cite"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """List laws with canonical_key set (the ones a card can be built for).

    Query logic lives in law_card_assembler.list_law_summaries — shared with
    the HTML page (law_card_routes.py) so there's exactly one implementation.
    """
    summaries, total = list_law_summaries(db, q=q, page=page, per_page=per_page)
    items = [
        {
            "canonical_key": s.canonical_key,
            "title": s.title,
            "short_cite": s.short_cite,
            "jurisdiction": s.jurisdiction,
            "extraction_count": s.extraction_count,
            "edited_count": s.edited_count,
            "human_review_state": s.human_review_state,
        }
        for s in summaries
    ]
    return {"items": items, "page": page, "per_page": per_page, "total": total}


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------


@router.get("/api/laws/{canonical_key}/card")
def get_law_card(
    canonical_key: str,
    run_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = assemble_card(db, canonical_key, run_id=run_id)
    if not result.found:
        raise HTTPException(status_code=404, detail=f"No law found for {canonical_key!r}")
    return result.card


# ---------------------------------------------------------------------------
# Edit — helpers shared by validate/edits endpoints
# ---------------------------------------------------------------------------


def _load_extraction_for_law(
    db: Session, canonical_key: str, extraction_id: int
) -> Extraction:
    """Load an extraction and verify it actually belongs to this law.

    Defense in depth against a client passing a mismatched (canonical_key,
    extraction_id) pair — e.g. a stale card open in one tab pointing at an
    extraction_id that's since been reassigned, or a copy-paste error in a
    manually-constructed request. Raises HTTPException directly (this is a
    route helper, not library code) rather than returning None for the
    caller to check.
    """
    extraction = db.get(Extraction, extraction_id)
    if extraction is None:
        raise HTTPException(status_code=404, detail=f"No extraction with id={extraction_id}")

    family = db.scalars(
        select(DocumentFamily).where(DocumentFamily.canonical_key == canonical_key)
    ).first()
    if family is None:
        raise HTTPException(status_code=404, detail=f"No law found for {canonical_key!r}")

    record = db.get(NormalizedSourceRecord, extraction.source_record_id)
    version = record.document_version if record else None
    if record is None or version is None or version.family_id != family.id:
        raise HTTPException(
            status_code=400,
            detail=f"Extraction {extraction_id} does not belong to law {canonical_key!r}.",
        )
    return extraction


class ValidateEditRequest(BaseModel):
    field_path: str
    new_value: Any = None


@router.post("/api/laws/{canonical_key}/extractions/{extraction_id}/validate")
def validate_edit_route(
    canonical_key: str,
    extraction_id: int,
    body: ValidateEditRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Dry-run validation — the "Check" step. Never persists anything."""
    extraction = _load_extraction_for_law(db, canonical_key, extraction_id)
    try:
        report = validate_edit(extraction, body.field_path, body.new_value)
    except EditServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return report.to_dict() | {"normalized_value": report.normalized_value}


class SaveEditRequest(BaseModel):
    field_path: str
    new_value: Any = None
    reason: str = Field(..., min_length=1)
    editor: str = Field(..., min_length=1)
    lock_token: str | None = None


@router.post("/api/laws/{canonical_key}/extractions/{extraction_id}/edits")
def save_edit_route(
    canonical_key: str,
    extraction_id: int,
    body: SaveEditRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Propose + apply an edit in one call — the "Save" action."""
    extraction = _load_extraction_for_law(db, canonical_key, extraction_id)
    try:
        edit = propose_edit(
            db, extraction,
            canonical_key=canonical_key,
            extraction_identity=extraction_identity_string(extraction),
            field_path=body.field_path,
            new_value=body.new_value,
            reason=body.reason,
            editor=body.editor,
            lock_token=body.lock_token,
        )
    except EditServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    result = apply_edit(db, edit.id, editor=body.editor, lock_token=body.lock_token)
    if not result.success:
        status_code = 409 if "changed since" in (result.error or "") else 422
        raise HTTPException(
            status_code=status_code,
            detail={
                "error": result.error,
                "validation": result.validation.to_dict() if result.validation else None,
            },
        )

    db.commit()
    return {
        "edit_id": edit.id,
        "field_path": edit.field_path,
        "new_value": edit.new_value,
        "status": edit.status.value,
        "validation": result.validation.to_dict() if result.validation else None,
    }


@router.post("/api/edits/{edit_id}/revert")
def revert_edit_route(edit_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    result = revert_edit(db, edit_id)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    db.commit()
    return {"edit_id": edit_id, "reverted": True}
