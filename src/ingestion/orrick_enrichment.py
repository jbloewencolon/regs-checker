"""Orrick metadata enrichment — populate key_requirements / enforcement_penalties.

Two-phase enrichment applied before extraction runs:

Phase 1 — Backfill (no LLM):
  DocumentFamilies already seeded from CSV often have Orrick text split
  across key_requirements / enforcement_penalties with only one column
  populated per row.  This phase combines them into a single orrick_summary
  and writes it back to metadata_ so the extraction context builder can
  always find the data regardless of which column was originally used.

Phase 2 — LLM generation (IAPP-only / no Orrick text):
  Laws sourced exclusively from the IAPP tracker have no Orrick data at all.
  This phase loads the ingested law text, calls the local LLM to produce a
  brief structured summary, and stores the result so these laws are no longer
  auto-gated to Tier D by the confidence model.

Usage (via seed_pipeline):
    python -m src.scripts.seed_pipeline --mode enrich-orrick
    python -m src.scripts.seed_pipeline --mode enrich-orrick --no-llm   # phase 1 only
    python -m src.scripts.seed_pipeline --mode enrich-orrick --limit 20 # LLM phase limit
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import structlog

from src.db.models import DocumentFamily

logger = structlog.get_logger()

_LAW_TEXTS_DIR = Path("output/law_texts")

_SYSTEM_PROMPT = """\
You are a legal analysis assistant. You will receive the text of an AI-related \
law or bill. Extract two concise summaries (2-4 sentences each):

1. key_requirements: What entities are required or prohibited from doing.
2. enforcement_penalties: What penalties, remedies, or enforcement mechanisms apply.

Reply with ONLY a JSON object in this exact format (no markdown fences):
{"key_requirements": "...", "enforcement_penalties": "..."}"""

_USER_PROMPT_TEMPLATE = """\
Law: {title}
Jurisdiction: {jurisdiction}

