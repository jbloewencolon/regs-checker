"""Law Card HTML pages (LC-2a) — read-only dashboard views over
law_card_assembler.py.

New module, mounted directly in src/api/app.py (not folded into
dashboard.py — that file is deliberately never grown further, per
docs/law_card_dashboard_plan.md). Both routes are gated behind
settings.law_cards_enabled: a 404 when disabled, matching the plan's
"every phase ships flagged until LC-6a rollout" commitment (the JSON API
in law_card_api.py is intentionally NOT gated by this flag — see the
comment on Settings.law_cards_enabled in src/core/config.py).

  GET /laws                    — list page (search + rollup counts)
  GET /laws/{canonical_key}    — one law's card (all extractions, bill-level)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from src.api.routes._dashboard_helpers import _render
from src.core.config import settings
from src.core.law_card_assembler import assemble_card, list_law_summaries
from src.db.engine import get_db

router = APIRouter()


def _require_enabled() -> None:
    if not settings.law_cards_enabled:
        raise HTTPException(status_code=404, detail="Law Card dashboard is not enabled")


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
    return _render(request, "law_card.html", {"card": result.card})
