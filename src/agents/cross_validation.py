"""Cross-Validation Agent — post-extraction consistency and accuracy checker.

Reviews existing extractions against the source passage to flag:
  - Hallucinated content (claims not supported by passage text)
  - Contradictions between extraction fields
  - Missed nuances (qualifiers, conditions, exceptions not captured)
  - Over-extraction (splitting one obligation into fragments)

Model is config-driven via agent_models.json["cross_validation"] (EA0-5) so
it resolves correctly under both the nvidia and local providers. Note: the
current default does NOT provide genuine model-lineage diversity from the
primary extraction agents (same family, different size) — see EA4-1 in
tasks.md for the planned fix.

This is a verification layer — it runs AFTER initial extraction and
produces a validation result, not new extractions. The validation
score feeds into the confidence model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import BaseModel, Field

from src.core.llm_provider import get_extraction_provider

logger = structlog.get_logger()


class CrossValidationIssue(BaseModel):
    """A single issue found during cross-validation."""

    issue_type: str = Field(
        description="Type: hallucination, contradiction, missed_nuance, "
        "over_extraction, under_extraction, incorrect_modality, incorrect_subject"
    )
    severity: str = Field(description="low / medium / high / critical")
    field_name: str | None = Field(
        default=None, description="Which extraction field has the issue"
    )
    explanation: str = Field(description="What is wrong and why")
    suggested_correction: str | None = Field(
        default=None, description="How to fix it"
    )
    evidence_text: str | None = Field(
        default=None, description="Verbatim passage text supporting the finding"
    )


class CrossValidationResult(BaseModel):
    """Result of cross-validating a single extraction against its source."""

    extraction_id: int | None = Field(
        default=None, description="ID of the extraction being validated"
    )
    is_valid: bool = Field(description="Whether the extraction passes validation")
    accuracy_score: float = Field(
        description="0.0-1.0 accuracy estimate based on review"
    )
    issues: list[CrossValidationIssue] = Field(
        default_factory=list, description="Issues found"
    )
    notes: str | None = Field(
        default=None, description="General reviewer notes"
    )


@dataclass
class CrossValidationSummary:
    """Aggregate results from cross-validating extractions for a passage.

    ``status`` makes the outcome explicit so callers never confuse a genuine
    pass with a failure (fail-closed):
      - "completed" — the model ran and produced validations
      - "skipped"   — there was nothing to validate (no extractions)
      - "failed"    — the provider call or parse failed, or every validation
                      item was unattributable (missing/duplicate/out-of-range
                      extraction_index); results are empty and
                      ``avg_accuracy_score`` MUST NOT be folded into any
                      document-level average or used to raise confidence.

    ``unmatched_extraction_ids`` lists extractions that had no corresponding
    validation item (model returned fewer validations than extractions) —
    these were never actually reviewed by CV and must not be treated as
    passing. ``discarded_count`` is the number of validation items dropped
    for missing/duplicate/out-of-range extraction_index (EA0-1).
    """

    passage_record_id: int
    extractions_checked: int
    extractions_valid: int
    extractions_flagged: int
    avg_accuracy_score: float
    results: list[dict[str, Any]]
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = "completed"
    unmatched_extraction_ids: list[int] = field(default_factory=list)
    discarded_count: int = 0


CROSS_VALIDATION_SYSTEM_PROMPT = """You are a legal accuracy auditor. Your role is to verify the accuracy
of AI-generated extractions from legislative text.

You will be given:
1. A SOURCE PASSAGE (the original legislative text)
2. One or more EXTRACTIONS (structured data claimed to be extracted from the passage)

Your job is to check each extraction for accuracy:

ISSUE TYPES:
- hallucination: Extraction contains claims NOT supported by the passage text
- contradiction: Fields within the extraction contradict each other
- missed_nuance: Important qualifiers, conditions, or exceptions in the passage are not captured
- over_extraction: A single obligation has been incorrectly split into fragments
- under_extraction: Multiple distinct obligations have been incorrectly merged
- incorrect_modality: The modality (shall/must/may/prohibited) is wrong
- incorrect_subject: The regulated entity (subject) is misidentified

SEVERITY LEVELS:
- critical: Extraction fundamentally misrepresents the law (wrong subject, wrong action, wrong modality)
- high: Material error that changes the compliance requirement
- medium: Non-trivial omission or imprecision that could mislead
- low: Minor stylistic or formatting issue

