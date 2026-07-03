"""Obligation Gap Detector — second-pass agent that finds missed obligations.

After the primary extraction agents run, this agent reviews the passage
alongside the existing extractions and identifies obligations, rights,
or requirements that the first pass missed.

This catches:
  - Obligations phrased in non-standard ways ("it is the policy of", "developers
    are expected to", passive constructions)
  - Obligations buried in subordinate clauses or parentheticals
  - Implicit obligations created by definitions (e.g., "covered model" definition
    implicitly creates a compliance boundary)
  - Multiple obligations in a single sentence where only one was captured

Model is config-driven via agent_models.json["gap_detection"] (EA0-5) so it
resolves correctly under both the nvidia and local providers. Note: the
current default does NOT provide genuine model-lineage diversity from the
primary extraction agents (same family, different size) — see EA4-1 in
tasks.md for the planned fix.
Returns new extraction candidates, not modifications to existing ones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import BaseModel, Field

from src.core.llm_provider import get_extraction_provider

logger = structlog.get_logger()


class GapCandidate(BaseModel):
    """A potential missed extraction identified by gap detection."""

    extraction_type: str = Field(
        description="Type: obligation, rights_protection, compliance_mechanism, "
        "threshold, exception, definition"
    )
    summary: str = Field(
        description="One-sentence summary of the missed extraction"
    )
    subject: str | None = Field(default=None, description="Who is regulated")
    action: str | None = Field(default=None, description="What they must do")
    modality: str | None = Field(
        default=None, description="shall / must / may / prohibited / expected_to / directed_to"
    )
    evidence_text: str = Field(
        description="VERBATIM text from passage supporting this extraction"
    )
    why_missed: str = Field(
        description="Why the primary agents likely missed this "
        "(non-standard phrasing, subordinate clause, implicit, etc.)"
    )
    confidence: str = Field(
        description="high / medium / low — confidence this is a real missed extraction"
    )


class GapDetectionResult(BaseModel):
    """Result of gap detection for a single passage."""

    gaps_found: list[GapCandidate] = Field(default_factory=list)
    analysis_notes: str | None = None


@dataclass
class GapDetectionSummary:
    """Summary of gap detection across passages.

    ``status`` makes the outcome explicit (fail-closed):
      - "completed" — the model ran; ``gaps_found`` reflects a real check
      - "failed"    — the provider call or parse failed; ``gaps_found`` is 0
                      only because nothing ran. Callers MUST NOT treat a failed
                      detection as a clean "no gaps" result.
    """

    passage_record_id: int
    existing_extraction_count: int
    gaps_found: int
    high_confidence_gaps: int
    candidates: list[dict[str, Any]]
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = "completed"


GAP_DETECTION_SYSTEM_PROMPT = """You are a legal extraction auditor specializing in COMPLETENESS verification.

Your role is to review a legislative passage alongside EXISTING extractions and identify
obligations, rights, or requirements that the primary extraction agents MISSED.

You will receive:
1. A SOURCE PASSAGE (legislative text)
2. EXISTING EXTRACTIONS (what was already found)

Your job is to find what was MISSED. Focus on:

1. NON-STANDARD OBLIGATION PHRASING:
   - "is responsible for ensuring..."
   - "has a duty to..."
   - "is expected to..."
   - "is directed to..."
   - "it is the policy of this state that..."
   - "no person may..." (prohibition without "prohibited")
   - "will destroy/provide/notify..." (future tense as obligation)
   - Passive constructions ("shall be provided", "must be maintained")

2. BURIED OBLIGATIONS:
   - In subordinate clauses ("...provided that the developer also...")
   - In parentheticals ("(including any applicable audit requirements)")
   - In definitions that implicitly create compliance boundaries
   - In cross-references ("subject to the requirements of Section 5")

3. MULTIPLE OBLIGATIONS IN ONE SENTENCE:
   - "shall notify AND provide AND obtain consent" (3 separate obligations)
   - "must conduct assessments and maintain records" (2 obligations)

4. IMPLICIT REQUIREMENTS:
   - Definitions that narrow scope (create compliance boundary)
   - Rights that imply corresponding duties on another party
   - Exceptions that implicitly affirm the rule they except from

