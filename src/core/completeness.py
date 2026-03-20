"""Completeness checking and re-extraction triggers.

Two responsibilities:
  1. Flag laws where extraction coverage < 100% of passages
  2. Detect stale extractions produced by outdated models or prompts

Usage:
    python -m src.scripts.seed_pipeline --mode check-completeness
    python -m src.scripts.seed_pipeline --mode check-stale
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from src.db.models import (
    DocumentFamily,
    DocumentVersion,
    Extraction,
    ExtractionType,
    NormalizedSourceRecord,
    Source,
)

logger = structlog.get_logger()

# Minimum passage length mirroring extractor.py — passages shorter than this
# are expected to have no extractions (boilerplate/stubs).
MIN_PASSAGE_LENGTH = 150


@dataclass
class CoverageGap:
    """A document version with incomplete extraction coverage."""

    document_version_id: int
    jurisdiction: str
    short_cite: str
    canonical_title: str
    total_passages: int
    extracted_passages: int
    skipped_short: int
    unextracted_passage_ids: list[int] = field(default_factory=list)

    @property
    def coverage_pct(self) -> float:
        extractable = self.total_passages - self.skipped_short
        if extractable <= 0:
            return 100.0
        return (self.extracted_passages / extractable) * 100.0

    @property
    def is_complete(self) -> bool:
        return self.coverage_pct >= 100.0


@dataclass
class StaleExtraction:
    """An extraction produced by a model/prompt that no longer matches current config."""

    extraction_id: int
    source_record_id: int
    extraction_type: str
    model_id: str | None
    prompt_hash: str | None
    template_version: str | None
    jurisdiction: str
    short_cite: str


@dataclass
class CompletenessReport:
    """Full report of coverage gaps and stale extractions."""

    total_document_versions: int
    complete_versions: int
    incomplete_versions: int
    gaps: list[CoverageGap]
    stale_extractions: list[StaleExtraction]
    stale_model_ids: list[str]

    @property
    def overall_coverage_pct(self) -> float:
        if not self.gaps:
            return 100.0
        total_extractable = sum(g.total_passages - g.skipped_short for g in self.gaps)
        total_extracted = sum(g.extracted_passages for g in self.gaps)
        if total_extractable <= 0:
            return 100.0
        return (total_extracted / total_extractable) * 100.0


def check_coverage(db: Session) -> list[CoverageGap]:
    """Find document versions where extraction coverage < 100%.

    Counts passages per document version, compares against passages that have
    at least one extraction, and returns gaps for any version with incomplete
    coverage. Short passages (< MIN_PASSAGE_LENGTH) are excluded from the
    denominator since the extractor skips them.
    """
    # All document versions that have normalized records
    dv_ids = db.scalars(
        select(distinct(NormalizedSourceRecord.document_version_id))
    ).all()

    gaps: list[CoverageGap] = []

    for dv_id in dv_ids:
        # Total passages
        all_records = db.scalars(
            select(NormalizedSourceRecord).where(
                NormalizedSourceRecord.document_version_id == dv_id
            )
        ).all()

        if not all_records:
            continue

        total = len(all_records)
        short = sum(1 for r in all_records if len(r.text_content) < MIN_PASSAGE_LENGTH)

        # Passages with at least one extraction
        extracted_ids = set(
            db.scalars(
                select(distinct(Extraction.source_record_id)).where(
                    Extraction.source_record_id.in_(
                        select(NormalizedSourceRecord.id).where(
                            NormalizedSourceRecord.document_version_id == dv_id
                        )
                    )
                )
            ).all()
        )

        # Find unextracted passage IDs (excluding short ones)
        unextracted = [
            r.id
            for r in all_records
            if r.id not in extracted_ids and len(r.text_content) >= MIN_PASSAGE_LENGTH
        ]

        # Get document metadata
        dv = db.get(DocumentVersion, dv_id)
        family = dv.family if dv else None
        source = family.source if family else None

        gap = CoverageGap(
            document_version_id=dv_id,
            jurisdiction=source.jurisdiction_code if source else "??",
            short_cite=family.short_cite if family else "Unknown",
            canonical_title=family.canonical_title if family else "Unknown",
            total_passages=total,
            extracted_passages=len(extracted_ids),
            skipped_short=short,
            unextracted_passage_ids=unextracted,
        )

        if not gap.is_complete:
            gaps.append(gap)

    return sorted(gaps, key=lambda g: g.coverage_pct)


def check_stale_extractions(
    db: Session,
    current_model_id: str,
    current_prompt_hashes: dict[str, str] | None = None,
) -> list[StaleExtraction]:
    """Find extractions produced by outdated models or prompts.

    Args:
        db: SQLAlchemy session.
        current_model_id: The model ID currently configured for extraction.
        current_prompt_hashes: Optional dict mapping agent_name → current prompt hash.
            If provided, extractions with a different prompt_hash are flagged.

    Returns:
        List of StaleExtraction records that need re-processing.
    """
    # Find extractions with a different model_id
    query = (
        select(Extraction)
        .where(Extraction.model_id.isnot(None))
        .where(Extraction.model_id != current_model_id)
    )

    stale = []
    for extraction in db.scalars(query).all():
        record = extraction.source_record
        dv = record.document_version if record else None
        family = dv.family if dv else None
        source = family.source if family else None

        stale.append(StaleExtraction(
            extraction_id=extraction.id,
            source_record_id=extraction.source_record_id,
            extraction_type=extraction.extraction_type.value,
            model_id=extraction.model_id,
            prompt_hash=extraction.prompt_hash,
            template_version=extraction.template_version,
            jurisdiction=source.jurisdiction_code if source else "??",
            short_cite=family.short_cite if family else "Unknown",
        ))

    # Also check prompt hashes if provided
    if current_prompt_hashes:
        for agent_name, expected_hash in current_prompt_hashes.items():
            prompt_stale = db.scalars(
                select(Extraction)
                .where(Extraction.prompt_hash.isnot(None))
                .where(Extraction.prompt_hash != expected_hash)
                .where(Extraction.model_id == current_model_id)
            ).all()

            for extraction in prompt_stale:
                # Avoid duplicates
                if any(s.extraction_id == extraction.id for s in stale):
                    continue
                record = extraction.source_record
                dv = record.document_version if record else None
                family = dv.family if dv else None
                source = family.source if family else None

                stale.append(StaleExtraction(
                    extraction_id=extraction.id,
                    source_record_id=extraction.source_record_id,
                    extraction_type=extraction.extraction_type.value,
                    model_id=extraction.model_id,
                    prompt_hash=extraction.prompt_hash,
                    template_version=extraction.template_version,
                    jurisdiction=source.jurisdiction_code if source else "??",
                    short_cite=family.short_cite if family else "Unknown",
                ))

    return stale


def run_completeness_report(
    db: Session,
    current_model_id: str | None = None,
) -> CompletenessReport:
    """Generate a full completeness report including coverage gaps and stale extractions.

    Args:
        db: SQLAlchemy session.
        current_model_id: If provided, also checks for stale extractions.

    Returns:
        CompletenessReport with all findings.
    """
    gaps = check_coverage(db)

    # Count total document versions with normalized records
    total_dvs = db.scalar(
        select(func.count(distinct(NormalizedSourceRecord.document_version_id)))
    ) or 0

    stale: list[StaleExtraction] = []
    stale_models: list[str] = []

    if current_model_id:
        stale = check_stale_extractions(db, current_model_id)
        stale_models = list({s.model_id for s in stale if s.model_id})

    return CompletenessReport(
        total_document_versions=total_dvs,
        complete_versions=total_dvs - len(gaps),
        incomplete_versions=len(gaps),
        gaps=gaps,
        stale_extractions=stale,
        stale_model_ids=stale_models,
    )


def format_completeness_report(report: CompletenessReport) -> str:
    """Format a CompletenessReport as human-readable text."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("EXTRACTION COMPLETENESS REPORT")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Document versions:  {report.total_document_versions}")
    lines.append(f"  Complete:         {report.complete_versions}")
    lines.append(f"  Incomplete:       {report.incomplete_versions}")
    lines.append(f"  Overall coverage: {report.overall_coverage_pct:.1f}%")
    lines.append("")

    if report.gaps:
        lines.append("-" * 70)
        lines.append("COVERAGE GAPS (sorted by coverage %)")
        lines.append("-" * 70)
        for gap in report.gaps:
            extractable = gap.total_passages - gap.skipped_short
            missing = len(gap.unextracted_passage_ids)
            lines.append(
                f"  {gap.jurisdiction} - {gap.short_cite}: "
                f"{gap.coverage_pct:.1f}% "
                f"({gap.extracted_passages}/{extractable} passages, "
                f"{missing} missing, {gap.skipped_short} short-skipped)"
            )
    else:
        lines.append("No coverage gaps found — all extractable passages processed.")

    if report.stale_extractions:
        lines.append("")
        lines.append("-" * 70)
        lines.append(f"STALE EXTRACTIONS ({len(report.stale_extractions)} total)")
        lines.append("-" * 70)
        if report.stale_model_ids:
            lines.append(f"  Outdated models: {', '.join(report.stale_model_ids)}")

        # Group by jurisdiction for readability
        by_jurisdiction: dict[str, int] = {}
        for s in report.stale_extractions:
            key = f"{s.jurisdiction} - {s.short_cite}"
            by_jurisdiction[key] = by_jurisdiction.get(key, 0) + 1
        for key, count in sorted(by_jurisdiction.items()):
            lines.append(f"  {key}: {count} stale extractions")

        lines.append("")
        lines.append(
            "Run --mode re-extract-stale to re-process these with the current model."
        )

    lines.append("")
    return "\n".join(lines)