OUTPUT FORMAT:
Return JSON with a top-level "validations" array. Each element is:
{
  "extraction_index": <0-based index of the extraction being validated>,
  "is_valid": true/false,
  "accuracy_score": 0.0-1.0,
  "issues": [{"issue_type": "...", "severity": "...", "field_name": "...", "explanation": "...", "suggested_correction": "...", "evidence_text": "..."}],
  "notes": "optional general notes"
}

If ALL extractions are accurate, still return the array with is_valid=true and accuracy_score=1.0.

CRITICAL RULES:
- Be STRICT. If the passage says "shall" and the extraction says "must", that is NOT an error (they are legally equivalent).
- Focus on SUBSTANCE over style. Minor paraphrasing is acceptable; changing meaning is not.
- Every issue MUST cite specific text from the passage or extraction.
- Do NOT penalize extractions for capturing only part of a long passage — focus on whether what IS captured is correct.
- evidence_text MUST be VERBATIM from the source passage.
- "extraction_index" and "accuracy_score" are REQUIRED on every validation item — never omit them.
- Return EXACTLY one validation item per extraction shown above, each with a unique "extraction_index" matching its "--- Extraction N ---" position. Do not skip, merge, or duplicate indices."""


def run_cross_validation(
    passage_text: str,
    extractions: list[dict[str, Any]],
    passage_record_id: int,
    extraction_ids: list[int] | None = None,
    context: dict | None = None,
) -> CrossValidationSummary:
    """Cross-validate extractions against their source passage.

    Uses a different model (GPT) than the primary extraction agents to
    provide genuine model diversity for verification.

    Args:
        passage_text: The source legislative text
        extractions: List of extraction payload dicts to validate
        passage_record_id: ID of the NormalizedSourceRecord
        extraction_ids: Optional list of extraction DB IDs (parallel to extractions)
        context: Optional context dict (document title, jurisdiction, etc.)

    Returns:
        CrossValidationSummary with per-extraction validation results.
    """
    if not extractions:
        return CrossValidationSummary(
            passage_record_id=passage_record_id,
            extractions_checked=0,
            extractions_valid=0,
            extractions_flagged=0,
            avg_accuracy_score=1.0,
            results=[],
            status="skipped",
        )

    # Build the validation prompt
    prompt = f"""Cross-validate the following extractions against the source passage.

SOURCE PASSAGE:
---
{passage_text}
---

