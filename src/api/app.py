"""Unified FastAPI application — Recommendation #6.

Single application with three route groups:
  /dashboard/ — pipeline dashboard (HTMX rendered)
  /internal/  — review API endpoints (JSON)
  /v1/        — product API endpoints (JSON, cached, rate-limited)

All share auth middleware, database connections, Pydantic models,
error handling, and deployment infrastructure.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from starlette.templating import Jinja2Templates

from src.api.middleware.auth import verify_api_key
from src.api.routes import dashboard, internal, law_card_api, law_card_routes, v1
from src.core.config import settings
from src.core.law_card_labels import (
    humanize_extracted_at,
    humanize_review_state,
    humanize_status,
    is_enforcement_visible,
)
from src.db.engine import SessionLocal

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _recover_stale_jobs() -> None:
    """Reset jobs left in transient states (fetching/parsing/running) from a prior crash."""
    from sqlalchemy import update

    from src.db.engine import SessionLocal
    from src.db.models import ExtractionJob, IngestionJob, IngestionStatus, RawArtifact

    db = SessionLocal()
    try:
        # IngestionJobs stuck in "fetching" — reset to pending (or fetched if artifact exists)
        stale_fetching = db.scalars(
            select(IngestionJob).where(IngestionJob.status == IngestionStatus.fetching)
        ).all()
        for job in stale_fetching:
            has_artifact = db.scalar(
                select(func.count()).select_from(RawArtifact).where(
                    RawArtifact.document_version_id == job.document_version_id
                )
            )
            if has_artifact:
                job.status = IngestionStatus.fetched
            else:
                job.status = IngestionStatus.pending
            job.error_message = "Recovered: process stopped during fetch"

        # IngestionJobs stuck in "parsing" — reset to fetched (artifact already downloaded)
        stale_parsing = db.execute(
            update(IngestionJob)
            .where(IngestionJob.status == IngestionStatus.parsing)
            .values(
                status=IngestionStatus.fetched,
                error_message="Recovered: process stopped during parse",
            )
        )

        # ExtractionJobs stuck in "running" — mark as interrupted
        stale_extraction = db.execute(
            update(ExtractionJob)
            .where(ExtractionJob.status == "running")
            .values(
                status="interrupted",
                error_message="Recovered: process stopped during extraction",
            )
        )

        db.commit()

        counts = (
            len(stale_fetching),
            stale_parsing.rowcount,
            stale_extraction.rowcount,
        )
        if any(counts):
            logger.warning(
                "Recovered stale jobs from prior crash: "
                f"{counts[0]} fetching, {counts[1]} parsing, {counts[2]} extracting"
            )
    except Exception:
        db.rollback()
        logger.exception("Failed to recover stale jobs on startup")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    try:
        _recover_stale_jobs()
    except Exception:
        logger.warning("Skipping stale job recovery (DB may be unavailable)")
    yield


app = FastAPI(
    title="Regs Checker — AI Legal Corpus",
    version=settings.app_version,
    description=(
        "Regulatory obligation extraction, analysis, and serving platform. "
        "Extracts structured obligations from legislative text using LLM-powered agents, "
        "with human-in-the-loop review and full provenance tracking."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Static files and templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# LC-2b: Law Card status/review-state humanization (design Rule 5) — a
# single Python source of truth, not a duplicated Jinja dict, so LC-2c's
# exhaustiveness test can import the same tables the templates render from.
app.state.templates.env.globals["humanize_status"] = humanize_status
app.state.templates.env.globals["humanize_review_state"] = humanize_review_state
app.state.templates.env.globals["is_enforcement_visible"] = is_enforcement_visible
app.state.templates.env.globals["humanize_extracted_at"] = humanize_extracted_at

# Mount route groups
app.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
app.include_router(internal.router, prefix="/internal", tags=["Internal Review"])
# LC-1d: Law Card JSON API — a new module, not folded into dashboard.py
# (that file is deliberately never grown further; see docs/law_card_dashboard_plan.md).
app.include_router(law_card_api.router, tags=["Law Cards"])
# LC-2a: Law Card HTML pages — gated behind settings.law_cards_enabled
# (404s when disabled; see law_card_routes.py's module docstring).
app.include_router(law_card_routes.router, tags=["Law Cards UI"])
app.include_router(
    v1.router,
    prefix="/v1",
    tags=["Product API"],
    dependencies=[Depends(verify_api_key)],
)


@app.get("/health")
async def health_check() -> dict:
    health: dict = {"status": "healthy", "version": settings.app_version}

    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT view_name, refreshed_at FROM view_refresh_log")).all()
        health["views_last_refreshed"] = {
            row.view_name: row.refreshed_at.isoformat() for row in rows
        }
    except Exception:
        logger.exception("Failed to read view_refresh_log for /health")
        health["views_last_refreshed"] = None
    finally:
        db.close()

    return health
