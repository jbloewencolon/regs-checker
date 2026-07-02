"""Per-agent extraction export — Phase B of the per-agent refactor.

Writes one CSV per agent to output/exports/<YYYY-MM-DD>/ plus a combined
all_agents.csv so offline accuracy comparisons can be run across agents.

Usage:
    # Export all agents (admitted extractions only):
    python -m src.scripts.export_by_agent

    # Include needs_review as well:
    python -m src.scripts.export_by_agent --include-needs-review

    # Export specific agents only:
    python -m src.scripts.export_by_agent --agents obligation,definition_actor

    # Dry run — print row counts without writing files:
    python -m src.scripts.export_by_agent --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

import structlog
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.core.admission import ADMITTED, NEEDS_REVIEW, compute_admission_status
from src.core.config import settings
from src.db.models import DocumentVersion, Extraction, NormalizedSourceRecord

logger = structlog.get_logger()

_ALL_AGENTS = [
    "obligation",
    "definition_actor",
    "threshold_exception",
    "rights_protection",
    "compliance_mechanism",
    "preemption",
]

_FIELDNAMES = [
    "extraction_id",
    "agent_name",
    "admission_status",
    "canonical_key",
    "law_jurisdiction",
    "law_title",
    "bill_number",
    "extraction_type",
    "confidence_score",
    "confidence_tier",
    "verified_spans",
    "total_spans",
    "section_path",
    "passage_text",
    "payload_json",
    "created_at",
]

_DISCLAIMER = (
    "# DISCLAIMER: Informational only — not legal advice. "
    "AI-extracted; verify against current official statutory text."
)


def _fetch_rows(db, agent_names: list[str], include_needs_review: bool) -> list[dict]:
    query = (
        select(Extraction, NormalizedSourceRecord, DocumentVersion)
        .join(NormalizedSourceRecord, Extraction.source_record_id == NormalizedSourceRecord.id)
        .join(DocumentVersion, NormalizedSourceRecord.document_version_id == DocumentVersion.id)
        .order_by(Extraction.agent_name, Extraction.confidence_score.desc())
    )
    if agent_names:
        query = query.where(Extraction.agent_name.in_(agent_names))

    rows = []
    for ext, rec, dv in db.execute(query).all():
        status = compute_admission_status(ext.evidence_spans, ext.confidence_tier.value)
        if status == ADMITTED or (include_needs_review and status == NEEDS_REVIEW):
            doc_family = dv.family
            spans = ext.evidence_spans or []
            rows.append({
                "extraction_id": ext.id,
                "agent_name": ext.agent_name or "",
                "admission_status": status,
                "canonical_key": doc_family.canonical_key if doc_family else "",
                "law_jurisdiction": (
                    doc_family.source.jurisdiction_code
                    if doc_family and doc_family.source else ""
                ),
                "law_title": doc_family.canonical_title if doc_family else "",
                "bill_number": dv.bill_number or "",
                "extraction_type": ext.extraction_type.value,
                "confidence_score": f"{ext.confidence_score:.4f}",
                "confidence_tier": ext.confidence_tier.value,
                "verified_spans": sum(1 for s in spans if s.get("verified")),
                "total_spans": len(spans),
                "section_path": rec.section_path or "",
                "passage_text": (rec.text_content or "")[:800],
                "payload_json": json.dumps(ext.payload, default=str),
                "created_at": ext.created_at.isoformat() if ext.created_at else "",
            })
    return rows


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(_DISCLAIMER + "\n")
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export per-agent extraction CSVs")
    parser.add_argument(
        "--agents", default="",
        help="Comma-separated list of agents to export (default: all clause-level agents)",
    )
    parser.add_argument(
        "--include-needs-review", action="store_true",
        help="Also export needs_review rows (default: admitted only)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print row counts without writing files",
    )
    parser.add_argument("--db-url", default="", help="Override database URL")
    args = parser.parse_args()

    agent_names = [a.strip() for a in args.agents.split(",") if a.strip()] or _ALL_AGENTS
    out_dir = Path("output") / "exports" / str(date.today())

    engine = create_engine(args.db_url or settings.database_url)
    with Session(engine) as db:
        all_rows = _fetch_rows(db, agent_names, args.include_needs_review)

    by_agent: dict[str, list[dict]] = {}
    for row in all_rows:
        by_agent.setdefault(row["agent_name"] or "unknown", []).append(row)

    if args.dry_run:
        print(f"Dry run — would write to {out_dir}/")
        for agent, rows in sorted(by_agent.items()):
            print(f"  {agent}: {len(rows)} rows")
        print(f"  all_agents: {len(all_rows)} rows total")
        return

    for agent, rows in sorted(by_agent.items()):
        path = out_dir / f"{agent}.csv"
        _write_csv(path, rows)
        logger.info("agent_export_written", agent=agent, rows=len(rows), path=str(path))

    if all_rows:
        combined = out_dir / "all_agents.csv"
        _write_csv(combined, all_rows)
        logger.info("combined_export_written", rows=len(all_rows), path=str(combined))

    print(f"Exported {len(all_rows)} rows across {len(by_agent)} agents → {out_dir}/")


if __name__ == "__main__":
    main()
