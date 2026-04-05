"""Export/import for extraction results.

This module provides a SUPPLEMENTARY extraction workflow for edge cases where
the programmatic API pipeline (--mode extract) cannot be used — e.g., when
debugging specific passages or performing manual review corrections.

The API-based extraction pipeline (--mode extract) is the PRIMARY workflow.
For production use, prefer:
  - --mode extract (synchronous Anthropic/local LLM API calls)
  - --mode extract --batch (Anthropic Batch API — 50% discount, 24h turnaround)

This export/import workflow is retained for:
  - Debugging: exporting specific passages for manual inspection
  - Correction: importing manually-corrected extraction JSON
  - Offline: environments without LLM API access

Export workflow:
  1. python -m src.scripts.seed_pipeline --mode export-passages
     → Writes passages to export/batch_001.txt with schema reference
  2. Process passages through any LLM (API, local, or interactive)
  3. python -m src.scripts.seed_pipeline --mode import-extractions --input export/batch_001_results.json
     → Validates and writes extractions to the database
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.confidence import compute_confidence
from src.db.models import (
    ConfidenceTier,
    Extraction,
    ExtractionType,
    NormalizedSourceRecord,
    ReviewQueueItem,
    ReviewStatus,
)
from src.schemas.extraction import EXTRACTION_TYPE_SCHEMAS

logger = structlog.get_logger()

EXPORT_DIR = Path("export")

# Agent names → extraction types (mirrors extractor.py)
AGENT_EXTRACTION_TYPES = {
    "obligation": ExtractionType.obligation,
    "definition_actor": ExtractionType.definition,
    "threshold_exception": ExtractionType.threshold,
    # "ambiguity" removed — findings now embedded as interpretation_risks on obligation/rights payloads
}

# The system prompt included in every export file so Claude knows the task
EXTRACTION_SYSTEM_PROMPT = """\
You are a legal extraction agent. For each passage below, extract ALL of the
following that apply:

1. OBLIGATIONS: Who must comply, what they must do, modality (shall/must/may),
   conditions, timeline, enforcement mechanisms. Note any interpretation_risks
   (vague terms, undefined references, conflicting provisions) inline on the obligation.
2. DEFINITIONS: Defined terms, their text, scope, related actors, framework refs
3. THRESHOLDS & EXCEPTIONS: Numeric/categorical thresholds, carve-outs,
   safe harbors, exemptions

CRITICAL RULES:
- Every evidence_spans[].text MUST be a VERBATIM quote from the passage
- If a passage has NO extractable content for a category, omit that category
- If a passage has MULTIPLE items of the same type, include all of them
- Use "detected": false only if the passage has NO extractable content at all

Return your response as a JSON array where each element has this structure:
{
  "passage_id": <the passage ID from the input>,
  "extractions": [
    {
      "agent": "obligation" | "definition_actor" | "threshold_exception",
      "items": [ ... ]  // array of extraction objects matching the schema below
    }
  ]
}

If a passage has no extractable content:
{
  "passage_id": <id>,
  "extractions": [],
  "abstention_reason": "why no extraction was possible"
}
"""

SCHEMA_REFERENCE = """\
## Obligation schema:
{
  "subject": "who must comply",
  "subject_normalized": "developer|deployer|operator|...",
  "modality": "must|shall|may|should|prohibited",
  "action": "what they must do",
  "object": "what the action applies to (optional)",
  "condition": "trigger conditions (optional)",
  "jurisdiction": "state code (optional)",
  "section_reference": "section ref (optional)",
  "timeline": {"effective_date": "...", "compliance_deadline": "...", "sunset_date": null, "phase_in_period": null, "timeline_text": "..."},
  "enforcement": {"enforcing_body": "...", "penalty_type": "...", "penalty_description": "...", "private_right_of_action": true/false, "enforcement_text": "..."},
  "evidence_spans": [{"field_name": "...", "text": "VERBATIM quote from passage"}]
}

