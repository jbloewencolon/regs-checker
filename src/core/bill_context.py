"""Bill-level context builder for extraction agents.

Extracts definitions, scope/applicability sections, and structural metadata
from a bill's passages so that per-passage extraction agents have full
context about the bill they're analyzing — without an extra LLM call.

The assembled context is cached in DocumentVersion.metadata_["bill_context"]
so it's computed once per bill, not once per extraction run.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger()

# Bump this when build_bill_context() output structure changes so that
# stale cached contexts in DocumentVersion.metadata_ are rebuilt automatically.
_BILL_CONTEXT_VERSION = "v2"

# ── Section classification patterns ──────────────────────────────────────────

# Patterns that identify definition sections (case-insensitive)
_DEFINITION_PATTERNS = re.compile(
    r"(?i)"
    r"(?:^|\b)"
    r"(?:"
    r"definition[s]?"
    r"|as used in this (?:section|chapter|act|article|title|part)"
    r"|for (?:the )?purpose[s]? of this (?:section|chapter|act|article|title|part)"
    r"|the following terms (?:shall )?have the (?:following )?meaning"
    r"|(?:shall |the term |as used herein[,]? )[\"\u201c].+?[\"\u201d] means"
    r")"
)

# Patterns that identify scope/applicability sections
_SCOPE_PATTERNS = re.compile(
    r"(?i)"
    r"(?:^|\b)"
    r"(?:"
    r"scope"
    r"|applicab(?:ility|le)"
    r"|purpose[s]? (?:and scope|of this)"
    r"|this (?:act|chapter|article|section|title|part) (?:shall )?appl(?:y|ies)"
    r"|(?:shall|does) not apply"
    r"|(?:are |is |be )?exempt(?:ion[s]?|ed)?"
    r"|short title"
    r"|legislative (?:findings?|intent|purpose)"
    r"|findings? and (?:purpose|declaration)"
    r")"
)

# Section paths that strongly signal definitions
_DEF_SECTION_PATH = re.compile(
    r"(?i)(?:definition|meaning|interpretation|term[s]?)"
)

# Section paths that strongly signal scope/applicability
_SCOPE_SECTION_PATH = re.compile(
    r"(?i)(?:scope|applicab|purpose|finding|intent|short.title|exempt)"
)

# Patterns that identify enforcement/penalty sections
_ENFORCEMENT_PATTERNS = re.compile(
    r"(?i)"
    r"(?:^|\b)"
    r"(?:"
    r"penalt(?:y|ies)"
    r"|civil (?:penalty|action|fine|liability|remedy|remedies)"
    r"|criminal (?:penalty|penalties|offense)"
    r"|enforcement"
    r"|violation[s]?"
    r"|liable? (?:for|to)"
    r"|fine[s]? (?:of|not to exceed|up to)"
    r"|right of action"
    r"|private right"
    r"|attorney general"
    r"|cause[s]? of action"
    r"|injunctive relief"
    r"|cure period"
    r"|notice and cure"
    r")"
)

# Section paths that strongly signal enforcement/penalty sections
_ENFORCEMENT_SECTION_PATH = re.compile(
    r"(?i)(?:penalt|enforc|violation|remedy|remedies|sanction|fine|liability|action)"
)

# Budget: max chars for each context section to stay within token limits
MAX_DEFINITIONS_CHARS = 30000
MAX_SCOPE_CHARS = 20000
MAX_ENFORCEMENT_CHARS = 10000
MAX_STRUCTURE_CHARS = 5000


def build_bill_context(
    passages: list[dict[str, Any]],
    force: bool = False,
) -> dict[str, Any]:
    """Build bill-level context from a list of passage dicts.

    Each passage dict must have:
        - text_content: str
        - section_path: str | None
        - ordinal: int

    Returns a dict with:
        - definitions: str — concatenated definitions text (truncated to budget)
        - scope: str — concatenated scope/applicability text (truncated to budget)
        - structure: str — section outline (section_path list)
        - defined_terms: list[str] — extracted term names
        - stats: dict — counts of what was found
    """
    definitions_parts: list[tuple[int, str]] = []  # (ordinal, text)
    scope_parts: list[tuple[int, str]] = []
    enforcement_parts: list[tuple[int, str]] = []
    section_paths: list[tuple[int, str]] = []
    defined_terms: list[str] = []

    for p in passages:
        text = p.get("text_content", "")
        section_path = p.get("section_path") or ""
        ordinal = p.get("ordinal", 0)

        if section_path:
            section_paths.append((ordinal, section_path))

        is_def = _is_definition_passage(text, section_path)
        is_scope = _is_scope_passage(text, section_path)
        is_enforcement = _is_enforcement_passage(text, section_path)

        if is_def:
            definitions_parts.append((ordinal, text))
            defined_terms.extend(_extract_defined_terms(text))
        if is_scope:
            scope_parts.append((ordinal, text))
        if is_enforcement and not is_def:
            # Skip definition sections that mention penalties in passing
            enforcement_parts.append((ordinal, text))

    # Sort by ordinal to preserve document order
    definitions_parts.sort(key=lambda x: x[0])
    scope_parts.sort(key=lambda x: x[0])
    enforcement_parts.sort(key=lambda x: x[0])
    section_paths.sort(key=lambda x: x[0])

    # Assemble and truncate
    definitions_text = _assemble_and_truncate(
        [text for _, text in definitions_parts],
        MAX_DEFINITIONS_CHARS,
    )
    scope_text = _assemble_and_truncate(
        [text for _, text in scope_parts],
        MAX_SCOPE_CHARS,
    )
    enforcement_text = _assemble_and_truncate(
        [text for _, text in enforcement_parts],
        MAX_ENFORCEMENT_CHARS,
    )
    structure_text = _build_structure_outline(section_paths, MAX_STRUCTURE_CHARS)

    # Deduplicate defined terms
    seen = set()
    unique_terms = []
    for term in defined_terms:
        lower = term.lower()
        if lower not in seen:
            seen.add(lower)
            unique_terms.append(term)

    context = {
        "_version": _BILL_CONTEXT_VERSION,
        "definitions": definitions_text,
        "scope": scope_text,
        "enforcement": enforcement_text,
        "structure": structure_text,
        "defined_terms": unique_terms,
        "stats": {
            "definition_passages": len(definitions_parts),
            "scope_passages": len(scope_parts),
            "enforcement_passages": len(enforcement_parts),
            "total_passages": len(passages),
            "defined_terms_count": len(unique_terms),
        },
    }

    logger.info(
        "bill_context_built",
        definition_passages=len(definitions_parts),
        scope_passages=len(scope_parts),
        enforcement_passages=len(enforcement_parts),
        defined_terms=len(unique_terms),
        total_passages=len(passages),
        definitions_chars=len(definitions_text),
        scope_chars=len(scope_text),
        enforcement_chars=len(enforcement_text),
    )

    return context


def _is_definition_passage(text: str, section_path: str) -> bool:
    """Determine if a passage is a definitions section."""
    if _DEF_SECTION_PATH.search(section_path):
        return True
    if _DEFINITION_PATTERNS.search(text[:500]):
        return True
    return False


def _is_scope_passage(text: str, section_path: str) -> bool:
    """Determine if a passage is a scope/applicability section."""
    if _SCOPE_SECTION_PATH.search(section_path):
        return True
    if _SCOPE_PATTERNS.search(text[:500]):
        return True
    return False


def _is_enforcement_passage(text: str, section_path: str) -> bool:
    """Determine if a passage is an enforcement/penalty section."""
    if _ENFORCEMENT_SECTION_PATH.search(section_path):
        return True
    if _ENFORCEMENT_PATTERNS.search(text[:500]):
        return True
    return False


# Pattern to extract quoted defined terms like "artificial intelligence" means
_TERM_PATTERN = re.compile(
    r'[\"\u201c]([^"\u201d]{2,80})[\"\u201d]\s+'
    r'(?:means?|refers? to|shall mean|is defined as|has the meaning)'
)


def _extract_defined_terms(text: str) -> list[str]:
    """Pull out quoted term names from a definitions passage."""
    return [m.group(1).strip() for m in _TERM_PATTERN.finditer(text)]


def _assemble_and_truncate(parts: list[str], max_chars: int) -> str:
    """Join passage texts with separator, truncating at budget."""
    if not parts:
        return ""

    result = []
    total = 0
    for part in parts:
        part = part.strip()
        if not part:
            continue
        added_len = len(part) + 4  # account for separator
        if total + added_len > max_chars:
            remaining = max_chars - total - 4  # separator overhead
            if remaining > 100 and not result:
                # First part exceeds budget — truncate it
                result.append(part[:remaining] + " [...]")
            elif result:
                result.append("[... truncated ...]")
            break
        result.append(part)
        total += added_len

    return "\n---\n".join(result)


def _build_structure_outline(
    section_paths: list[tuple[int, str]], max_chars: int
) -> str:
    """Build a compact section outline from section paths."""
    if not section_paths:
        return ""

    lines = []
    total = 0
    for _, path in section_paths:
        line = f"  {path}"
        if total + len(line) + 1 > max_chars:
            lines.append("  ...")
            break
        lines.append(line)
        total += len(line) + 1

    return "\n".join(lines)


def get_or_build_bill_context(
    db,
    document_version_id: int,
    records: list | None = None,
) -> dict[str, Any]:
    """Get cached bill context or build and cache it.

    Checks DocumentVersion.metadata_["bill_context"]. If missing,
    builds from the bill's NormalizedSourceRecords and caches.

    Args:
        db: SQLAlchemy session
        document_version_id: ID of the DocumentVersion
        records: Optional pre-loaded records (avoids extra query)

    Returns:
        Bill context dict (definitions, scope, structure, defined_terms, stats)
    """
    from sqlalchemy import select

    from src.db.models import DocumentVersion, NormalizedSourceRecord

    dv = db.get(DocumentVersion, document_version_id)
    if not dv:
        return {}

    # Check cache — version-gated so stale contexts are rebuilt when structure changes
    meta = dv.metadata_ or {}
    cached = meta.get("bill_context")
    if (
        cached
        and not isinstance(cached, str)
        and cached.get("_version") == _BILL_CONTEXT_VERSION
    ):
        return cached

    # Load passages if not provided
    if records is None:
        records = db.scalars(
            select(NormalizedSourceRecord)
            .where(NormalizedSourceRecord.document_version_id == document_version_id)
            .order_by(NormalizedSourceRecord.ordinal)
        ).all()

    passages = [
        {
            "text_content": r.text_content,
            "section_path": r.section_path,
            "ordinal": r.ordinal,
        }
        for r in records
    ]

    bill_ctx = build_bill_context(passages)

    # Cache in DocumentVersion.metadata_
    if dv.metadata_ is None:
        dv.metadata_ = {}
    # Use dict merge to preserve existing metadata
    updated = dict(dv.metadata_)
    updated["bill_context"] = bill_ctx
    dv.metadata_ = updated
    db.flush()

    return bill_ctx
