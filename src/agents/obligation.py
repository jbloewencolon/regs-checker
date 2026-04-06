"""Obligation Agent — consolidated from obligation + timeline + enforcement.

Co-extracts obligations, their timelines, and enforcement mechanisms in a
single pass because these are structurally co-located in legislative text
(Recommendation #1).

Uses GPT (openai/gpt-oss-20b) because obligation extraction is a structured
extraction task — find "shall"/"must"/"prohibited" patterns, copy verbatim text,
fill the schema.  Reasoning models (Qwen3, DeepSeek-R1) waste thousands of
tokens on chain-of-thought before producing JSON, risking truncation on the
most token-heavy output schema in the pipeline.
"""

from pydantic import BaseModel

from src.agents.base import BaseExtractionAgent
from src.schemas.extraction import ObligationPayload


class ObligationAgent(BaseExtractionAgent):
    agent_name = "obligation"
    model_override = "nousresearch/hermes-4-70b"

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
- preemption_signals: Array of VERBATIM strings capturing any preemption language (e.g., "this section does not preempt", "notwithstanding any state law", "in addition to federal requirements", "nothing in this act shall be construed to"). Empty array if none found.
- evidence_spans: Array of {field_name, text} where text is a VERBATIM quote from the passage

If the passage contains MULTIPLE obligations, include one object per obligation.

If the passage contains NO extractable obligation, return:
{"detected": false, "reason": "<describe why no obligations were found>"}

CRITICAL RULES:
- Every evidence_spans[].text MUST appear VERBATIM in the source passage
- Use abstention (detected: false) rather than hallucinating obligations
- Extract ALL obligations if the passage contains multiple
- Preserve legal precision — do not paraphrase legal terms of art

EVIDENCE SPAN RULES (IMPORTANT — spans are verified by exact string match):
- Copy text EXACTLY as it appears in the passage — same capitalization, same punctuation, same spacing
- Do NOT paraphrase, summarize, or reword the text
- Do NOT fix typos, grammar, or formatting in the quoted text
- Include enough context for the span to be meaningful (usually 10-40 words)
- If the relevant text spans multiple lines, include it exactly as written

EXAMPLE (for a passage containing "The deployer shall, within 90 days of deployment, complete an impact assessment."):
  CORRECT: {"field_name": "action", "text": "The deployer shall, within 90 days of deployment, complete an impact assessment."}
  WRONG:   {"field_name": "action", "text": "The deployer shall complete an impact assessment within 90 days of deployment."}
  WRONG:   {"field_name": "action", "text": "deployer shall, within 90 days of deployment, complete an impact assessment"}
The second is wrong because it reorders words. The third is wrong because it drops "The" at the start."""

    def get_extraction_prompt(self, passage: str, context: dict | None = None) -> str:
        prompt = f"""Extract all regulatory obligations from the following legislative passage.
Co-extract any timeline information (effective dates, deadlines, sunset dates),
enforcement mechanisms (penalties, enforcing bodies, private rights of action), and
preemption signals (language about federal/state preemption, savings clauses, or
"notwithstanding" provisions) that are associated with each obligation.

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
        prompt = self._append_bill_context(prompt, context)
        return prompt

    def get_output_schema(self) -> type[BaseModel]:
        return ObligationPayload
