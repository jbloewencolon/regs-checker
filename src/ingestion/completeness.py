"""Extraction completeness manifest — split from extractor.py (RR7a).

Reports per-document extraction coverage: total passages, processed,
skipped, coverage percentage, and gaps where passages have no extractions.
Use this to certify that all passages in a law have been processed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from src.db.models import DocumentVersion, Extraction, NormalizedSourceRecord


@dataclass
class PassageCoverage:
    """Extraction coverage for a single passage."""

    record_id: int
    section_path: str | None
    text_length: int
    skipped_short: bool
    skipped_boilerplate: bool
    agents_run: list[str]
    agents_with_extractions: list[str]
    agents_abstained: list[str]
    extraction_count: int


@dataclass
class DocumentCompleteness:
    """Completeness manifest for a single document version."""

    document_version_id: int
    document_label: str
    jurisdiction: str | None
    total_passages: int
    passages_processed: int
    passages_skipped_short: int
    passages_skipped_boilerplate: int
    passages_with_extractions: int
    passages_with_no_results: int
    coverage_percent: float
    agent_coverage: dict[str, dict[str, int]]  # agent -> {run, extracted, abstained}
    gaps: list[dict[str, Any]]  # passages with incomplete coverage
    is_complete: bool


def compute_completeness_manifest(
    db,
    document_version_id: int | None = None,
) -> list[DocumentCompleteness]:
    """Compute extraction completeness for documents.

    For each document version, reports:
    - Total passages vs. processed passages
    - Which agents ran on each passage and which produced results
    - Gaps where passages were skipped or agents didn't run
    - Overall coverage percentage

    This enables audit-grade completeness assurance: you can certify
    that every passage in a law has been processed and flag laws where
    extraction coverage is below 100%.

    Args:
        db: SQLAlchemy session
        document_version_id: Compute for a single doc (None = all with passages)

    Returns:
        List of DocumentCompleteness reports.
    """
    from sqlalchemy import distinct

    # Lazy imports from extractor to avoid circular dependency
    from src.ingestion.extractor import (
        AGENT_EXTRACTION_TYPES,
        MIN_PASSAGE_LENGTH,
        _BOILERPLATE_PATTERN,
        _ENACTING_CLAUSE_PATTERN,
        _get_agents,
        _select_agents_for_passage,
    )

    agents = _get_agents()
    agent_names = sorted(agents.keys())

    # Find document versions to check
    dv_query = select(DocumentVersion.id)
    if document_version_id:
        dv_query = dv_query.where(DocumentVersion.id == document_version_id)
    else:
        dv_query = dv_query.where(
            DocumentVersion.id.in_(
                select(distinct(NormalizedSourceRecord.document_version_id))
            )
        )

    dv_ids = db.scalars(dv_query).all()
    results: list[DocumentCompleteness] = []

    for dv_id in dv_ids:
        dv = db.get(DocumentVersion, dv_id)
        if not dv:
            continue

        label = f"version {dv_id}"
        jurisdiction = None
        if dv.family:
            if dv.family.source:
                jurisdiction = dv.family.source.jurisdiction_code
            label = f"{jurisdiction or '??'} - {dv.family.short_cite or dv.family.canonical_title}"

        records = db.scalars(
            select(NormalizedSourceRecord)
            .where(NormalizedSourceRecord.document_version_id == dv_id)
            .order_by(NormalizedSourceRecord.ordinal)
        ).all()

        if not records:
            continue

        extraction_rows = db.execute(
            select(
                Extraction.source_record_id,
                Extraction.extraction_type,
                Extraction.model_id,
            ).where(
                Extraction.source_record_id.in_([r.id for r in records])
            )
        ).all()

        extractions_by_record: dict[int, set[str]] = {}
        for src_id, ext_type, _ in extraction_rows:
            ext_val = ext_type.value if hasattr(ext_type, "value") else str(ext_type)
            extractions_by_record.setdefault(src_id, set()).add(ext_val)

        total = len(records)
        processed = 0
        skipped_short = 0
        skipped_boilerplate = 0
        with_extractions = 0
        no_results = 0
        agent_stats: dict[str, dict[str, int]] = {
            name: {"run": 0, "extracted": 0, "abstained": 0}
            for name in agent_names
        }
        gaps: list[dict[str, Any]] = []

        for record in records:
            text = record.text_content
            text_len = len(text)

            if text_len < MIN_PASSAGE_LENGTH:
                skipped_short += 1
                continue

            text_stripped = text.strip()
            if _BOILERPLATE_PATTERN.fullmatch(text_stripped):
                skipped_boilerplate += 1
                continue
            if _ENACTING_CLAUSE_PATTERN.match(text_stripped) and len(text_stripped) < 300:
                skipped_boilerplate += 1
                continue

            processed += 1

            selected = _select_agents_for_passage(text, agents)
            selected_names = sorted(selected.keys())

            record_extractions = extractions_by_record.get(record.id, set())
            agents_with_results = []
            agents_abstained_list = []

            for agent_name in selected_names:
                agent_types = AGENT_EXTRACTION_TYPES[agent_name]
                type_vals = {t.value for t in agent_types}
                if record_extractions & type_vals:
                    agents_with_results.append(agent_name)
                    agent_stats[agent_name]["extracted"] += 1
                else:
                    agents_abstained_list.append(agent_name)
                    agent_stats[agent_name]["abstained"] += 1
                agent_stats[agent_name]["run"] += 1

            has_extractions = len(agents_with_results) > 0
            if has_extractions or record_extractions:
                with_extractions += 1
            else:
                no_results += 1

            if not record_extractions:
                gaps.append({
                    "record_id": record.id,
                    "section_path": record.section_path,
                    "text_preview": text[:150].replace("\n", " "),
                    "text_length": text_len,
                    "expected_agents": selected_names,
                    "reason": "no_extractions",
                })

        coverage = 0.0
        if processed > 0:
            coverage = round((with_extractions / processed) * 100, 1)

        results.append(DocumentCompleteness(
            document_version_id=dv_id,
            document_label=label,
            jurisdiction=jurisdiction,
            total_passages=total,
            passages_processed=processed,
            passages_skipped_short=skipped_short,
            passages_skipped_boilerplate=skipped_boilerplate,
            passages_with_extractions=with_extractions,
            passages_with_no_results=no_results,
            coverage_percent=coverage,
            agent_coverage=agent_stats,
            gaps=gaps,
            is_complete=(len(gaps) == 0 and processed > 0),
        ))

    return results
