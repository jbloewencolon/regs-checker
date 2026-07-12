"""Shared cancellation primitive for long-running background pipeline work.

Lives outside src/ingestion/extractor.py so src/agents/base.py and
src/core/llm_provider.py can check it without a circular import —
extractor.py imports agent classes (ObligationAgent, etc.), which import
src/agents/base.py, so base.py importing back from extractor.py would
create a cycle. extractor.py re-exports these names so existing
`from src.ingestion.extractor import is_cancelled` call sites (e.g. in
dashboard.py) are unaffected.
"""

from __future__ import annotations

import threading

_cancel_event = threading.Event()


def is_cancelled() -> bool:
    """Check whether cancellation has been requested."""
    return _cancel_event.is_set()


def clear_cancel() -> None:
    """Reset the cancellation flag (called at pipeline start)."""
    _cancel_event.clear()


class OperationCancelled(RuntimeError):
    """Raised by an LLM provider/agent call when it detects mid-flight
    cancellation, so callers can distinguish "operator stopped this" from
    a genuine failure worth retrying.
    """