EXTRACTIONS TO VALIDATE:
"""
    for i, ext in enumerate(extractions):
        # Strip internal metadata fields
        clean_ext = {k: v for k, v in ext.items() if not k.startswith("_")}
        prompt += f"\n--- Extraction {i} ---\n{json.dumps(clean_ext, indent=2, default=str)}\n"

    if context:
        if context.get("document_title"):
            prompt += f"\nDOCUMENT: {context['document_title']}"
        if context.get("jurisdiction"):
            prompt += f"\nJURISDICTION: {context['jurisdiction']}"

    # Model is config-driven (EA0-5) via agent_models.json["cross_validation"]
    # so verification works under both the nvidia and local providers — the
    # previous hardcoded "openai/gpt-oss-20b" override broke under the local
    # provider (LM Studio only loads one model; that name isn't it) and
    # bypassed the Models page entirely.
    from src.core.model_config import get_config
    cfg = get_config().get("cross_validation")

    provider = get_extraction_provider()
    system_prompt = CROSS_VALIDATION_SYSTEM_PROMPT + (
        "\n\nReturn only raw JSON with no markdown formatting, "
        "no code fences, and no preamble."
    )

    try:
        response = provider.call(
            system_prompt=system_prompt,
            user_prompt=prompt,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
            model_override=cfg.model or None,
        )
        raw_output = response.text
        usage = response.usage
        model_id = response.model_id

        # Parse response
        cleaned = raw_output.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
            cleaned = cleaned.rsplit("```", 1)[0].strip()

        parsed = json.loads(cleaned)
        validations = parsed.get("validations", [parsed])
        if not isinstance(validations, list):
            validations = [validations]

        # EA0-1: attribution must be trustworthy before a validation result is
        # allowed to recompute an extraction's confidence/tier. A model-reported
        # extraction_index that is missing, duplicated, or out of range would
        # otherwise silently write a CV score onto the wrong extraction row (or
        # onto no row, via the old `len(results)` fallback, which drifts the
        # moment items arrive out of order or one is dropped).
        results = []
        valid_count = 0
        flagged_count = 0
        total_accuracy = 0.0
        discarded_count = 0
        seen_indices: set[int] = set()
        num_extractions = len(extraction_ids) if extraction_ids else len(extractions)

        for v in validations:
            ext_idx = v.get("extraction_index")
            if not isinstance(ext_idx, int) or isinstance(ext_idx, bool):
                logger.warning(
                    "cross_validation_missing_index",
                    record_id=passage_record_id,
                    raw_index=v.get("extraction_index"),
                )
                discarded_count += 1
                continue
            if ext_idx < 0 or ext_idx >= num_extractions:
                logger.warning(
                    "cross_validation_index_out_of_range",
                    record_id=passage_record_id,
                    extraction_index=ext_idx,
                    num_extractions=num_extractions,
                )
                discarded_count += 1
                continue
            if ext_idx in seen_indices:
                logger.warning(
                    "cross_validation_duplicate_index",
                    record_id=passage_record_id,
                    extraction_index=ext_idx,
                )
                discarded_count += 1
                continue
            seen_indices.add(ext_idx)

            is_valid = v.get("is_valid", True)
            raw_accuracy = v.get("accuracy_score")
            if raw_accuracy is None:
                # A missing score must not read as a perfect (or a free 0.5)
                # pass — that let a bare {"is_valid": true} inflate confidence
                # via cross_validation with no actual scoring behind it.
                logger.warning(
                    "cross_validation_missing_accuracy_score",
                    record_id=passage_record_id,
                    extraction_index=ext_idx,
                )
                accuracy = 0.5
                score_missing = True
            else:
                accuracy = float(raw_accuracy)
                score_missing = False
            total_accuracy += accuracy

            ext_id = extraction_ids[ext_idx] if extraction_ids else None

            result_dict = {
                "extraction_id": ext_id,
                "extraction_index": ext_idx,
                "is_valid": is_valid,
                "accuracy_score": accuracy,
                "score_missing": score_missing,
                "issues": v.get("issues", []),
                "notes": v.get("notes"),
            }
            results.append(result_dict)

            if is_valid:
                valid_count += 1
            else:
                flagged_count += 1

        # Every validation item was unattributable — this is not a clean pass,
        # it's a parse/attribution failure. Fail closed rather than report an
        # empty "completed" result that looks identical to "nothing to flag".
        if validations and not results:
            logger.error(
                "cross_validation_all_items_discarded",
                record_id=passage_record_id,
                discarded=discarded_count,
            )
            return CrossValidationSummary(
                passage_record_id=passage_record_id,
                extractions_checked=0,
                extractions_valid=0,
                extractions_flagged=0,
                avg_accuracy_score=0.0,
                results=[],
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                status="failed",
                discarded_count=discarded_count,
            )

        unmatched_ids: list[int] = []
        if extraction_ids:
            matched = {r["extraction_id"] for r in results}
            unmatched_ids = [eid for eid in extraction_ids if eid not in matched]
            if unmatched_ids:
                logger.warning(
                    "cross_validation_unmatched_extractions",
                    record_id=passage_record_id,
                    unmatched_extraction_ids=unmatched_ids,
                )

        avg_accuracy = total_accuracy / len(results) if results else 1.0

        logger.info(
            "cross_validation_complete",
            record_id=passage_record_id,
            checked=len(results),
            valid=valid_count,
            flagged=flagged_count,
            avg_accuracy=round(avg_accuracy, 3),
            discarded=discarded_count,
            unmatched=len(unmatched_ids),
            model_id=model_id,
        )

        return CrossValidationSummary(
            passage_record_id=passage_record_id,
            extractions_checked=len(results),
            extractions_valid=valid_count,
            extractions_flagged=flagged_count,
            avg_accuracy_score=round(avg_accuracy, 4),
            results=results,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            status="completed",
            unmatched_extraction_ids=unmatched_ids,
            discarded_count=discarded_count,
        )

    except Exception as e:
        logger.error(
            "cross_validation_failed",
            record_id=passage_record_id,
            error=str(e),
        )
        # FAIL CLOSED: a verification failure must never look like a pass.
        # Return an explicit "failed" status with empty results so the caller
        # cannot fold a neutral accuracy into the document average or use it to
        # raise confidence. avg_accuracy_score=0.0 is a defensive default only;
        # callers gate on status, not this value.
        return CrossValidationSummary(
            passage_record_id=passage_record_id,
            extractions_checked=0,
            extractions_valid=0,
            extractions_flagged=0,
            avg_accuracy_score=0.0,
            results=[],
            status="failed",
        )
