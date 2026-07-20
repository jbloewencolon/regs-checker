"""Law Card HTML surface — pages (LC-2a) and HTMX fragment endpoints for
field-level editing (LC-3a) over law_card_assembler.py / edit_service.py.

New module, mounted directly in src/api/app.py (not folded into
dashboard.py — that file is deliberately never grown further, per
docs/law_card_dashboard_plan.md). Every route here is gated behind
settings.law_cards_enabled: a 404 when disabled, matching the plan's
"every phase ships flagged until LC-6a rollout" commitment (the JSON API
in law_card_api.py is intentionally NOT gated by this flag — see the
comment on Settings.law_cards_enabled in src/core/config.py).

  GET  /laws                                                — list page
  GET  /laws/{canonical_key}                                — one law's card
  GET  /laws/{ck}/extractions/{id}/fields/{path}/edit        — swap a field row into edit mode
  GET  /laws/{ck}/extractions/{id}/fields/{path}/view        — swap back to display (Cancel)
  POST /laws/{ck}/extractions/{id}/fields/{path}/check       — dry-run validate, message-only
  POST /laws/{ck}/extractions/{id}/fields/{path}/save        — propose+apply, returns updated row
  POST /laws/{ck}/extractions/{id}/fields/{path}/edits/{eid}/revert — revert, returns updated row

The field-editor endpoints follow this repo's established HTMX-fragment
convention (review_routes.py's `/api/review/{id}/edit`): they return
rendered HTML fragments, not JSON, and reuse edit_service directly rather
than round-tripping through law_card_api.py's JSON endpoints.

LC-3b — editor identity + CSRF + optimistic lock (D-6's interim resolution:
"required reviewer-name session field + CSRF token on mutating routes";
full authn/z stays Run-1 Phase 6a):
  - `lc_editor_name` cookie remembers the last-used editor so the name
    field pre-fills instead of being retyped on every edit.
  - `lc_csrf_token` cookie + a matching hidden form field, verified on
    every state-changing POST (save/revert; `check` performs no write, so
    it's not gated). This repo has no session middleware, so this is a
    standard double-submit-cookie CSRF check, not a session-bound one.
  - `known_edit_id` hidden field captures the field's active edit id at
    the moment the edit form was opened; `field_save` re-checks it against
    the field's CURRENT edit id and rejects the save as a conflict if
    someone else edited the same field in between, instead of silently
    superseding their change.
"""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from src.api.routes._dashboard_helpers import _render
from src.api.routes.law_card_api import _load_extraction_for_law
from src.core.config import settings
from src.core.edit_service import (
    EditServiceError,
    apply_edit,
    extraction_identity_string,
    propose_edit,
    revert_edit,
    validate_edit,
)
from src.core.field_catalog import LIST_TEXT, SELECT, TEXT, TEXTAREA
from src.core.law_card_assembler import assemble_card, list_law_summaries
from src.db.engine import get_db
from src.db.models import Extraction

router = APIRouter()

_CSRF_COOKIE = "lc_csrf_token"
_EDITOR_COOKIE = "lc_editor_name"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _require_enabled() -> None:
    if not settings.law_cards_enabled:
        raise HTTPException(status_code=404, detail="Law Card dashboard is not enabled")


def _get_csrf_token(request: Request) -> str:
    return request.cookies.get(_CSRF_COOKIE) or secrets.token_hex(16)


def _set_csrf_cookie_if_new(request: Request, response: HTMLResponse, token: str) -> None:
    if request.cookies.get(_CSRF_COOKIE) != token:
        response.set_cookie(
            _CSRF_COOKIE, token, httponly=True, samesite="lax", max_age=_COOKIE_MAX_AGE,
        )


def _verify_csrf(request: Request, submitted: str) -> None:
    cookie_token = request.cookies.get(_CSRF_COOKIE)
    if not cookie_token or not submitted or cookie_token != submitted:
        raise HTTPException(
            status_code=403,
            detail="Your session token is missing or stale — refresh the page and try again.",
        )


# ---------------------------------------------------------------------------
# Pages (LC-2a)
# ---------------------------------------------------------------------------


