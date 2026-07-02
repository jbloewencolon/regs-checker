"""Re-run a single clause-level extraction agent — Phase D of the per-agent refactor.

Reuses extract_single_record() from the main pipeline so re-run behavior
(signal routing, dedup, confidence scoring, evidence-span verification) is
identical to a full extraction run — only the set of agents passed in is
scoped to one. This makes per-agent accuracy comparisons apples-to-apples
and lets a prompt be iterated on for a single agent (or a single law) without
touching the other five clause-level agents.

Assumes the main pipeline has already run at least once (triage + extraction
enum/table bootstrap already done). Refuses to run if an ExtractionRun row is
currently marked 'running' unless --force is passed, since a concurrent full
run and a scoped purge both writing to Extraction/ExtractionAttempt could race.

Usage:
    # Idempotent fill-in: only processes passages this agent hasn't succeeded on yet.
    python -m src.scripts.rerun_agent --agent obligation

    # Scope to a single law (by canonical_key, e.g. US-TX-HB149 or TMP-TX-AITEXASRESPONS):
    python -m src.scripts.rerun_agent --agent obligation --law US-TX-HB149

    # Wipe this agent's prior output (scoped delete) and regenerate:
    python -m src.scripts.rerun_agent --agent obligation --repurge

    # Preview what --repurge would delete without writing anything:
    python -m src.scripts.rerun_agent --agent obligation --repurge --dry-run

    # Cap passages processed (smoke test before a full re-run):
    python -m src.scripts.rerun_agent --agent obligation --limit 20
"""

from __future__ import annotations

import argparse
import sys

import structlog
from sqlalchemy import create_engine, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session

from src.core.circuit_breaker import CircuitBreakerTripped, FailureTracker
from src.core.config import settings
from src.db.models import (
    ApplicabilityCondition,
    DocumentFamily,
    DocumentVersion,
    Extraction,
    ExtractionAttempt,
    ExtractionRun,
    FailedExtractionAttempt,
    IngestionJob,
    NormalizedSourceRecord,
    ObligationDependency,
    ReviewAction,
    ReviewQueueItem,
    SectionTriageResult,
    TriageDecision,
)
from src.ingestion.extractor import _get_agents, _wrap_passages, extract_single_record

logger = structlog.get_logger()

_CLAUSE_LEVEL_AGENTS = {
    "obligation",
    "definition_actor",
    "threshold_exception",
    "rights_protection",
    "compliance_mechanism",
    "preemption",
}

_BILL_LEVEL_AGENTS = {
    "enforcement_agent",
    "applicability_agent",
    "compliance_timeline_agent",
}


def _resolve_record_ids(db: Session, law: str | None) -> set[int]:
    """Triage-relevant NormalizedSourceRecord ids, optionally scoped to one law."""
    triaged_ids = select(SectionTriageResult.source_record_id).where(
        SectionTriageResult.decision.in_([TriageDecision.relevant, TriageDecision.uncertain])
    )
    query = select(NormalizedSourceRecord.id).where(NormalizedSourceRecord.id.in_(triaged_ids))
    if law:
        query = (
            select(NormalizedSourceRecord.id)
            .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
            .join(DocumentFamily, DocumentVersion.family_id == DocumentFamily.id)
            .where(NormalizedSourceRecord.id.in_(triaged_ids))
            .where(DocumentFamily.canonical_key == law)
        )
    return set(db.scalars(query).all())


