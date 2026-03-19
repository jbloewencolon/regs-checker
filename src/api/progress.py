"""Pipeline progress tracking — provides real-time completion %
and ETA calculations for the dashboard UI.

Computes progress across the 7-step pipeline by counting items
at each stage and deriving what percentage of total work is done.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    DocumentVersion,
    Extraction,
    ExtractionJob,
    IngestionJob,
    IngestionStatus,
    NormalizedSourceRecord,
    ReviewQueueItem,
    ReviewStatus,
)


@dataclass
class StepProgress:
    """Progress for a single pipeline step."""

    step: int
    name: str
    total: int
    completed: int
    failed: int = 0
    in_progress: int = 0

    @property
    def pending(self) -> int:
        return max(0, self.total - self.completed - self.failed - self.in_progress)

    @property
    def percent(self) -> float:
        if self.total == 0:
            return 100.0
        return round((self.completed / self.total) * 100, 1)

    @property
    def is_complete(self) -> bool:
        return self.total > 0 and self.completed >= self.total


@dataclass
class PipelineProgress:
    """Overall pipeline progress with per-step breakdown."""

    steps: list[StepProgress]
    overall_percent: float
    estimated_completion: datetime | None
    estimated_remaining_seconds: int | None
    items_per_minute: float | None
    total_items: int
    completed_items: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_percent": self.overall_percent,
            "estimated_completion": (
                self.estimated_completion.isoformat()
                if self.estimated_completion
                else None
            ),
            "estimated_remaining_seconds": self.estimated_remaining_seconds,
            "items_per_minute": self.items_per_minute,
            "total_items": self.total_items,
            "completed_items": self.completed_items,
            "steps": [
                {
                    "step": s.step,
                    "name": s.name,
                    "total": s.total,
                    "completed": s.completed,
                    "failed": s.failed,
                    "in_progress": s.in_progress,
                    "pending": s.pending,
                    "percent": s.percent,
                    "is_complete": s.is_complete,
                }
                for s in self.steps
            ],
        }


# ---------------------------------------------------------------------------
# Average processing rates (seconds per item) — initial estimates.
# These get refined as actual data comes in via extraction_jobs.
# ---------------------------------------------------------------------------

DEFAULT_RATES = {
    "fetch": 15.0,       # seconds per document fetch+parse
    "extraction": 8.0,   # seconds per passage extraction
    "review": 30.0,      # seconds per review item (manual estimate)
}


def compute_pipeline_progress(db: Session) -> PipelineProgress:
    """Compute current pipeline progress across all steps."""

    # Step 1: Discovery — count document families vs ingestion jobs
    total_families = db.scalar(
        select(func.count()).select_from(DocumentVersion)
    ) or 0
    ingestion_completed = db.scalar(
        select(func.count()).where(IngestionJob.status == IngestionStatus.completed)
    ) or 0
    ingestion_pending = db.scalar(
        select(func.count()).where(IngestionJob.status == IngestionStatus.pending)
    ) or 0
    ingestion_failed = db.scalar(
        select(func.count()).where(IngestionJob.status == IngestionStatus.failed)
    ) or 0
    ingestion_in_progress = db.scalar(
        select(func.count()).where(
            IngestionJob.status.in_([
                IngestionStatus.fetching,
                IngestionStatus.parsing,
                IngestionStatus.normalizing,
            ])
        )
    ) or 0
    total_ingestion = ingestion_completed + ingestion_pending + ingestion_failed + ingestion_in_progress

    step1 = StepProgress(
        step=1,
        name="Discovery",
        total=total_ingestion,
        completed=ingestion_completed,
        failed=ingestion_failed,
        in_progress=ingestion_in_progress,
    )

    # Step 2: Fetch & Parse — ingestion jobs that have produced passages
    step2 = StepProgress(
        step=2,
        name="Fetch & Parse",
        total=total_ingestion,
        completed=ingestion_completed,
        failed=ingestion_failed,
        in_progress=ingestion_in_progress,
    )

    # Step 3+4+5: Extraction — passages that have been extracted
    total_passages = db.scalar(
        select(func.count()).select_from(NormalizedSourceRecord)
    ) or 0
    extracted_passage_ids = select(Extraction.source_record_id).distinct()
    extracted_passages = db.scalar(
        select(func.count()).select_from(extracted_passage_ids.subquery())
    ) or 0

    step345 = StepProgress(
        step=4,
        name="Extraction",
        total=total_passages,
        completed=extracted_passages,
    )

    # Step 6: Review — items reviewed vs total
    total_review = db.scalar(
        select(func.count()).select_from(ReviewQueueItem)
    ) or 0
    reviewed = db.scalar(
        select(func.count()).where(
            ReviewQueueItem.status.in_([ReviewStatus.approved, ReviewStatus.rejected])
        )
    ) or 0
    pending_review = db.scalar(
        select(func.count()).where(ReviewQueueItem.status == ReviewStatus.pending)
    ) or 0

    step6 = StepProgress(
        step=6,
        name="Review",
        total=total_review,
        completed=reviewed,
        in_progress=0,
    )

    # Step 7: Sync — approved items that could be synced
    approved = db.scalar(
        select(func.count()).where(Extraction.review_status == ReviewStatus.approved)
    ) or 0

    step7 = StepProgress(
        step=7,
        name="Sync",
        total=approved,
        completed=0,  # We don't track synced count in DB yet
    )

    steps = [step1, step2, step345, step6, step7]

    # Overall progress: weighted by importance
    # Discovery(5%) + Fetch(10%) + Extraction(50%) + Review(30%) + Sync(5%)
    weights = [0.05, 0.10, 0.50, 0.30, 0.05]
    weighted_percent = sum(
        s.percent * w for s, w in zip(steps, weights)
    )
    overall_percent = round(weighted_percent, 1)

    # ETA calculation based on extraction rate (the bottleneck)
    estimated_completion = None
    estimated_remaining_seconds = None
    items_per_minute = None

    remaining_passages = total_passages - extracted_passages
    if remaining_passages > 0:
        # Check actual extraction rate from recent jobs
        rate = _get_extraction_rate(db)
        if rate and rate > 0:
            items_per_minute = round(rate, 2)
            remaining_seconds = int((remaining_passages / rate) * 60)
            estimated_remaining_seconds = remaining_seconds
            estimated_completion = datetime.now() + timedelta(seconds=remaining_seconds)

    total_items = total_passages + total_review
    completed_items = extracted_passages + reviewed

    return PipelineProgress(
        steps=steps,
        overall_percent=overall_percent,
        estimated_completion=estimated_completion,
        estimated_remaining_seconds=estimated_remaining_seconds,
        items_per_minute=items_per_minute,
        total_items=total_items,
        completed_items=completed_items,
    )


def _get_extraction_rate(db: Session) -> float | None:
    """Compute items/minute from recent extraction jobs."""
    # Look at completed extraction jobs to estimate rate
    recent_jobs = db.execute(
        select(
            ExtractionJob.records_processed,
            ExtractionJob.started_at,
            ExtractionJob.completed_at,
        )
        .where(
            ExtractionJob.status == "completed",
            ExtractionJob.started_at.isnot(None),
            ExtractionJob.completed_at.isnot(None),
            ExtractionJob.records_processed > 0,
        )
        .order_by(ExtractionJob.completed_at.desc())
        .limit(10)
    ).all()

    if not recent_jobs:
        return None

    total_items = 0
    total_seconds = 0
    for job in recent_jobs:
        duration = (job.completed_at - job.started_at).total_seconds()
        if duration > 0:
            total_items += job.records_processed
            total_seconds += duration

    if total_seconds == 0:
        return None

    return (total_items / total_seconds) * 60  # items per minute


def get_confidence_distribution(db: Session) -> dict[str, Any]:
    """Get confidence score distribution for analytics."""
    from src.db.models import ConfidenceTier

    distribution = {"A": 0, "B": 0, "C": 0, "D": 0}
    rows = db.execute(
        select(Extraction.confidence_tier, func.count())
        .group_by(Extraction.confidence_tier)
    ).all()
    for tier, count in rows:
        tier_val = tier.value if hasattr(tier, "value") else str(tier)
        distribution[tier_val] = count

    # Score histogram (buckets of 0.1)
    histogram = []
    for bucket_start in [i / 10 for i in range(10)]:
        bucket_end = bucket_start + 0.1
        count = db.scalar(
            select(func.count()).where(
                Extraction.confidence_score >= bucket_start,
                Extraction.confidence_score < bucket_end,
            )
        ) or 0
        histogram.append({
            "range": f"{bucket_start:.1f}-{bucket_end:.1f}",
            "count": count,
        })

    return {
        "tier_distribution": distribution,
        "score_histogram": histogram,
        "total_extractions": sum(distribution.values()),
    }


def get_extraction_by_type(db: Session) -> dict[str, int]:
    """Count extractions grouped by type."""
    rows = db.execute(
        select(Extraction.extraction_type, func.count())
        .group_by(Extraction.extraction_type)
    ).all()
    return {
        (t.value if hasattr(t, "value") else str(t)): c
        for t, c in rows
    }


def get_model_comparison(db: Session) -> list[dict[str, Any]]:
    """Compare extraction quality across models."""
    rows = db.execute(
        select(
            Extraction.model_id,
            func.count().label("count"),
            func.avg(Extraction.confidence_score).label("avg_confidence"),
        )
        .where(Extraction.model_id.isnot(None))
        .group_by(Extraction.model_id)
    ).all()

    results = []
    for model_id, count, avg_conf in rows:
        # Get tier distribution per model
        tier_rows = db.execute(
            select(Extraction.confidence_tier, func.count())
            .where(Extraction.model_id == model_id)
            .group_by(Extraction.confidence_tier)
        ).all()
        tiers = {
            (t.value if hasattr(t, "value") else str(t)): c
            for t, c in tier_rows
        }

        results.append({
            "model_id": model_id or "unknown",
            "count": count,
            "avg_confidence": round(float(avg_conf or 0), 4),
            "tiers": tiers,
        })

    return results


def get_jurisdiction_summary(db: Session) -> list[dict[str, Any]]:
    """Get extraction counts by jurisdiction."""
    from src.db.models import DocumentFamily, Source

    rows = db.execute(
        select(
            Source.jurisdiction_code,
            func.count(Extraction.id).label("extraction_count"),
            func.avg(Extraction.confidence_score).label("avg_confidence"),
        )
        .join(NormalizedSourceRecord, Extraction.source_record_id == NormalizedSourceRecord.id)
        .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
        .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
        .join(Source, DocumentFamily.source_id == Source.id)
        .group_by(Source.jurisdiction_code)
        .order_by(func.count(Extraction.id).desc())
    ).all()

    return [
        {
            "jurisdiction": jur,
            "extraction_count": count,
            "avg_confidence": round(float(avg or 0), 3),
        }
        for jur, count, avg in rows
    ]