OUTPUT FORMAT:
Return JSON:
{
  "gaps_found": [
    {
      "extraction_type": "obligation|rights_protection|compliance_mechanism|threshold|exception|definition",
      "summary": "One-sentence description",
      "subject": "Who is regulated",
      "action": "What they must do",
      "modality": "shall/must/may/prohibited/expected_to/directed_to",
      "evidence_text": "VERBATIM text from passage",
      "why_missed": "Why primary agents likely missed this",
      "confidence": "high/medium/low"
    }
  ],
  "analysis_notes": "Optional overall notes"
}

If no gaps are found, return: {"gaps_found": [], "analysis_notes": "No missed extractions identified."}

CRITICAL RULES:
- evidence_text MUST appear VERBATIM in the source passage
- Do NOT re-report obligations that are already captured in the existing extractions
- Only report HIGH or MEDIUM confidence gaps — don't stretch to find issues that aren't there
- An obligation phrased differently but with the same meaning is NOT a gap
- Focus on SUBSTANCE: did the existing extractions capture the legal requirements? Not style."""


def run_gap_detection(
    passage_text: str,
    existing_extractions: list[dict[str, Any]],
    passage_record_id: int,
    context: dict | None = None,
) -> GapDetectionSummary:
    """Detect missed obligations in a passage by comparing against existing extractions.

    Args:
        passage_text: The source legislative text
        existing_extractions: List of extraction payload dicts already extracted
        passage_record_id: ID of the NormalizedSourceRecord
        context: Optional context dict (document title, jurisdiction, etc.)

    Returns:
        GapDetectionSummary with identified gap candidates.
    """
    prompt = f"""Review this legislative passage for MISSED obligations, rights, or requirements
that were NOT captured by the existing extractions.

SOURCE PASSAGE:
---
{passage_text}
---

EXISTING EXTRACTIONS ({len(existing_extractions)} found):
"""
    for i, ext in enumerate(existing_extractions):
        clean = {k: v for k, v in ext.items() if not k.startswith("_")}
        prompt += f"\n--- Extraction {i} ---\n{json.dumps(clean, indent=2, default=str)}\n"

    if not existing_extractions:
        prompt += "\n(No extractions found by primary agents — the passage may have been missed entirely)\n"

    if context:
        if context.get("document_title"):
            prompt += f"\nDOCUMENT: {context['document_title']}"
        if context.get("jurisdiction"):
            prompt += f"\nJURISDICTION: {context['jurisdiction']}"

    # Model is config-driven (EA0-5) via agent_models.json["gap_detection"] —
    # see cross_validation.py for the rationale (hardcoded override broke
    # under the local provider and bypassed the Models page).
    from src.core.model_config import get_config
    cfg = get_config().get("gap_detection")

    provider = get_extraction_provider()
    system_prompt = GAP_DETECTION_SYSTEM_PROMPT + (
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

        cleaned = raw_output.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
            cleaned = cleaned.rsplit("```", 1)[0].strip()

        parsed = json.loads(cleaned)
        gaps = parsed.get("gaps_found", [])
        if not isinstance(gaps, list):
            gaps = []

        # Filter to medium+ confidence only
        valid_gaps = []
        high_confidence = 0
        for gap in gaps:
            conf = gap.get("confidence", "low")
            if conf in ("high", "medium"):
                valid_gaps.append(gap)
                if conf == "high":
                    high_confidence += 1

        logger.info(
            "gap_detection_complete",
            record_id=passage_record_id,
            existing_extractions=len(existing_extractions),
            gaps_found=len(valid_gaps),
            high_confidence=high_confidence,
            model_id=model_id,
        )

        return GapDetectionSummary(
            passage_record_id=passage_record_id,
            existing_extraction_count=len(existing_extractions),
            gaps_found=len(valid_gaps),
            high_confidence_gaps=high_confidence,
            candidates=valid_gaps,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            status="completed",
        )

    except Exception as e:
        logger.error(
            "gap_detection_failed",
            record_id=passage_record_id,
            error=str(e),
        )
        # FAIL CLOSED: a failed detection is not "zero gaps found". Mark it
        # failed so the caller routes the passage to review instead of
        # treating it as clean.
        return GapDetectionSummary(
            passage_record_id=passage_record_id,
            existing_extraction_count=len(existing_extractions),
            gaps_found=0,
            high_confidence_gaps=0,
            candidates=[],
            status="failed",
        )
