"""Threshold & Exception Agent — consolidated from threshold + exception.

Co-extracts thresholds and exceptions because both are "boundary condition"
extractions — when does the obligation apply, and when doesn't it?
They share context needs (the obligation they modify) and can be co-extracted
(Recommendation #1).
"""

from pydantic import BaseModel

from src.agents.base import BaseExtractionAgent
from src.schemas.extraction import ThresholdExceptionPayload


class ThresholdExceptionAgent(BaseExtractionAgent):
    agent_name = "threshold_exception"
    model_override = "openai/gpt-oss-20b"

    def get_system_prompt(self) -> str:
        return """You are a legal extraction agent specializing in thresholds and exceptions.

Your task is to extract boundary conditions from legislative text:
- THRESHOLDS: Numeric or categorical conditions that determine when an obligation applies
  (e.g., "companies with more than 50 employees", "systems that process more than 10,000 records")
- EXCEPTIONS: Carve-outs, safe harbors, exemptions, and conditions under which an obligation
  does NOT apply (e.g., "except for small businesses", "does not apply to research purposes")

OUTPUT FORMAT:
Return a JSON object with a top-level "extractions" array. Each element includes:
- threshold_type: Type of threshold (numeric, categorical, temporal, etc.)
- threshold_value: The threshold value
- threshold_unit: Unit of measurement if applicable
- threshold_condition: The full condition expression
- applies_to_obligation: Which obligation this threshold modifies
- exceptions: Array of {exception_type, description, conditions, scope}
- evidence_spans: Array of {field_name, text} where text is a VERBATIM quote from the passage

If the passage contains MULTIPLE boundary conditions, include one object per condition.

If the passage contains NO extractable thresholds or exceptions, return:
{"detected": false, "reason": "<describe why no thresholds or exceptions were found>"}

CRITICAL RULES:
- Every evidence_spans[].text MUST appear VERBATIM in the source passage
- Use abstention (detected: false) rather than hallucinating boundaries
- Distinguish clearly between thresholds (when it applies) and exceptions (when it doesn't)
- Capture exact numeric values and units

EVIDENCE SPAN RULES (IMPORTANT — spans are verified by exact string match):
- Copy text EXACTLY as it appears in the passage — same capitalization, same punctuation, same spacing
- Do NOT paraphrase, summarize, or reword the text
- Do NOT fix typos, grammar, or formatting in the quoted text
- Include enough context for the span to be meaningful (usually 10-40 words)

EXAMPLE (for a passage containing "This section does not apply to a covered entity that employs fewer than 50 employees."):
  CORRECT: {"field_name": "text", "text": "This section does not apply to a covered entity that employs fewer than 50 employees."}
  WRONG:   {"field_name": "text", "text": "Does not apply to covered entities with fewer than 50 employees."}
The second is wrong because it paraphrases instead of copying the exact text."""

    def get_extraction_prompt(self, passage: str, context: dict | None = None) -> str:
        prompt = f"""Extract all thresholds and exceptions from the following legislative passage.
Identify boundary conditions that determine when obligations apply or don't apply.

If there are multiple boundary conditions, return each as a separate object in
the "extractions" array.

PASSAGE:
---
{passage}
---"""
        if context:
            if context.get("document_title"):
                prompt += f"\n\nDOCUMENT: {context['document_title']}"
            if context.get("related_obligations"):
                prompt += f"\nRELATED OBLIGATIONS: {context['related_obligations']}"
            if context.get("key_requirements"):
                prompt += (
                    f"\n\nKEY REQUIREMENTS (from Orrick AI Law Tracker — use as "
                    f"context to improve extraction accuracy):\n{context['key_requirements']}"
                )
        return prompt

    def get_output_schema(self) -> type[BaseModel]:
        return ThresholdExceptionPayload
