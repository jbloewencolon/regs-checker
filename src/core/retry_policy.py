"""Typed retry taxonomy for the extraction pipeline (RR6d).

Each failure category has its own policy because the right response differs:
  LLM_TIMEOUT   — network/GPU saturation; exponential backoff helps
  VALIDATION    — same input → same invalid output; never retry
  DB_CONFLICT   — transient lock; one short retry
  PARSE         — same content → same parse failure; never retry
  SYNC          — remote API hiccups; backoff up to 3 attempts
  LLM_CALL      — generic LLM call; short retry with jitter

Usage:
    from src.core.retry_policy import with_retry, ErrorCategory

    @with_retry(ErrorCategory.LLM_CALL)
    def call_model(...):
        ...

    # Or inline:
    from tenacity import retry
    from src.core.retry_policy import get_tenacity_kwargs

    result = retry(**get_tenacity_kwargs(ErrorCategory.LLM_TIMEOUT))(fn)()
"""

from __future__ import annotations

from enum import Enum
from typing import Callable

import structlog
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
    wait_random_exponential,
)

logger = structlog.get_logger()


class ErrorCategory(str, Enum):
    """Pipeline failure categories with distinct retry semantics."""

    LLM_TIMEOUT = "llm_timeout"
    LLM_CALL = "llm_call"
    VALIDATION = "validation"
    DB_CONFLICT = "db_conflict"
    PARSE = "parse"
    SYNC = "sync"


# ---------------------------------------------------------------------------
# Per-category policy definitions
# ---------------------------------------------------------------------------

_POLICIES: dict[ErrorCategory, dict] = {
    # LLM timeout: GPU may be saturated; give it time (2s → 16s, 3 attempts)
    ErrorCategory.LLM_TIMEOUT: dict(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        reraise=True,
    ),
    # Generic LLM call: jitter prevents thundering herd (1–3s, 2 attempts)
    ErrorCategory.LLM_CALL: dict(
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=1, min=1, max=3),
        reraise=True,
    ),
    # Validation: same input → same invalid output — do not retry
    ErrorCategory.VALIDATION: dict(
        stop=stop_after_attempt(1),
        wait=wait_fixed(0),
        reraise=True,
    ),
    # DB conflict: transient lock; one short retry after 0.5s
    ErrorCategory.DB_CONFLICT: dict(
        stop=stop_after_attempt(2),
        wait=wait_fixed(0.5),
        reraise=True,
    ),
    # Parse failure: same content → same failure — do not retry
    ErrorCategory.PARSE: dict(
        stop=stop_after_attempt(1),
        wait=wait_fixed(0),
        reraise=True,
    ),
    # Sync: remote API hiccups; backoff with jitter (1–8s, 3 attempts)
    ErrorCategory.SYNC: dict(
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    ),
}


def get_tenacity_kwargs(category: ErrorCategory) -> dict:
    """Return tenacity decorator kwargs for the given error category."""
    return _POLICIES[category]


def with_retry(category: ErrorCategory) -> Callable:
    """Decorator that applies the category's retry policy to a function.

    Example:
        @with_retry(ErrorCategory.LLM_CALL)
        def call_extraction_model(prompt: str) -> str:
            ...
    """
    policy = _POLICIES[category]
    return retry(**policy)


def classify_llm_error(exc: Exception) -> ErrorCategory:
    """Classify an exception from an LLM call into a retry category.

    Inspects the exception message and type to pick the appropriate policy.
    Falls back to LLM_CALL for unrecognised errors.
    """
    msg = str(exc).lower()

    if any(kw in msg for kw in ("timeout", "timed out", "read timeout", "connect timeout")):
        return ErrorCategory.LLM_TIMEOUT

    if any(kw in msg for kw in ("validation", "pydantic", "schema", "json decode")):
        return ErrorCategory.VALIDATION

    if any(kw in msg for kw in ("conflict", "deadlock", "lock", "duplicate key")):
        return ErrorCategory.DB_CONFLICT

    if any(kw in msg for kw in ("parse", "pdf", "encoding", "decode")):
        return ErrorCategory.PARSE

    return ErrorCategory.LLM_CALL


__all__ = [
    "ErrorCategory",
    "RetryError",
    "classify_llm_error",
    "get_tenacity_kwargs",
    "with_retry",
]