@router.get("/laws")
def laws_list(
    request: Request,
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    _gate: None = Depends(_require_enabled),
):
    per_page = 25
    summaries, total = list_law_summaries(db, q=q, page=page, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)
    return _render(request, "laws.html", {
        "laws": summaries,
        "q": q or "",
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@router.get("/laws/{canonical_key}")
def law_card_detail(
    request: Request,
    canonical_key: str,
    run_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _gate: None = Depends(_require_enabled),
):
    result = assemble_card(db, canonical_key, run_id=run_id)
    if not result.found:
        raise HTTPException(status_code=404, detail=f"No law found for {canonical_key!r}")
    csrf_token = _get_csrf_token(request)
    response = _render(request, "law_card.html", {"card": result.card, "csrf_token": csrf_token})
    _set_csrf_cookie_if_new(request, response, csrf_token)
    return response


# ---------------------------------------------------------------------------
# Field editor (LC-3a) — helpers
# ---------------------------------------------------------------------------


def _render_macro(
    request: Request, template_name: str, macro_name: str, **kwargs: Any,
) -> HTMLResponse:
    """Render a single Jinja macro's output as an HTML fragment.

    Jinja exposes top-level `{% macro %}` definitions as attributes on a
    template's compiled `.module` — this is the standard way to call one
    macro directly (rather than a full `{% block %}`-based template) from
    Python, and it still resolves through the same Environment, so the
    `humanize_status`/etc. globals registered in app.py are available
    inside the macro exactly as they are on the full pages.
    """
    template = request.app.state.templates.get_template(template_name)
    macro = getattr(template.module, macro_name)
    return HTMLResponse(str(macro(**kwargs)))


def _find_field(
    db: Session, canonical_key: str, extraction_id: int, field_path: str,
) -> dict[str, Any]:
    """Locate one field's card-JSON entry via a fresh assemble_card() call.

    Rebuilding the whole card per field-edit request is not free, but this
    is a low-QPS internal analyst tool (matches this repo's existing
    dashboard/review-page patterns, which re-derive their display data per
    request rather than maintaining a separate read path) and it guarantees
    the editor always reflects the exact same field list — labels, widgets,
    evidence, editability — the page itself just rendered.
    """
    result = assemble_card(db, canonical_key)
    if not result.found:
        raise HTTPException(status_code=404, detail=f"No law found for {canonical_key!r}")
    extraction = next((e for e in result.card["extractions"] if e["id"] == extraction_id), None)
    if extraction is None:
        raise HTTPException(
            status_code=404, detail=f"No extraction with id={extraction_id} on this law",
        )
    field = next((f for f in extraction["fields"] if f["path"] == field_path), None)
    if field is None:
        raise HTTPException(status_code=404, detail=f"No field {field_path!r} on this extraction")
    return field


def _coerce_form_value(widget: str, raw_value: str | None) -> Any:
    """Turn a raw form string into what edit_service.validate_edit expects.

    NUMBER/DATE/BOOLEAN are passed through as-is — validate_edit already
    normalizes an empty string to None and parses/validates from there.
    LIST_TEXT needs its own split here: the textarea widget collects one
    item per line, but validate_edit given a bare string wraps it as a
    single-item list, not one item per line. TEXT/TEXTAREA/SELECT collapse
    a blank/whitespace-only submission to None (Rule 1: an intentionally
    cleared field should read as a real gap, not a blank line that looks
    unintentional).
    """
    if widget == LIST_TEXT:
        if not raw_value:
            return []
        return [line.strip() for line in raw_value.splitlines() if line.strip()]
    if widget in (TEXT, TEXTAREA, SELECT):
        value = raw_value.strip() if raw_value else ""
        return value if value else None
    return raw_value


# ---------------------------------------------------------------------------
# Field editor (LC-3a) — routes
# ---------------------------------------------------------------------------


@router.get("/laws/{canonical_key}/extractions/{extraction_id}/fields/{field_path}/edit")
def field_edit_form(
    request: Request, canonical_key: str, extraction_id: int, field_path: str,
    db: Session = Depends(get_db), _gate: None = Depends(_require_enabled),
) -> HTMLResponse:
    field = _find_field(db, canonical_key, extraction_id, field_path)
    if not field["editable"]:
        raise HTTPException(status_code=400, detail="This field cannot be edited.")
    csrf_token = _get_csrf_token(request)
    response = _render_macro(
        request, "partials/lc_field_editor.html", "field_editor_form",
        canonical_key=canonical_key, extraction_id=extraction_id, field=field,
        csrf_token=csrf_token, editor_name=request.cookies.get(_EDITOR_COOKIE, ""),
    )
    _set_csrf_cookie_if_new(request, response, csrf_token)
    return response


@router.get("/laws/{canonical_key}/extractions/{extraction_id}/fields/{field_path}/view")
def field_view(
    request: Request, canonical_key: str, extraction_id: int, field_path: str,
    db: Session = Depends(get_db), _gate: None = Depends(_require_enabled),
) -> HTMLResponse:
    field = _find_field(db, canonical_key, extraction_id, field_path)
    csrf_token = _get_csrf_token(request)
    response = _render_macro(
        request, "partials/lc_field_editor.html", "field_display",
        canonical_key=canonical_key, extraction_id=extraction_id, field=field,
        csrf_token=csrf_token,
    )
    _set_csrf_cookie_if_new(request, response, csrf_token)
    return response


@router.post("/laws/{canonical_key}/extractions/{extraction_id}/fields/{field_path}/check")
def field_check(
    request: Request, canonical_key: str, extraction_id: int, field_path: str,
    value: str | None = Form(default=None),
    db: Session = Depends(get_db), _gate: None = Depends(_require_enabled),
) -> HTMLResponse:
    """Dry-run validation — never writes anything, matches law_card_api.py's
    validate_edit_route but returns a message-only HTML fragment instead of
    JSON (the field editor stays in edit mode; only the messages area swaps)."""
    extraction = _load_extraction_for_law(db, canonical_key, extraction_id)
    field = _find_field(db, canonical_key, extraction_id, field_path)
    coerced = _coerce_form_value(field["widget"], value)
    try:
        report = validate_edit(extraction, field_path, coerced)
    except EditServiceError as e:
        return HTMLResponse(f'<div class="lc-edit-error">{e}</div>')
    html = "".join(f'<div class="lc-edit-error">{e}</div>' for e in report.errors)
    html += "".join(f'<div class="lc-edit-warning">{w}</div>' for w in report.warnings)
    if not report.errors and not report.warnings:
        html = '<div class="lc-edit-warning" style="color:var(--success);">Looks good.</div>'
    return HTMLResponse(html)


@router.post("/laws/{canonical_key}/extractions/{extraction_id}/fields/{field_path}/save")
def field_save(
    request: Request, canonical_key: str, extraction_id: int, field_path: str,
    value: str | None = Form(default=None),
    reason: str = Form(default=""),
    editor: str = Form(default=""),
    known_edit_id: str = Form(default=""),
    csrf_token: str = Form(default=""),
    db: Session = Depends(get_db), _gate: None = Depends(_require_enabled),
) -> HTMLResponse:
    _verify_csrf(request, csrf_token)
    field = _find_field(db, canonical_key, extraction_id, field_path)

    current_edit_id = str(field["edit_id"]) if field["edit_id"] else ""
    if known_edit_id != current_edit_id:
        return _render_macro(
            request, "partials/lc_field_editor.html", "field_editor_form",
            canonical_key=canonical_key, extraction_id=extraction_id, field=field,
            errors=["Someone else changed this field since you started editing. "
                    "Refresh and try again."],
            editor_name=editor, reason=reason, csrf_token=csrf_token,
        )

    extraction: Extraction = _load_extraction_for_law(db, canonical_key, extraction_id)
    coerced = _coerce_form_value(field["widget"], value)

    try:
        edit = propose_edit(
            db, extraction,
            canonical_key=canonical_key,
            extraction_identity=extraction_identity_string(extraction),
            field_path=field_path, new_value=coerced,
            reason=reason, editor=editor,
        )
    except EditServiceError as e:
        # Not committed — get_db's teardown rolls back anything flushed
        # above (matches law_card_api.py's save_edit_route error path).
        return _render_macro(
            request, "partials/lc_field_editor.html", "field_editor_form",
            canonical_key=canonical_key, extraction_id=extraction_id, field=field,
            errors=[str(e)], editor_name=editor, reason=reason, csrf_token=csrf_token,
        )

    result = apply_edit(db, edit.id, editor=editor)
    if not result.success:
        errors = result.validation.errors if result.validation else [result.error or "Save failed."]
        warnings = result.validation.warnings if result.validation else []
        return _render_macro(
            request, "partials/lc_field_editor.html", "field_editor_form",
            canonical_key=canonical_key, extraction_id=extraction_id, field=field,
            errors=errors, warnings=warnings, editor_name=editor, reason=reason,
            csrf_token=csrf_token,
        )

    db.commit()
    updated_field = _find_field(db, canonical_key, extraction_id, field_path)
    response = _render_macro(
        request, "partials/lc_field_editor.html", "field_display",
        canonical_key=canonical_key, extraction_id=extraction_id, field=updated_field,
        csrf_token=csrf_token,
    )
    if editor.strip():
        response.set_cookie(
            _EDITOR_COOKIE, editor.strip(), samesite="lax", max_age=_COOKIE_MAX_AGE,
        )
    return response


@router.post("/laws/{canonical_key}/extractions/{extraction_id}/fields/{field_path}/edits/{edit_id}/revert")
def field_revert(
    request: Request, canonical_key: str, extraction_id: int, field_path: str, edit_id: int,
    csrf_token: str = Form(default=""),
    db: Session = Depends(get_db), _gate: None = Depends(_require_enabled),
) -> HTMLResponse:
    _verify_csrf(request, csrf_token)
    result = revert_edit(db, edit_id)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    db.commit()
    field = _find_field(db, canonical_key, extraction_id, field_path)
    return _render_macro(
        request, "partials/lc_field_editor.html", "field_display",
        canonical_key=canonical_key, extraction_id=extraction_id, field=field,
        csrf_token=_get_csrf_token(request),
    )
