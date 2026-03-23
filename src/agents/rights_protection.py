"""Rights & Protections Agent — extracts individual rights granted by AI legislation.

Captures the flip side of obligations: what individuals are entitled to.
While the Obligation Agent extracts what entities must DO, this agent extracts
what consumers, employees, and data subjects are ENTITLED TO — notice,
explanation, opt-out, appeal, deletion, human review, etc.

Uses GPT (openai/gpt-oss-20b) for reliable structured output.  DeepSeek-R1
reasoning models consume all output tokens on chain-of-thought before
producing JSON, causing repeated truncation and empty responses.
"""

from pydantic import BaseModel

from src.agents.base import BaseExtractionAgent
from src.schemas.extraction import RightsProtectionPayload


class RightsProtectionAgent(BaseExtractionAgent):
    agent_name = "rights_protection"
    model_override = "openai/gpt-oss-20b"

    def get_system_prompt(self) -> str:
        return """You are a legal extraction agent specializing in individual rights and protections in AI legislation.

Your task is to extract rights granted to individuals (consumers, employees, applicants, data subjects)
by AI regulation. These are the FLIP SIDE of obligations — while obligations say what entities must DO,
rights say what individuals are ENTITLED TO.

TYPES OF RIGHTS:
- notice: Right to be informed that AI is being used in a decision
- explanation: Right to understand how an AI system reached a decision
- opt_out: Right to decline AI-based processing or decision-making
- appeal: Right to contest or appeal an AI-driven decision
- deletion: Right to have personal data or AI-generated content deleted
- human_review: Right to request human review of an automated decision
- non_discrimination: Right to be free from algorithmic discrimination
- portability: Right to receive or transfer data used by AI systems
- access: Right to access information about AI systems affecting them

OUTPUT FORMAT:
Return a JSON object with a top-level "extractions" array. Each element includes:
- right_holder: Who holds the right (consumer, employee, applicant, data subject, etc.)
- right_holder_normalized: Normalized category (consumer, employee, public)
- right_type: One of the types listed above
- right_description: Full description of the right in legal language
- trigger_condition: When the right is activated (e.g., "upon adverse decision", "before AI interaction")
- duty_bearer: Who must fulfill this right (developer, deployer, employer, etc.)
- remedies: Array of {remedy_type, description, available_to, time_limit}
- section_reference: Section/subsection reference
- jurisdiction: Jurisdiction code if identifiable
- evidence_spans: Array of {field_name, text} where text is a VERBATIM quote from the passage

If the passage grants MULTIPLE rights, include one object per right.

If the passage contains NO individual rights or protections, return:
{"detected": false, "reason": "<describe why no rights or protections were found>"}

CRITICAL RULES:
- Every evidence_spans[].text MUST appear VERBATIM in the source passage
- Use abstention (detected: false) rather than hallucinating rights
- Distinguish rights (what individuals get) from obligations (what entities must do)
- A notice OBLIGATION on a company implies a notice RIGHT for the individual — extract the right
- Capture remedies and recourse mechanisms when specified
- Preserve exact legal language — do not paraphrase

EVIDENCE SPAN RULES (IMPORTANT — spans are verified by exact string match):
- Copy text EXACTLY as it appears in the passage — same capitalization, same punctuation, same spacing
- Do NOT paraphrase, summarize, or reword the text
- Do NOT fix typos, grammar, or formatting in the quoted text

EXAMPLE (for a passage containing "A consumer has the right to opt out of automated decision-making."):
  CORRECT: {"field_name": "text", "text": "A consumer has the right to opt out of automated decision-making."}
  WRONG:   {"field_name": "text", "text": "Consumers have the right to opt out of automated decision-making."}
The second is wrong because it changes "A consumer" to "Consumers"."""

    def get_extraction_prompt(self, passage: str, context: dict | None = None) -> str:
        prompt = f"""Analyze the following legislative passage and extract all individual rights
and protections it grants. Look for:
- Explicit rights ("the consumer has the right to...")
- Implied rights from obligations ("the deployer shall notify..." implies a notice right)
- Remedies and recourse ("may file a complaint", "shall have the opportunity to appeal")

If there are multiple rights, return each as a separate object in the "extractions" array.

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
                    f"context to identify rights and protections):\n{context['key_requirements']}"
                )
            if context.get("enforcement_summary"):
                prompt += (
                    f"\n\nENFORCEMENT CONTEXT (helps identify remedies available "
                    f"to rights holders):\n{context['enforcement_summary']}"
                )
        return prompt

    def get_output_schema(self) -> type[BaseModel]:
        return RightsProtectionPayload
