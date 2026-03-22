"""Dependency Graph Builder — post-extraction agent that identifies relationships.

Runs per-document after all extraction agents have completed.  Loads ALL
extractions for a document version, sends them to GPT (openai/gpt-oss-20b with
131k context) to identify inter-extraction relationships, and writes edges
to the ``obligation_dependencies`` table.

Relationship types (from DependencyType enum):
  - requires_definition: obligation references a defined term
  - modifies: one obligation modifies/amends another
  - excepts: exception carves out from an obligation
  - enforces: enforcement mechanism backs an obligation
  - references: general cross-reference between extractions
  - supersedes: one provision replaces another
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.agents.prompt_loader import load_prompt_template, render_prompt
from src.core.llm_provider import get_extraction_provider
from src.core.config import settings
from src.db.models import (
    DependencyType,
    DocumentVersion,
    Extraction,
    ExtractionType,
    NormalizedSourceRecord,
    ObligationDependency,
)

logger = structlog.get_logger()

# GPT model with 131k context — can handle hundreds of extractions per document
MODEL_OVERRIDE = "openai/gpt-oss-20b"

# Maximum extractions to include in a single prompt to stay within context limits.
# Each extraction summary is ~200-400 tokens, so 300 extractions ≈ 90k tokens.
MAX_EXTRACTIONS_PER_PROMPT = 300

# Valid dependency types for validation
_VALID_DEP_TYPES = {t.value for t in DependencyType}


def _summarize_extraction(ext: Extraction) -> dict[str, Any]:
    """Create a compact summary of an extraction for the dependency prompt."""
    payload = ext.payload or {}
    summary: dict[str, Any] = {
        "id": ext.id,
        "type": ext.extraction_type.value if isinstance(ext.extraction_type, ExtractionType) else str(ext.extraction_type),
        "section": None,
    }

    # Get section path from the source record
    if ext.source_record:
        summary["section"] = ext.source_record.section_path

    # Type-specific summary fields
    ext_type = summary["type"]

    if ext_type == "obligation":
        summary["subject"] = payload.get("subject", "")
        summary["action"] = payload.get("action", "")
        summary["modality"] = payload.get("modality", "")
        summary["condition"] = payload.get("condition", "")
        enf = payload.get("enforcement") or {}
        if enf.get("enforcing_body") or enf.get("penalty_type"):
            summary["enforcement"] = {
                k: v for k, v in enf.items() if v
            }

    elif ext_type == "definition":
        summary["term"] = payload.get("term", "")
        summary["definition_text"] = (payload.get("definition_text", "") or "")[:200]

    elif ext_type == "threshold":
        summary["threshold_type"] = payload.get("threshold_type", "")
        summary["threshold_condition"] = payload.get("threshold_condition", "")
        summary["threshold_value"] = payload.get("threshold_value", "")

    elif ext_type == "exception":
        summary["exception_type"] = payload.get("exception_type", "")
        summary["description"] = (payload.get("description", "") or "")[:200]

    elif ext_type == "enforcement":
        enf = payload.get("enforcement") or payload
        summary["enforcing_body"] = enf.get("enforcing_body", "")
        summary["penalty_type"] = enf.get("penalty_type", "")

    elif ext_type == "timeline":
        tl = payload.get("timeline") or payload
        summary["effective_date"] = tl.get("effective_date", "")
        summary["compliance_deadline"] = tl.get("compliance_deadline", "")

    elif ext_type == "actor_mapping":
        summary["actors"] = payload.get("actors", [])[:5]

    elif ext_type == "rights_protection":
        summary["right_type"] = payload.get("right_type", "")
        summary["right_holder"] = payload.get("right_holder", "")
        summary["duty_bearer"] = payload.get("duty_bearer", "")

    elif ext_type == "compliance_mechanism":
        summary["mechanism_type"] = payload.get("mechanism_type", "")
        summary["responsible_party"] = payload.get("responsible_party", "")

    elif ext_type == "ambiguity":
        summary["ambiguous_text"] = (payload.get("ambiguous_text", "") or "")[:150]

    return summary


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences wrapping JSON output."""
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
        text = text.rsplit("```", 1)[0].strip()
    return text


def load_extractions_for_document(
    db: Session, document_version_id: int
) -> list[Extraction]:
    """Load all extractions for a document version, ordered by section."""
    return list(
        db.scalars(
            select(Extraction)
            .join(NormalizedSourceRecord)
            .where(NormalizedSourceRecord.document_version_id == document_version_id)
            .order_by(NormalizedSourceRecord.ordinal)
        ).all()
    )