def scoped_purge_agent(
    db: Session, agent_name: str, law: str | None = None, dry_run: bool = False
) -> dict[str, int]:
    """Delete one agent's prior Extraction rows (+ FK dependents), scoped
    optionally to a single law, and clear its ExtractionAttempt dedup state
    for the same scope so the next run doesn't silently skip the purged
    passages.

    Deletes in FK order: ReviewAction -> ApplicabilityCondition ->
    ObligationDependency -> ReviewQueueItem -> Extraction, then
    ExtractionAttempt + FailedExtractionAttempt for the same agent/scope.
    Other agents' rows are untouched — this never wipes global state.

    Returns a counts dict. When dry_run=True, counts are computed but nothing
    is deleted.
    """
    record_ids = _resolve_record_ids(db, law) if law else None

    ext_query = select(Extraction.id).where(Extraction.agent_name == agent_name)
    if record_ids is not None:
        ext_query = ext_query.where(Extraction.source_record_id.in_(record_ids))
    target_ext_ids = list(db.scalars(ext_query).all())

    counts = {
        "extractions": len(target_ext_ids),
        "review_queue_items": 0,
        "review_actions": 0,
        "obligation_dependencies": 0,
        "applicability_conditions": 0,
        "extraction_attempts": 0,
        "failed_extraction_attempts": 0,
    }

    attempt_count_q = select(func.count()).select_from(ExtractionAttempt).where(
        ExtractionAttempt.agent_name == agent_name
    )
    failed_count_q = select(func.count()).select_from(FailedExtractionAttempt).where(
        FailedExtractionAttempt.agent_name == agent_name
    )
    if record_ids is not None:
        attempt_count_q = attempt_count_q.where(
            ExtractionAttempt.source_record_id.in_(record_ids)
        )
        failed_count_q = failed_count_q.where(
            FailedExtractionAttempt.source_record_id.in_(record_ids)
        )
    counts["extraction_attempts"] = db.scalar(attempt_count_q) or 0
    counts["failed_extraction_attempts"] = db.scalar(failed_count_q) or 0

    qi_ids: list[int] = []
    if target_ext_ids:
        qi_ids = list(db.scalars(
            select(ReviewQueueItem.id).where(ReviewQueueItem.extraction_id.in_(target_ext_ids))
        ).all())
        counts["review_queue_items"] = len(qi_ids)
        if qi_ids:
            counts["review_actions"] = db.scalar(
                select(func.count()).select_from(ReviewAction)
                .where(ReviewAction.queue_item_id.in_(qi_ids))
            ) or 0
        counts["obligation_dependencies"] = db.scalar(
            select(func.count()).select_from(ObligationDependency).where(
                ObligationDependency.parent_extraction_id.in_(target_ext_ids)
                | ObligationDependency.child_extraction_id.in_(target_ext_ids)
            )
        ) or 0
        counts["applicability_conditions"] = db.scalar(
            select(func.count()).select_from(ApplicabilityCondition)
            .where(ApplicabilityCondition.extraction_id.in_(target_ext_ids))
        ) or 0

    if dry_run:
        return counts

    if qi_ids:
        db.execute(sa_delete(ReviewAction).where(ReviewAction.queue_item_id.in_(qi_ids)))
    if target_ext_ids:
        db.execute(sa_delete(ApplicabilityCondition).where(
            ApplicabilityCondition.extraction_id.in_(target_ext_ids)
        ))
        db.execute(sa_delete(ObligationDependency).where(
            ObligationDependency.parent_extraction_id.in_(target_ext_ids)
            | ObligationDependency.child_extraction_id.in_(target_ext_ids)
        ))
        db.execute(sa_delete(ReviewQueueItem).where(
            ReviewQueueItem.extraction_id.in_(target_ext_ids)
        ))
        db.execute(sa_delete(Extraction).where(Extraction.id.in_(target_ext_ids)))

    attempt_delete = sa_delete(ExtractionAttempt).where(
        ExtractionAttempt.agent_name == agent_name
    )
    failed_delete = sa_delete(FailedExtractionAttempt).where(
        FailedExtractionAttempt.agent_name == agent_name
    )
    if record_ids is not None:
        attempt_delete = attempt_delete.where(
            ExtractionAttempt.source_record_id.in_(record_ids)
        )
        failed_delete = failed_delete.where(
            FailedExtractionAttempt.source_record_id.in_(record_ids)
        )
    db.execute(attempt_delete)
    db.execute(failed_delete)

    db.commit()
    return counts


