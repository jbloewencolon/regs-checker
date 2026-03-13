"""Unified FastAPI application — Recommendation #6.

Single application with three route groups:
  /dashboard/ — pipeline dashboard (HTMX rendered)
  /internal/  — review API endpoints (JSON)
  /v1/        — product API endpoints (JSON, cached, rate-limited)

All share auth middleware, database connections, Pydantic models,
error handling, and deployment infrastructure.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from src.api.routes import dashboard, internal, v1
from src.core.config import settings

BASE_DIR = Path(__file__).resolve().parent.parent.parent

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
)

# Static files and templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Mount route groups
app.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
app.include_router(internal.router, prefix="/internal", tags=["Internal Review"])
app.include_router(v1.router, prefix="/v1", tags=["Product API"])


@app.get("/health")
async def health_check() -> dict:
    return {"status": "healthy", "version": settings.app_version}