def build_dependency_graph(
    db: Session,
    document_version_id: int,
    on_progress: callable | None = None,
) -> dict[str, Any]:
    """Build the dependency graph for a single document version.

    Loads all extractions, sends them to GPT for relationship identification,
    and writes edges to the obligation_dependencies table.

    Args:
        db: SQLAlchemy session
        document_version_id: The document version to process
        on_progress: Optional callback for status messages

    Returns:
        Summary dict with edge counts and any errors.
    """
    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    # Load all extractions for this document
    extractions = load_extractions_for_document(db, document_version_id)

    if not extractions:
        _log(f"No extractions found for document_version_id={document_version_id}")
        return {"document_version_id": document_version_id, "edges_created": 0}

    # Get document label
    dv = db.get(DocumentVersion, document_version_id)
    label = "unknown"
    if dv and dv.family:
        src = dv.family.source
        label = f"{src.jurisdiction_code} - {dv.family.short_cite}" if src else dv.family.canonical_title

    _log(f"[{label}] Building dependency graph: {len(extractions)} extractions")

    # Build extraction summaries
    summaries = [_summarize_extraction(ext) for ext in extractions]

    # Build extraction ID lookup for validation
    valid_ids = {ext.id for ext in extractions}

    # If too many extractions, process in chunks by splitting at natural boundaries
    if len(summaries) > MAX_EXTRACTIONS_PER_PROMPT:
        _log(f"  Large document: splitting {len(summaries)} extractions into chunks")
        chunks = _chunk_summaries(summaries, MAX_EXTRACTIONS_PER_PROMPT)
    else:
        chunks = [summaries]

    total_edges = 0
    total_errors = 0

    for chunk_idx, chunk in enumerate(chunks):
        if len(chunks) > 1:
            _log(f"  Processing chunk {chunk_idx + 1}/{len(chunks)} ({len(chunk)} extractions)")

        try:
            edges = _identify_dependencies(chunk, label)
            written = _write_edges(db, edges, valid_ids)
            total_edges += written
            _log(f"  Chunk {chunk_idx + 1}: {written} edges written")
        except Exception as e:
            total_errors += 1
            logger.error(
                "dependency_graph_chunk_failed",
                document_version_id=document_version_id,
                chunk=chunk_idx,
                error=str(e),
            )
            _log(f"  Chunk {chunk_idx + 1} failed: {e}")

    db.commit()

    _log(f"  Done: {total_edges} dependency edges created ({total_errors} errors)")

    return {
        "document_version_id": document_version_id,
        "document_label": label,
        "extraction_count": len(extractions),
        "edges_created": total_edges,
        "errors": total_errors,
    }


def _chunk_summaries(
    summaries: list[dict], max_size: int
) -> list[list[dict]]:
    """Split summaries into chunks, preserving section groupings."""
    chunks = []
    for i in range(0, len(summaries), max_size):
        chunks.append(summaries[i:i + max_size])
    return chunks


def _identify_dependencies(
    summaries: list[dict], document_label: str
) -> list[dict[str, Any]]:
    """Call GPT to identify dependencies between extractions.

    Returns a list of edge dicts: {parent_id, child_id, dependency_type, reason}
    """
    provider = get_extraction_provider()

    # Load prompt template
    template = load_prompt_template("dependency_graph")

    if template and "system_prompt" in template:
        system_prompt = template["system_prompt"].strip()
    else:
        system_prompt = _get_system_prompt()

    system_prompt += (
        "\n\nReturn only raw JSON with no markdown formatting, "
        "no code fences, and no preamble."
    )

    # Build user prompt
    extractions_json = json.dumps(summaries, indent=1, default=str)

    if template and "extraction_prompt" in template:
        user_prompt = render_prompt(template["extraction_prompt"], {
            "document_label": document_label,
            "extraction_count": len(summaries),
            "extractions_json": extractions_json,
        })
    else:
        user_prompt = _get_user_prompt(document_label, len(summaries), extractions_json)

    response = provider.call(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=settings.extraction_max_tokens,
        temperature=0.0,
        model_override=MODEL_OVERRIDE,
    )

    logger.info(
        "dependency_graph_llm_response",
        document=document_label,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        model=response.model_id,
    )

    # Parse response
    cleaned = _strip_code_fences(response.text)
    parsed = json.loads(cleaned)

    edges = parsed.get("dependencies", [])
    if not isinstance(edges, list):
        edges = []

    return edges


