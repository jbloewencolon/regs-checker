"""ARCHIVED 2026-04-05 — Ambiguity Agent retired.

Ambiguity findings are now embedded as `interpretation_risks` annotations on
ObligationPayload and RightsProtectionPayload (src/schemas/extraction.py).
The obligation and rights agents populate this field during their primary
extraction pass — zero additional LLM calls, findings attached to the
obligation or right they affect.

ExtractionType.ambiguity remains in the DB enum for backward compat with
existing rows. This file is kept as reference for the original schema and
prompt design.

----

Original docstring:

Ambiguity Agent — meta-analysis agent (unchanged from original design).

Identifies vague, ambiguous, or conflicting language in legislative text.
Kept separate because it's genuinely different from extraction — it's a
meta-analysis task that evaluates the quality and clarity of the legal text
itself (Recommendation #1).

Uses openai/gpt-oss-20b (same as all other agents) to avoid VRAM model
swapping in LM Studio, which adds seconds of latency per passage.
"""

from pydantic import BaseModel

from src.agents.base import BaseExtractionAgent
from src.schemas.extraction import AmbiguityPayload


class AmbiguityAgent(BaseExtractionAgent):
    agent_name = "ambiguity"
    model_override = "openai/gpt-oss-20b"

    def get_system_prompt(self) -> str:
        return """You are a legal analysis agent specializing in identifying ambiguity in legislative text.

Your task is to identify language that is vague, ambiguous, internally conflicting,
or references undefined terms. This is a META-ANALYSIS task — you are evaluating the
quality and clarity of the legal text itself, not extracting substantive obligations.

TYPES OF AMBIGUITY:
- vague_term: Terms that lack precise definition (e.g., "reasonable", "appropriate", "significant")
- conflicting_provisions: Provisions that appear to contradict each other
- undefined_reference: References to terms, entities, or standards not defined in the text
- scope_ambiguity: Unclear scope of applicability
- temporal_ambiguity: Unclear timing or sequencing
- conditional_ambiguity: Conditions that are difficult to evaluate

OUTPUT FORMAT:
Return a JSON object with a top-level "extractions" array. Each element includes:
- ambiguous_text: The exact ambiguous passage
- ambiguity_type: One of the types listed above
- severity: low / medium / high / critical
- affected_obligations: List of obligation references that may be affected
- interpretation_notes: Analysis of why this is ambiguous
- suggested_clarification: What would make the language clearer
- evidence_spans: Array of {field_name, text} where text is a VERBATIM quote from the passage

If the passage contains MULTIPLE ambiguities, include one object per ambiguity.

If the passage contains NO identifiable ambiguity, return:
{"detected": false, "reason": "<describe why no ambiguity was found>"}

CRITICAL RULES:
- Every evidence_spans[].text MUST appear VERBATIM in the source passage
- Use abstention (detected: false) rather than over-flagging clear language
- Severity should reflect actual compliance risk, not stylistic preference
- Not all general terms are ambiguous — consider legislative context

EVIDENCE SPAN RULES (IMPORTANT — spans are verified by exact string match):
- Copy text EXACTLY as it appears in the passage — same capitalization, same punctuation, same spacing
- Do NOT paraphrase, summarize, or reword the text
- The ambiguous_text field must ALSO be an exact verbatim quote from the passage

EXAMPLE (for a passage containing "the system shall use reasonable measures to prevent harm"):
  CORRECT evidence_span: {"field_name": "ambiguous_text", "text": "the system shall use reasonable measures to prevent harm"}
  WRONG:  {"field_name": "ambiguous_text", "text": "reasonable measures to prevent harm"}
The second is wrong because it drops the beginning of the phrase."""

    def get_extraction_prompt(self, passage: str, context: dict | None = None) -> str:
        prompt = f"""Analyze the following legislative passage for ambiguity, vagueness,
conflicting provisions, or undefined references.

If there are multiple ambiguities, return each as a separate object in
the "extractions" array.

PASSAGE:
---
{passage}
---"""
        if context:
            if context.get("document_title"):
                prompt += f"\n\nDOCUMENT: {context['document_title']}"
            if context.get("defined_terms"):
                prompt += (
                    f"\nALREADY DEFINED TERMS: {', '.join(context['defined_terms'])}"
                )
            if context.get("key_requirements"):
                prompt += (
                    f"\n\nKEY REQUIREMENTS (from Orrick AI Law Tracker — use as "
                    f"context to identify ambiguity in scope and applicability):\n"
                    f"{context['key_requirements']}"
                )
        prompt = self._append_bill_context(prompt, context)
        return prompt

    def get_output_schema(self) -> type[BaseModel]:
        return AmbiguityPayload