def rerun_agent(
    db: Session,
    agent_name: str,
    law: str | None = None,
    limit: int | None = None,
    on_progress=print,
) -> dict:
    """Run one agent across triage-relevant passages (optionally scoped to a
    law), reusing extract_single_record for full parity with the main
    pipeline's routing/dedup/scoring behavior.
    """
    from src.core.bill_context import get_or_build_bill_context

    all_agents = _get_agents()
    if agent_name not in all_agents:
        raise ValueError(f"Unknown agent {agent_name!r}. Known: {sorted(all_agents)}")
    agent_subset = {agent_name: all_agents[agent_name]}

    record_ids = _resolve_record_ids(db, law)
    summary: dict = {
        "agent": agent_name,
        "law": law,
        "total_records": 0,
        "total_extractions": 0,
        "records_processed": 0,
        "records_failed": 0,
    }
    if not record_ids:
        on_progress(f"No triage-relevant passages found for scope (law={law!r}).")
        return summary

    query = select(NormalizedSourceRecord).where(NormalizedSourceRecord.id.in_(record_ids))
    if limit:
        query = query.limit(limit)
    records = db.scalars(query).all()
    summary["total_records"] = len(records)

    # Dedup map scoped to this agent only — pulling in the other five agents'
    # attempt rows (as the full pipeline preload does) would waste memory and
    # is irrelevant since `agents` here is a single-entry dict anyway.
    succeeded_attempts: dict[tuple[int, str], set[str]] = {}
    attempt_rows = db.execute(
        select(ExtractionAttempt.source_record_id, ExtractionAttempt.input_text_hash)
        .where(ExtractionAttempt.agent_name == agent_name)
        .where(ExtractionAttempt.status == "succeeded")
        .where(ExtractionAttempt.input_text_hash.isnot(None))
        .distinct()
    ).all()
    for src_id, text_hash in attempt_rows:
        succeeded_attempts.setdefault((src_id, agent_name), set()).add(text_hash)

    dv_records: dict[int, list[NormalizedSourceRecord]] = {}
    for record in records:
        dv_records.setdefault(record.document_version_id, []).append(record)

    tracker = FailureTracker(
        context=f"rerun_agent({agent_name})",
        max_consecutive=8,
        max_failure_rate=0.8,
        min_items_for_rate=20,
    )

    for dv_id, dv_group in dv_records.items():
        merged_passages = _wrap_passages(dv_group)

        ingestion_job = db.scalars(
            select(IngestionJob).where(IngestionJob.document_version_id == dv_id)
        ).first()
        parse_quality = ingestion_job.parse_quality_score if ingestion_job else None

        bill_ctx = get_or_build_bill_context(db, dv_id, records=dv_group)

        first_rec = dv_group[0]
        dv = first_rec.document_version
        label = "unknown"
        if dv and dv.family:
            label = f"{dv.family.source.jurisdiction_code} - {dv.family.short_cite}"
        on_progress(f"[{label}] {len(merged_passages)} passages ({agent_name})...")

        for i, passage in enumerate(merged_passages):
            try:
                count = extract_single_record(
                    db, passage, agent_subset,
                    extraction_job=None,
                    parse_quality=parse_quality,
                    token_usage=None,
                    succeeded_attempts=succeeded_attempts,
                    tracker=tracker,
                    bill_context=bill_ctx,
                    run_id=None,
                )
                if count and count > 0:
                    summary["total_extractions"] += count
                summary["records_processed"] += len(passage.source_records)
                if (i + 1) % 10 == 0:
                    db.commit()
            except CircuitBreakerTripped as cb:
                db.commit()
                on_progress(f"Circuit breaker tripped: {cb}")
                summary["circuit_breaker_tripped"] = True
                return summary
            except Exception as e:
                summary["records_failed"] += len(passage.source_records)
                logger.error(
                    "rerun_agent_passage_failed",
                    agent=agent_name,
                    record_id=passage.primary_record.id,
                    error=str(e),
                )
        db.commit()

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run a single clause-level extraction agent")
    parser.add_argument("--agent", required=True, help="Agent name, e.g. obligation")
    parser.add_argument("--law", default=None, help="Restrict to one law's canonical_key")
    parser.add_argument(
        "--repurge", action="store_true",
        help="Delete this agent's prior output for the scope before re-running",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="With --repurge: preview delete counts only. Alone: preview passage count only.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap passages processed")
    parser.add_argument(
        "--force", action="store_true",
        help="Proceed even if an extraction_runs row is currently marked 'running'",
    )
    parser.add_argument("--db-url", default="", help="Override database URL")
    args = parser.parse_args()

    if args.agent in _BILL_LEVEL_AGENTS:
        print(
            f"'{args.agent}' is a bill-level agent — it already upserts on "
            f"(document_version_id, agent_name) and re-runs in place via the "
            f"normal Extract pipeline step. This script only handles the six "
            f"clause-level agents: {sorted(_CLAUSE_LEVEL_AGENTS)}"
        )
        sys.exit(1)

    if args.agent not in _CLAUSE_LEVEL_AGENTS:
        print(
            f"Unknown agent {args.agent!r}. "
            f"Known clause-level agents: {sorted(_CLAUSE_LEVEL_AGENTS)}"
        )
        sys.exit(1)

    engine = create_engine(args.db_url or settings.database_url)
    with Session(engine) as db:
        running = db.scalar(
            select(func.count()).select_from(ExtractionRun).where(ExtractionRun.status == "running")
        ) or 0
        if running and not args.force:
            print(
                f"Refusing to run: {running} extraction_runs row(s) marked 'running'. "
                f"A full pipeline run may be in progress — concurrent writes could race. "
                f"Pass --force to override (e.g. if this is a stale row from a crashed run)."
            )
            sys.exit(1)

        if args.repurge:
            dry_prefix = "[DRY RUN] " if args.dry_run else ""
            print(f"{dry_prefix}Scoped purge: agent={args.agent} law={args.law or 'ALL'}")
            counts = scoped_purge_agent(db, args.agent, law=args.law, dry_run=args.dry_run)
            for k, v in counts.items():
                print(f"  {k}: {v}")
            if args.dry_run:
                return
            print("Purge complete.\n")
        elif args.dry_run:
            record_ids = _resolve_record_ids(db, args.law)
            print(
                f"[DRY RUN] Would process up to {len(record_ids)} triage-relevant "
                f"passages (limit={args.limit or 'none'}) for agent={args.agent} "
                f"law={args.law or 'ALL'}. No --repurge — this would be an idempotent "
                f"fill-in run (already-succeeded passages are skipped)."
            )
            return

        summary = rerun_agent(db, args.agent, law=args.law, limit=args.limit, on_progress=print)
        print("\n--- Summary ---")
        for k, v in summary.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