def _write_edges(
    db: Session,
    edges: list[dict[str, Any]],
    valid_ids: set[int],
) -> int:
    """Validate and write dependency edges to the database.

    Uses INSERT ... ON CONFLICT DO NOTHING to handle duplicates gracefully.
    Returns the number of edges successfully written.
    """
    written = 0

    for edge in edges:
        parent_id = edge.get("parent_id")
        child_id = edge.get("child_id")
        dep_type = edge.get("dependency_type")
        reason = edge.get("reason", "")

        # Validate
        if not parent_id or not child_id or not dep_type:
            logger.debug("dependency_edge_missing_fields", edge=edge)
            continue

        if parent_id == child_id:
            logger.debug("dependency_edge_self_reference", edge=edge)
            continue

        if parent_id not in valid_ids or child_id not in valid_ids:
            logger.debug(
                "dependency_edge_invalid_id",
                parent_id=parent_id,
                child_id=child_id,
            )
            continue

        if dep_type not in _VALID_DEP_TYPES:
            logger.debug("dependency_edge_invalid_type", dep_type=dep_type)
            continue

        try:
            stmt = pg_insert(ObligationDependency).values(
                parent_extraction_id=parent_id,
                child_extraction_id=child_id,
                dependency_type=DependencyType(dep_type),
                metadata_={"reason": reason} if reason else {},
            ).on_conflict_do_nothing(
                constraint="uq_obligation_dep"
            )
            db.execute(stmt)
            written += 1
        except Exception as e:
            logger.warning(
                "dependency_edge_write_failed",
                parent_id=parent_id,
                child_id=child_id,
                error=str(e),
            )

    return written


def _get_system_prompt() -> str:
    """Inline fallback system prompt for dependency identification."""
    return """You are a legal analysis agent that identifies relationships between extracted provisions in AI legislation.

Given a list of extractions from a single legislative document, your task is to identify DEPENDENCY RELATIONSHIPS between them. Each extraction has an ID, type, and key fields.

DEPENDENCY TYPES:
- requires_definition: An obligation, threshold, or right references a defined term. The parent is the provision that uses the term; the child is the definition.
- modifies: One provision modifies, amends, or qualifies another. The parent is the modifying provision; the child is the provision being modified.
- excepts: An exception carves out from an obligation or threshold. The parent is the general rule; the child is the exception.
- enforces: An enforcement mechanism (penalty, enforcing body) backs a specific obligation. The parent is the obligation; the child is the enforcement provision.
- references: A general cross-reference between provisions (e.g., "as described in Section X"). The parent is the referencing provision; the child is the referenced one.
- supersedes: One provision replaces or overrides another. The parent is the new provision; the child is the superseded one.

OUTPUT FORMAT:
Return a JSON object with a "dependencies" array. Each element:
{
  "parent_id": <int>,
  "child_id": <int>,
  "dependency_type": "<one of the types above>",
  "reason": "<brief explanation of why this relationship exists>"
}

RULES:
- Only identify relationships that are clearly supported by the extraction content
- Do NOT hallucinate relationships — if unsure, omit the edge
- A definition is "required" by an obligation only if the obligation's subject, action, object, or condition uses the defined term
- An exception "excepts" from an obligation only if the exception explicitly carves out from that obligation's scope
- Enforcement "enforces" an obligation only if the enforcement mechanism specifically backs that obligation
- Look for section cross-references in conditions and descriptions
- Prefer precision over recall — fewer correct edges are better than many questionable ones
- Do not create circular dependencies (A→B→A)"""


def _get_user_prompt(
    document_label: str, extraction_count: int, extractions_json: str
) -> str:
    """Inline fallback user prompt."""
    return f"""Analyze the following {extraction_count} extractions from "{document_label}" and identify all dependency relationships between them.

Focus on:
1. Obligations that reference defined terms (requires_definition)
2. Exceptions that carve out from specific obligations (excepts)
3. Enforcement mechanisms that back specific obligations (enforces)
4. Provisions that modify or qualify other provisions (modifies)
5. Cross-references between sections (references)

EXTRACTIONS:
{extractions_json}

Return the dependencies as a JSON object with a "dependencies" array."""