## Definition schema:
{
  "term": "the defined term",
  "definition_text": "the full definition",
  "scope": "scope or applicability (optional)",
  "cross_references": ["section refs"],
  "actors": [{"actor_name": "...", "actor_type": "regulator|developer|...", "responsibilities": ["..."]}],
  "framework_refs": [{"framework_name": "...", "section_or_standard": "...", "relationship": "incorporates|references|..."}],
  "evidence_spans": [{"field_name": "...", "text": "VERBATIM quote"}]
}

## Threshold/Exception schema:
{
  "threshold_type": "numeric|categorical|...",
  "threshold_value": "...",
  "threshold_unit": "...",
  "threshold_condition": "the condition expression",
  "applies_to_obligation": "which obligation this modifies",
  "exceptions": [{"exception_type": "carve-out|safe-harbor|exemption", "description": "...", "conditions": "...", "scope": "..."}],
  "evidence_spans": [{"field_name": "...", "text": "VERBATIM quote"}]
}

## Interpretation risks (embedded on obligation and rights_protection objects):
"interpretation_risks": [
  {
    "risk_type": "vague_term|undefined_reference|conflicting_provision|scope_ambiguity|temporal_ambiguity|conditional_ambiguity",
    "term": "the specific ambiguous term or phrase",
    "concern": "why this creates compliance uncertainty",
    "severity": "low|medium|high|critical",
    "evidence_spans": [{"field_name": "term", "text": "VERBATIM quote"}]
  }
]
"""


def export_passages(
    db: Session,
    limit: int | None = None,
    batch_size: int = 15,
) -> dict:
    """Export unprocessed passages as text files for Claude Code extraction.

    Creates numbered batch files in export/ directory, each containing
    up to batch_size passages with the system prompt and schema reference.

    Args:
        db: SQLAlchemy session.
        limit: Max total passages to export (default: all unprocessed).
        batch_size: Passages per export file (default: 15, fits Claude context).

    Returns:
        Summary dict with counts and file paths.
    """
    # Find passages without any extractions
    existing_extracted = (
        select(Extraction.source_record_id).distinct()
    )
    query = (
        select(NormalizedSourceRecord)
        .where(NormalizedSourceRecord.id.notin_(existing_extracted))
        .where(NormalizedSourceRecord.text_content.isnot(None))
        .order_by(
            NormalizedSourceRecord.document_version_id,
            NormalizedSourceRecord.ordinal,
        )
    )
    if limit:
        query = query.limit(limit)

    records = db.scalars(query).all()

    if not records:
        print("No unprocessed passages found.")
        return {"total_passages": 0, "batches": 0, "files": []}

    # Filter short passages (same as the API pipeline)
    MIN_LENGTH = 150
    filtered = [r for r in records if len(r.text_content.strip()) >= MIN_LENGTH]
    skipped = len(records) - len(filtered)

    EXPORT_DIR.mkdir(exist_ok=True)

    # Find next batch number
    existing = list(EXPORT_DIR.glob("batch_*.txt"))
    next_num = 1
    if existing:
        nums = []
        for f in existing:
            try:
                nums.append(int(f.stem.split("_")[1]))
            except (ValueError, IndexError):
                pass
        if nums:
            next_num = max(nums) + 1

    files = []
    batch_count = 0

    for i in range(0, len(filtered), batch_size):
        batch = filtered[i : i + batch_size]
        batch_num = next_num + batch_count
        filename = EXPORT_DIR / f"batch_{batch_num:03d}.txt"

        lines = []
        lines.append("=" * 70)
        lines.append("REGS CHECKER — EXTRACTION BATCH")
        lines.append(f"Generated: {datetime.now().isoformat()}")
        lines.append(f"Batch: {batch_num:03d} ({len(batch)} passages)")
        lines.append("=" * 70)
        lines.append("")
        lines.append("## INSTRUCTIONS")
        lines.append(EXTRACTION_SYSTEM_PROMPT)
        lines.append(SCHEMA_REFERENCE)
        lines.append("=" * 70)
        lines.append("## PASSAGES TO EXTRACT")
        lines.append("=" * 70)

        # Track record IDs for the manifest
        record_ids = []

        for record in batch:
            dv = record.document_version
            df = dv.family if dv else None
            source = df.source if df else None

            label_parts = []
            if source:
                label_parts.append(source.jurisdiction_code)
            if df:
                label_parts.append(df.short_cite or df.canonical_title[:50])
            label = " — ".join(label_parts) if label_parts else "Unknown"

            lines.append("")
            lines.append(f"--- PASSAGE ID: {record.id} ---")
            lines.append(f"Document: {label}")
            if record.section_path:
                lines.append(f"Section: {record.section_path}")
            lines.append("")
            lines.append(record.text_content)
            lines.append("")
            record_ids.append(record.id)

        lines.append("=" * 70)
        lines.append("END OF BATCH")
        lines.append("=" * 70)
        lines.append("")
        lines.append("Return your extractions as a single JSON array.")
        lines.append("Save the JSON response as:")
        lines.append(f"  export/batch_{batch_num:03d}_results.json")

        filename.write_text("\n".join(lines), encoding="utf-8")

        # Also write a manifest for the import script
        manifest = {
            "batch_num": batch_num,
            "created_at": datetime.now().isoformat(),
            "record_ids": record_ids,
            "passage_count": len(batch),
        }
        manifest_file = EXPORT_DIR / f"batch_{batch_num:03d}_manifest.json"
        manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        files.append(str(filename))
        batch_count += 1
        print(f"  Wrote {filename} ({len(batch)} passages, IDs: {record_ids[0]}..{record_ids[-1]})")

    print(f"\nExport complete:")
    print(f"  Total passages:  {len(filtered)}")
    print(f"  Short skipped:   {skipped}")
    print(f"  Batches created: {batch_count}")
    print(f"\nNext steps:")
    print(f"  1. Open each batch_*.txt file")
    print(f"  2. Paste the content into Claude Code or Claude Chat")
    print(f"  3. Save Claude's JSON response as batch_*_results.json")
    print(f"  4. Run: python -m src.scripts.seed_pipeline --mode import-extractions")

    return {
        "total_passages": len(filtered),
        "skipped_short": skipped,
        "batches": batch_count,
        "files": files,
    }


def import_extractions(
    db: Session,
    input_path: str | None = None,
) -> dict:
    """Import Claude Code extraction results from JSON files.

    Reads JSON files from export/ directory (or a specific file),
    validates each extraction against Pydantic schemas, computes
    confidence scores, and writes to the extractions table.

    Args:
        db: SQLAlchemy session.
        input_path: Specific JSON file to import (default: all *_results.json in export/).

    Returns:
        Summary dict with counts.
    """
    if input_path:
        result_files = [Path(input_path)]
    else:
        result_files = sorted(EXPORT_DIR.glob("batch_*_results.json"))

    if not result_files:
        print("No result files found. Expected export/batch_*_results.json")
        return {"files_processed": 0, "extractions_created": 0, "errors": 0}

    total_created = 0
    total_errors = 0
    total_skipped = 0
    files_processed = 0

    for result_file in result_files:
        print(f"\nProcessing {result_file}...")

        try:
            raw = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  ERROR: Invalid JSON in {result_file}: {e}")
            total_errors += 1
            continue

        # Accept both a raw array and {"results": [...]}
        if isinstance(raw, dict):
            passages = raw.get("results", raw.get("passages", [raw]))
        elif isinstance(raw, list):
            passages = raw
        else:
            print(f"  ERROR: Unexpected format in {result_file}")
            total_errors += 1
            continue

        file_created = 0
        file_errors = 0

        for passage_data in passages:
            passage_id = passage_data.get("passage_id")
            if not passage_id:
                print(f"  WARN: Missing passage_id, skipping entry")
                file_errors += 1
                continue

            # Check the record exists
            record = db.get(NormalizedSourceRecord, passage_id)
            if not record:
                print(f"  WARN: passage_id {passage_id} not found in DB, skipping")
                file_errors += 1
                continue

            # Check for abstention
            if not passage_data.get("extractions"):
                reason = passage_data.get("abstention_reason", "no extractions")
                logger.info("import_abstention", passage_id=passage_id, reason=reason)
                continue

            # Get parse quality for confidence scoring
            dv = record.document_version
            parse_quality = None
            if dv:
                for job in dv.ingestion_jobs:
                    if job.parse_quality_score is not None:
                        parse_quality = job.parse_quality_score
                        break

            for agent_block in passage_data["extractions"]:
                agent_name = agent_block.get("agent", "")
                items = agent_block.get("items", [])

                if agent_name not in AGENT_EXTRACTION_TYPES:
                    print(f"  WARN: Unknown agent '{agent_name}' for passage {passage_id}")
                    file_errors += 1
                    continue

                extraction_type = AGENT_EXTRACTION_TYPES[agent_name]
                schema_class = EXTRACTION_TYPE_SCHEMAS.get(extraction_type.value)

                for item in items:
                    try:
                        # Validate via Pydantic
                        if schema_class:
                            validated = schema_class.model_validate(item)
                            payload = validated.model_dump(by_alias=True)
                        else:
                            payload = item

                        # Verify evidence spans
                        evidence = item.get("evidence_spans", [])
                        verified_spans = _verify_spans(evidence, record.text_content)

                        # Compute confidence
                        confidence = compute_confidence(
                            schema_valid=True,
                            evidence_spans=verified_spans,
                            extraction_payload=payload,
                            schema_class=schema_class,
                            parse_quality_score=parse_quality,
                        )

                        # Check for duplicates
                        existing = db.scalars(
                            select(Extraction).where(
                                Extraction.source_record_id == record.id,
                                Extraction.extraction_type == extraction_type,
                                Extraction.payload == payload,
                            )
                        ).first()
                        if existing:
                            total_skipped += 1
                            continue

                        extraction = Extraction(
                            source_record_id=record.id,
                            extraction_type=extraction_type,
                            payload=payload,
                            evidence_spans=verified_spans,
                            confidence_score=confidence.total_score,
                            confidence_tier=ConfidenceTier(confidence.tier),
                            review_status=ReviewStatus.pending,
                            model_id="claude-code-manual",
                        )
                        db.add(extraction)
                        db.flush()

                        db.add(ReviewQueueItem(
                            extraction_id=extraction.id,
                            priority={"A": 0, "B": 1, "C": 2, "D": 3}.get(confidence.tier, 1),
                        ))

                        file_created += 1

                    except Exception as e:
                        print(f"  ERROR: passage {passage_id}, agent {agent_name}: {e}")
                        file_errors += 1

        db.commit()
        files_processed += 1
        total_created += file_created
        total_errors += file_errors
        print(f"  Created {file_created} extractions, {file_errors} errors")

        # Rename processed file so it's not re-imported
        done_path = result_file.with_suffix(".json.done")
        result_file.rename(done_path)
        print(f"  Renamed to {done_path.name}")

    print(f"\nImport complete:")
    print(f"  Files processed:     {files_processed}")
    print(f"  Extractions created: {total_created}")
    print(f"  Duplicates skipped:  {total_skipped}")
    print(f"  Errors:              {total_errors}")

    return {
        "files_processed": files_processed,
        "extractions_created": total_created,
        "duplicates_skipped": total_skipped,
        "errors": total_errors,
    }


def _verify_spans(spans: list[dict], passage_text: str) -> list[dict]:
    """Verify evidence spans via string matching (same as base agent)."""
    verified = []
    for span in spans:
        text = span.get("text", "")
        field_name = span.get("field_name", "")
        if text and text in passage_text:
            start = passage_text.index(text)
            verified.append({
                "field_name": field_name,
                "text": text,
                "char_start": start,
                "char_end": start + len(text),
                "verified": True,
            })
        else:
            verified.append({
                "field_name": field_name,
                "text": text,
                "verified": False,
            })
    return verified