Text:
{text}"""

# Characters of law text to include in LLM prompt (keeps prompt well within 128k context)
_MAX_TEXT_CHARS = 12000


def _load_law_text(canonical_law_id: str) -> str | None:
    """Return the ingested law text for a given canonical_law_id, or None."""
    path = _LAW_TEXTS_DIR / f"{canonical_law_id}.txt"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return text[:_MAX_TEXT_CHARS] if len(text) > _MAX_TEXT_CHARS else text


def _parse_llm_json(raw: str) -> dict[str, str] | None:
    """Extract JSON from LLM output, tolerating minor formatting issues."""
    # Strip markdown fences if present
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    # Find first {...} block
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
        if "key_requirements" in data or "enforcement_penalties" in data:
            return {
                "key_requirements": str(data.get("key_requirements", "")),
                "enforcement_penalties": str(data.get("enforcement_penalties", "")),
            }
    except json.JSONDecodeError:
        pass
    return None


def _build_orrick_summary(key_req: str, enforcement: str) -> str:
    """Combine the two columns into a single summary string."""
    return " ".join(p.strip() for p in [key_req, enforcement] if p and p.strip())


def run_orrick_enrichment(
    db,
    limit: int | None = None,
    llm_enabled: bool = True,
    on_progress: Any = None,
) -> dict[str, int]:
    """Enrich all DocumentFamily records with Orrick metadata.

    Args:
        db: SQLAlchemy session.
        limit: Max number of families to process in the LLM phase (None = all).
        llm_enabled: When False, only run Phase 1 (backfill, no LLM calls).
        on_progress: Optional callable(str) for progress messages.

    Returns:
        Stats dict with counts for each action taken.
    """
    def _log(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info("orrick_enrichment", msg=msg)

    stats: dict[str, int] = {
        "total": 0,
        "backfilled": 0,      # Phase 1: combined existing split columns
        "already_complete": 0, # Already had orrick_summary
        "llm_generated": 0,   # Phase 2: LLM produced new summaries
        "llm_skipped_no_text": 0,  # No law text file found
        "llm_failed": 0,      # LLM call failed or returned unparseable output
        "no_data_skipped": 0, # No Orrick data and no law text — left as-is
    }

    families = db.query(DocumentFamily).all()
    stats["total"] = len(families)
    _log(f"Checking {len(families)} document families for Orrick enrichment")

    llm_candidates: list[DocumentFamily] = []

    # ── Phase 1: Backfill existing split data ──────────────────────────────
    for family in families:
        meta = dict(family.metadata_ or {})
        existing_summary = (meta.get("orrick_summary") or "").strip()
        key_req = (meta.get("key_requirements") or "").strip()
        enforcement = (meta.get("enforcement_penalties") or "").strip()

        if existing_summary:
            # Already has a combined summary — check if it needs updating
            combined = _build_orrick_summary(key_req, enforcement)
            if combined and combined != existing_summary:
                # Individual columns were updated after the summary was written
                meta["orrick_summary"] = combined
                family.metadata_ = meta
                stats["backfilled"] += 1
            else:
                stats["already_complete"] += 1
            continue

        if key_req or enforcement:
            # Has split data — combine it now
            meta["orrick_summary"] = _build_orrick_summary(key_req, enforcement)
            family.metadata_ = meta
            stats["backfilled"] += 1
            _log(f"  Backfilled: {family.canonical_title[:60]}")
        else:
            # No Orrick data at all — candidate for LLM phase
            llm_candidates.append(family)

    db.flush()
    _log(f"Phase 1 complete: {stats['backfilled']} backfilled, "
         f"{stats['already_complete']} already complete, "
         f"{len(llm_candidates)} candidates for LLM phase")

    if not llm_enabled or not llm_candidates:
        db.commit()
        return stats

    # ── Phase 2: LLM generation for laws with no Orrick data ──────────────
    if limit is not None:
        llm_candidates = llm_candidates[:limit]

    from src.core.llm_provider import get_discovery_provider
    provider = get_discovery_provider()

    _log(f"Phase 2: generating Orrick summaries for {len(llm_candidates)} laws via LLM")

    for family in llm_candidates:
        meta = dict(family.metadata_ or {})
        canonical_law_id = meta.get("canonical_law_id", "")

        # Resolve jurisdiction from source relationship
        jurisdiction = ""
        try:
            jurisdiction = family.source.jurisdiction_code if family.source else ""
        except Exception:
            pass

        law_text = _load_law_text(canonical_law_id) if canonical_law_id else None
        if not law_text:
            # Try matching by title pattern if canonical_law_id lookup failed
            logger.debug("no_law_text_found", canonical_law_id=canonical_law_id)
            stats["llm_skipped_no_text"] += 1
            stats["no_data_skipped"] += 1
            continue

        user_prompt = _USER_PROMPT_TEMPLATE.format(
            title=family.canonical_title or canonical_law_id,
            jurisdiction=jurisdiction or "Unknown",
            text=law_text,
        )

        try:
            response = provider.call(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=1024,
                temperature=0.0,
                reasoning_effort="low",  # Minimal thinking — this is summarization
            )
            parsed = _parse_llm_json(response.text)
            if parsed:
                key_req = parsed["key_requirements"].strip()
                enforcement = parsed["enforcement_penalties"].strip()
                combined = _build_orrick_summary(key_req, enforcement)

                meta["key_requirements"] = key_req
                meta["enforcement_penalties"] = enforcement
                meta["orrick_summary"] = combined
                meta["orrick_source"] = "llm_generated"
                family.metadata_ = meta

                stats["llm_generated"] += 1
                _log(f"  LLM enriched: {family.canonical_title[:60]}")
                logger.info(
                    "orrick_llm_enriched",
                    canonical_law_id=canonical_law_id,
                    key_req_len=len(key_req),
                    enforcement_len=len(enforcement),
                )
            else:
                logger.warning(
                    "orrick_llm_parse_failed",
                    canonical_law_id=canonical_law_id,
                    raw=response.text[:200],
                )
                stats["llm_failed"] += 1
                stats["no_data_skipped"] += 1
        except Exception as exc:
            logger.error(
                "orrick_llm_error",
                canonical_law_id=canonical_law_id,
                error=str(exc),
            )
            stats["llm_failed"] += 1
            stats["no_data_skipped"] += 1

    db.commit()
    _log(
        f"Phase 2 complete: {stats['llm_generated']} generated, "
        f"{stats['llm_failed']} failed, {stats['llm_skipped_no_text']} no text file"
    )
    return stats
