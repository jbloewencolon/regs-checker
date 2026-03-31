"""Programmatic web search integration for fallback URL verification.

When the ingestion pipeline discovers that a fetched URL contains irrelevant
content (e.g. the Discovery Agent classifies it as non-AI-legislation), we
use a search API to find the correct official URL for the bill.

Supports three search backends (configured via REGS_SEARCH_PROVIDER):
  - tavily:     Tavily Search API (tavily.com)
  - serper:     Serper Google Search API (serper.dev)
  - google_cse: Google Custom Search Engine

Results are filtered to prioritise .gov and official state legislature domains.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

from src.core.config import settings

logger = structlog.get_logger()

# Domains we trust for official legislative text
_GOV_DOMAINS = {
    ".gov",
    ".state.",
    "legislature.",
    "legis.",
    "capitol.",
    "lis.",
    "leg.",
}


@dataclass
class SearchResult:
    """A single web search result."""

    title: str
    url: str
    snippet: str
    is_official: bool = False


def _is_official_domain(url: str) -> bool:
    """Heuristic: does the URL belong to a government/legislature domain?"""
    url_lower = url.lower()
    return any(domain in url_lower for domain in _GOV_DOMAINS)


def _rank_results(results: list[SearchResult]) -> list[SearchResult]:
    """Re-rank results to prioritise official government domains."""
    for r in results:
        r.is_official = _is_official_domain(r.url)
    return sorted(results, key=lambda r: (not r.is_official, 0))


def search_for_bill(query: str, max_results: int = 3) -> list[SearchResult]:
    """Search the web for a legislative bill and return top results.

    Args:
        query: Search query (e.g. "Colorado SB 205 AI regulation full text")
        max_results: Maximum results to return.

    Returns:
        List of SearchResult, ranked with .gov domains first.

    Raises:
        ValueError: If no search provider is configured.
    """
    provider = settings.search_provider

    if provider == "tavily":
        results = _search_tavily(query, max_results)
    elif provider == "serper":
        results = _search_serper(query, max_results)
    elif provider == "google_cse":
        results = _search_google_cse(query, max_results)
    else:
        raise ValueError(
            f"No search provider configured (REGS_SEARCH_PROVIDER='{provider}'). "
            f"Set to 'tavily', 'serper', or 'google_cse'."
        )

    ranked = _rank_results(results)[:max_results]
    logger.info(
        "web_search_completed",
        query=query,
        provider=provider,
        results_count=len(ranked),
        official_count=sum(1 for r in ranked if r.is_official),
    )
    return ranked


def _search_tavily(query: str, max_results: int) -> list[SearchResult]:
    """Search via Tavily API."""
    resp = httpx.post(
        "https://api.tavily.com/search",
        json={
            "api_key": settings.tavily_api_key,
            "query": query,
            "max_results": max_results + 2,  # fetch extra, filter later
            "search_depth": "basic",
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()

    return [
        SearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            snippet=r.get("content", "")[:300],
        )
        for r in data.get("results", [])
    ]


def _search_serper(query: str, max_results: int) -> list[SearchResult]:
    """Search via Serper (Google Search API)."""
    resp = httpx.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": settings.serper_api_key},
        json={"q": query, "num": max_results + 2},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()

    return [
        SearchResult(
            title=r.get("title", ""),
            url=r.get("link", ""),
            snippet=r.get("snippet", "")[:300],
        )
        for r in data.get("organic", [])
    ]


def _search_google_cse(query: str, max_results: int) -> list[SearchResult]:
    """Search via Google Custom Search Engine."""
    resp = httpx.get(
        "https://www.googleapis.com/customsearch/v1",
        params={
            "key": settings.google_cse_api_key,
            "cx": settings.google_cse_cx,
            "q": query,
            "num": min(max_results + 2, 10),
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()

    return [
        SearchResult(
            title=r.get("title", ""),
            url=r.get("link", ""),
            snippet=r.get("snippet", "")[:300],
        )
        for r in data.get("items", [])
    ]
