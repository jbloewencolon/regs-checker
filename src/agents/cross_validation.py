"""Cross-Validation Agent — post-extraction consistency and accuracy checker.

Reviews existing extractions against the source passage to flag:
  - Hallucinated content (claims not supported by passage text)
  - Contradictions between extraction fields
  - Missed nuances (qualifiers, conditions, exceptions not captured)
  - Over-extraction (splitting one obligation into fragments)

Uses a DIFFERENT model from the primary extraction agents to provide
genuine model diversity. Primary agents use qwen3.5-9b; this agent
uses GPT for an independent second opinion.

This is a verification layer — it runs AFTER initial extraction and
produces a validation result, not new extractions. The validation
score feeds into the confidence model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
      - "failed"    — the provider call or parse failed; results are empty and
                      ``avg_accuracy_score`` MUST NOT be folded into any
                      document-level average or used to raise confidence.
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
- evidence_text MUST be VERBATIM from the source passage."""


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

    # Use GPT for cross-validation (different model than extraction agents)
    provider = get_extraction_provider()
    system_prompt = CROSS_VALIDATION_SYSTEM_PROMPT + (
        "\n\nReturn only raw JSON with no markdown formatting, "
        "no code fences, and no preamble."
    )

    try:
        response = provider.call(
            system_prompt=system_prompt,
            user_prompt=prompt,
            model_override="openai/gpt-oss-20b",
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

        results = []
        valid_count = 0
        flagged_count = 0
        total_accuracy = 0.0

        for v in validations:
            is_valid = v.get("is_valid", True)
            accuracy = float(v.get("accuracy_score", 1.0 if is_valid else 0.5))
            total_accuracy += accuracy

            ext_idx = v.get("extraction_index", len(results))
            ext_id = (
                extraction_ids[ext_idx]
                if extraction_ids and ext_idx < len(extraction_ids)
                else None
            )

            result_dict = {
                "extraction_id": ext_id,
                "extraction_index": ext_idx,
                "is_valid": is_valid,
                "accuracy_score": accuracy,
                "issues": v.get("issues", []),
                "notes": v.get("notes"),
            }
            results.append(result_dict)

            if is_valid:
                valid_count += 1
            else:
                flagged_count += 1

        avg_accuracy = total_accuracy / len(validations) if validations else 1.0

        logger.info(
            "cross_validation_complete",
            record_id=passage_record_id,
            checked=len(validations),
            valid=valid_count,
            flagged=flagged_count,
            avg_accuracy=round(avg_accuracy, 3),
            model_id=model_id,
        )

        return CrossValidationSummary(
            passage_record_id=passage_record_id,
            extractions_checked=len(validations),
            extractions_valid=valid_count,
            extractions_flagged=flagged_count,
            avg_accuracy_score=round(avg_accuracy, 4),
            results=results,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            status="completed",
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
