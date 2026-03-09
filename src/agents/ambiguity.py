"""Ambiguity Agent — meta-analysis agent (unchanged from original design).

Identifies vague, ambiguous, or conflicting language in legislative text.
Kept separate because it's genuinely different from extraction — it's a
meta-analysis task that evaluates the quality and clarity of the legal text
itself (Recommendation #1).
"""

from pydantic import BaseModel

from src.agents.base import BaseExtractionAgent
from src.schemas.extraction import AmbiguityPayload


class AmbiguityAgent(BaseExtractionAgent):
    agent_name = "ambiguity"

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
{"detected": false, "reason": "explanation"}

CRITICAL RULES:
- Every evidence_spans[].text MUST appear VERBATIM in the source passage
- Use abstention (detected: false) rather than over-flagging clear language
- Severity should reflect actual compliance risk, not stylistic preference
- Not all general terms are ambiguous — consider legislative context"""

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
        return prompt

    def get_output_schema(self) -> type[BaseModel]:
        return AmbiguityPayload
