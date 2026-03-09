"""Unified FastAPI application — Recommendation #6.

Single application with two route groups:
  /internal/ — review endpoints (HTMX rendered)
  /v1/       — product API endpoints (JSON, cached, rate-limited)

Both share auth middleware, database connections, Pydantic models,
error handling, and deployment infrastructure.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api.routes import internal, v1
from src.core.config import settings

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

# Mount route groups
app.include_router(internal.router, prefix="/internal", tags=["Internal Review"])
app.include_router(v1.router, prefix="/v1", tags=["Product API"])


@app.get("/health")
async def health_check() -> dict:
    return {"status": "healthy", "version": settings.app_version}
