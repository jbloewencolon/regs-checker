"""Applicability Condition Parser — parses condition text into expression trees.

Converts free-text condition strings from extraction payloads into structured
AND/OR/NOT boolean expression trees stored in the ``applicability_conditions``
table using an adjacency-list pattern.

The parser is rule-based (no LLM call needed) and handles common legislative
patterns:
  - Conjunctive lists: "A, B, and C" → AND(A, B, C)
  - Disjunctive lists: "A or B" → OR(A, B)
  - Negation: "unless X" / "except when Y" → NOT(X)
  - Nested: "A and (B or C)" → AND(A, OR(B, C))
  - Simple conditions: "if X" → LEAF(X)

When the condition is too complex for rule-based parsing, it falls back to a
single LEAF node with the full text preserved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    ApplicabilityCondition,
    ConditionNodeType,
    Extraction,
    ExtractionType,
    NormalizedSourceRecord,
)

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Expression tree data structures
# ---------------------------------------------------------------------------


@dataclass
class ConditionNode:
    """In-memory representation of a condition tree node."""

    node_type: ConditionNodeType
    text: str | None = None
    children: list[ConditionNode] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parsing patterns
# ---------------------------------------------------------------------------

# Negation prefixes — these create NOT nodes
_NEGATION_PATTERNS = [
    re.compile(r"^\s*unless\s+", re.IGNORECASE),
    re.compile(r"^\s*except\s+(?:where|when|if|that|as)?\s*", re.IGNORECASE),
    re.compile(r"^\s*provided\s+that\s+.+\s+(?:does|do)\s+not\s+", re.IGNORECASE),
    re.compile(r"^\s*excluding\s+", re.IGNORECASE),
    re.compile(r"^\s*other\s+than\s+", re.IGNORECASE),
]

# Conditional prefixes to strip before parsing the body
_CONDITIONAL_PREFIXES = re.compile(
    r"^\s*(?:if|when|where|provided\s+that|in\s+the\s+event\s+that"
    r"|in\s+cases?\s+where|to\s+the\s+extent\s+that)\s+",
    re.IGNORECASE,
)

# Splitter for top-level AND connectives
# Handles: "A, B, and C" / "A; and B" / "A and B"
_AND_SPLIT = re.compile(
    r"(?:;\s*and\s+|,\s*and\s+|\s+and\s+)",
    re.IGNORECASE,
)

# Splitter for top-level OR connectives
_OR_SPLIT = re.compile(
    r"(?:;\s*or\s+|,\s*or\s+|\s+or\s+)",
    re.IGNORECASE,
)

# Semicolon-separated clauses (common in legislative lists)
_SEMICOLON_SPLIT = re.compile(r"\s*;\s*")

# Parenthesized sub-expressions
_PAREN_RE = re.compile(r"\(([^()]+)\)")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_condition(text: str) -> ConditionNode:
    """Parse a condition string into an expression tree.

    Returns a ConditionNode tree. Simple conditions become a single LEAF.
    Complex conditions are decomposed into AND/OR/NOT trees.
    """
    if not text or not text.strip():
        return ConditionNode(node_type=ConditionNodeType.LEAF, text="")

    text = text.strip()

    # Check for negation wrappers
    for pat in _NEGATION_PATTERNS:
        m = pat.match(text)
        if m:
            inner_text = text[m.end():].strip()
            if inner_text:
                inner = _parse_connectives(inner_text)
                return ConditionNode(
                    node_type=ConditionNodeType.NOT,
                    children=[inner],
                    metadata={"negation_keyword": m.group().strip()},
                )

    # Strip conditional prefix
    m = _CONDITIONAL_PREFIXES.match(text)
    if m:
        text = text[m.end():].strip()

    return _parse_connectives(text)


def _parse_connectives(text: str) -> ConditionNode:
    """Parse a condition body for AND/OR connectives."""
    if not text:
        return ConditionNode(node_type=ConditionNodeType.LEAF, text="")

    # Try splitting on OR first (lower precedence)
    or_parts = _split_respecting_parens(text, _OR_SPLIT)
    if len(or_parts) > 1:
        children = [_parse_connectives(p.strip()) for p in or_parts if p.strip()]
        if len(children) == 1:
            return children[0]
        return ConditionNode(node_type=ConditionNodeType.OR, children=children)

    # Try splitting on AND
    and_parts = _split_respecting_parens(text, _AND_SPLIT)
    if len(and_parts) > 1:
        children = [_parse_connectives(p.strip()) for p in and_parts if p.strip()]
        if len(children) == 1:
            return children[0]
        return ConditionNode(node_type=ConditionNodeType.AND, children=children)

    # Try semicolon-separated lists (treat as AND)
    semi_parts = _SEMICOLON_SPLIT.split(text)
    semi_parts = [p.strip() for p in semi_parts if p.strip()]
    if len(semi_parts) > 1:
        children = [_parse_connectives(p) for p in semi_parts]
        return ConditionNode(node_type=ConditionNodeType.AND, children=children)

    # Check for parenthesized sub-expression
    inner_match = _PAREN_RE.search(text)
    if inner_match:
        # If the entire text is parenthesized, unwrap it
        stripped = text.strip()
        if stripped.startswith("(") and stripped.endswith(")"):
            return _parse_connectives(stripped[1:-1].strip())

    # Leaf node — atomic condition
    return ConditionNode(node_type=ConditionNodeType.LEAF, text=text)


def _split_respecting_parens(text: str, pattern: re.Pattern) -> list[str]:
    """Split text on a pattern but skip matches inside parentheses."""
    # Simple approach: mask parenthesized content, split, then unmask
    masked = text
    parens: list[tuple[str, str]] = []
    paren_idx = 0

    # Replace parenthesized groups with placeholders
    while True:
        m = _PAREN_RE.search(masked)
        if not m:
            break
        placeholder = f"\x00PAREN{paren_idx}\x00"
        parens.append((placeholder, m.group()))
        masked = masked[:m.start()] + placeholder + masked[m.end():]
        paren_idx += 1

    # Split the masked text
    parts = pattern.split(masked)

    # Unmask
    result = []
    for part in parts:
        for placeholder, original in parens:
            part = part.replace(placeholder, original)
        result.append(part)

    return result


# ---------------------------------------------------------------------------
# Tree → DB writer
# ---------------------------------------------------------------------------


def write_condition_tree(
    db: Session,
    extraction_id: int,
    root: ConditionNode,
) -> int:
    """Write a condition tree to the applicability_conditions table.

    Returns the number of nodes written.
    """
    count = 0

    def _write_node(
        node: ConditionNode,
        parent_db_id: int | None,
        ordinal: int,
    ) -> None:
        nonlocal count
        row = ApplicabilityCondition(
            extraction_id=extraction_id,
            parent_id=parent_db_id,
            node_type=node.node_type,
            ordinal=ordinal,
            condition_text=node.text,
            metadata_=node.metadata if node.metadata else {},
        )
        db.add(row)
        db.flush()  # get row.id for children
        count += 1

        for i, child in enumerate(node.children):
            _write_node(child, row.id, i)

    _write_node(root, None, 0)
    return count


# ---------------------------------------------------------------------------
# Batch processing — parse conditions for all extractions
# ---------------------------------------------------------------------------

# Extraction types that have parseable condition fields
_CONDITION_FIELDS: dict[str, list[str]] = {
    "obligation": ["condition"],
    "threshold": ["threshold_condition"],
    "exception": ["conditions"],
    "rights_protection": ["trigger_condition"],
}


def parse_conditions_for_extraction(
    db: Session,
    extraction: Extraction,
) -> int:
    """Parse condition fields from a single extraction's payload.

    Returns the number of condition tree nodes created.
    """
    ext_type = (
        extraction.extraction_type.value
        if isinstance(extraction.extraction_type, ExtractionType)
        else str(extraction.extraction_type)
    )

    fields = _CONDITION_FIELDS.get(ext_type)
    if not fields:
        return 0

    payload = extraction.payload or {}
    total_nodes = 0

    for field_name in fields:
        condition_text = payload.get(field_name)
        if not condition_text or not isinstance(condition_text, str):
            continue

        condition_text = condition_text.strip()
        if not condition_text:
            continue

        # Parse the condition text into a tree
        tree = parse_condition(condition_text)

        # Skip trivial single-leaf trees with very short text (< 10 chars)
        if (
            tree.node_type == ConditionNodeType.LEAF
            and tree.text
            and len(tree.text) < 10
            and not tree.children
        ):
            continue

        # Tag the root with source field
        tree.metadata["source_field"] = field_name

        nodes = write_condition_tree(db, extraction.id, tree)
        total_nodes += nodes

    return total_nodes


def run_condition_parsing(
    db: Session,
    document_version_id: int | None = None,
    on_progress: callable | None = None,
) -> dict[str, Any]:
    """Parse conditions for extractions that don't yet have applicability trees.

    Args:
        db: SQLAlchemy session
        document_version_id: Limit to a single document (None = all pending).
        on_progress: Optional callback for status messages.

    Returns:
        Summary dict with counts.
    """
    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    # Types that have condition fields
    target_types = [ExtractionType(t) for t in _CONDITION_FIELDS.keys()]

    # Find extractions with condition-bearing types that don't yet have
    # applicability_conditions rows
    query = (
        select(Extraction)
        .where(Extraction.extraction_type.in_(target_types))
        .outerjoin(
            ApplicabilityCondition,
            ApplicabilityCondition.extraction_id == Extraction.id,
        )
        .where(ApplicabilityCondition.id.is_(None))
    )

    if document_version_id:
        query = query.join(
            NormalizedSourceRecord,
            NormalizedSourceRecord.id == Extraction.source_record_id,
        ).where(
            NormalizedSourceRecord.document_version_id == document_version_id
        )

    extractions = db.scalars(query).all()

    if not extractions:
        _log("No extractions pending condition parsing.")
        return {
            "extractions_processed": 0,
            "nodes_created": 0,
            "extractions_with_conditions": 0,
        }

    _log(f"Parsing conditions for {len(extractions)} extractions...")

    total_nodes = 0
    with_conditions = 0
    errors = 0

    for i, extraction in enumerate(extractions):
        try:
            # Use savepoint so one failed parse doesn't poison the session
            sp = db.begin_nested()
            nodes = parse_conditions_for_extraction(db, extraction)
            sp.commit()
            total_nodes += nodes
            if nodes > 0:
                with_conditions += 1
        except Exception as e:
            sp.rollback()
            errors += 1
            logger.error(
                "condition_parse_failed",
                extraction_id=extraction.id,
                error=str(e),
            )

        if (i + 1) % 100 == 0:
            db.commit()
            _log(f"  {i + 1}/{len(extractions)} extractions processed...")

    db.commit()

    _log(
        f"Condition parsing complete: {with_conditions} extractions had parseable "
        f"conditions → {total_nodes} tree nodes created ({errors} errors)"
    )

    return {
        "extractions_processed": len(extractions),
        "nodes_created": total_nodes,
        "extractions_with_conditions": with_conditions,
        "errors": errors,
    }
