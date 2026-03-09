"""Obligation Agent — consolidated from obligation + timeline + enforcement.

Co-extracts obligations, their timelines, and enforcement mechanisms in a
single pass because these are structurally co-located in legislative text
(Recommendation #1).
"""

from pydantic import BaseModel

from src.agents.base import BaseExtractionAgent
from src.schemas.extraction import ObligationPayload


class ObligationAgent(BaseExtractionAgent):
    agent_name = "obligation"

    def get_system_prompt(self) -> str:
        return """You are a legal extraction agent specializing in regulatory obligations.

Your task is to extract structured obligation data from legislative text passages.
You MUST co-extract the obligation itself, any associated timeline/dates, and any
enforcement mechanisms, because these are typically co-located in the same clause
or adjacent clauses.

OUTPUT FORMAT:
Return a JSON object with a top-level "extractions" array. Each element includes:
- subject: Who must comply (the regulated entity)
- subject_normalized: Normalized category (e.g., "developer", "deployer", "operator")
- modality: must / shall / may / should / prohibited
- action: What the subject must do or refrain from doing
- object: What the action applies to (if specified)
- condition: Conditions or triggers for the obligation
- jurisdiction: Jurisdiction code if identifiable
- section_reference: Section/subsection reference
- timeline: Object with effective_date, compliance_deadline, sunset_date, phase_in_period, timeline_text
- enforcement: Object with enforcing_body, penalty_type, penalty_description, private_right_of_action, enforcement_text
- evidence_spans: Array of {field_name, text} where text is a VERBATIM quote from the passage

If the passage contains MULTIPLE obligations, include one object per obligation.

If the passage contains NO extractable obligation, return:
{"detected": false, "reason": "explanation"}

CRITICAL RULES:
- Every evidence_spans[].text MUST appear VERBATIM in the source passage
- Use abstention (detected: false) rather than hallucinating obligations
- Extract ALL obligations if the passage contains multiple
- Preserve legal precision — do not paraphrase legal terms of art"""

    def get_extraction_prompt(self, passage: str, context: dict | None = None) -> str:
        prompt = f"""Extract all regulatory obligations from the following legislative passage.
Co-extract any timeline information (effective dates, deadlines, sunset dates) and
enforcement mechanisms (penalties, enforcing bodies, private rights of action) that
are associated with each obligation.

If there are multiple distinct obligations, return each as a separate object in the
"extractions" array.

PASSAGE:
---
{passage}
---"""
        if context:
            if context.get("document_title"):
                prompt += f"\n\nDOCUMENT: {context['document_title']}"
            if context.get("jurisdiction"):
                prompt += f"\nJURISDICTION: {context['jurisdiction']}"
            if context.get("section_path"):
                prompt += f"\nSECTION: {context['section_path']}"
            if context.get("key_requirements"):
                prompt += (
                    f"\n\nKEY REQUIREMENTS (from Orrick AI Law Tracker — use as "
                    f"context to improve extraction accuracy):\n{context['key_requirements']}"
                )
            if context.get("enforcement_summary"):
                prompt += f"\nENFORCEMENT SUMMARY: {context['enforcement_summary']}"
        return prompt

    def get_output_schema(self) -> type[BaseModel]:
        return ObligationPayload
