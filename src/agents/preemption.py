"""Preemption Signal Agent — cross-jurisdictional conflict detection.

Identifies federal preemption risks, Commerce Clause tensions, cross-state
contradictions, and First Amendment challenges in legislative text.

This agent fills the Jurisdictional_Conflicts field in the State AI
Regulation Matrix schema. It detects both explicit preemption language
("nothing in this section shall preempt") and implicit structural conflicts
(state law regulating interstate model distribution).

Uses openai/gpt-oss-20b (same as ambiguity, definition_actor, etc.) to
avoid VRAM model swapping. Runs in the GPT group alongside the other 5
GPT-based agents.
"""

from pydantic import BaseModel

from src.agents.base import BaseExtractionAgent
from src.schemas.extraction import PreemptionSignalPayload


class PreemptionAgent(BaseExtractionAgent):
    agent_name = "preemption"
    model_override = "openai/gpt-oss-20b"

    def get_system_prompt(self) -> str:
        return """You are a legal analysis agent specializing in jurisdictional conflicts and preemption analysis for US AI legislation.

Your task is to identify cross-jurisdictional conflicts in legislative text:

CONFLICT TYPES:
- federal_preemption: Language indicating federal law, executive order, or agency regulation may override this state provision. Look for savings clauses ("nothing in this section shall preempt"), express preemption clauses, or references to federal supremacy.
- interstate_commerce: Provisions that regulate AI systems distributed across state lines, potentially burdening interstate commerce. Look for developer obligations that apply to out-of-state companies or model distribution requirements.
- cross_state_conflict: Requirements that directly contradict obligations in other state AI laws (e.g., one state requires disclosure of training data while another prohibits it as trade secret).
- first_amendment: Provisions that may regulate AI-generated speech or content in ways that trigger First Amendment scrutiny. Look for content labeling mandates, synthetic content restrictions, or compelled speech requirements.
- dormant_commerce_clause: State regulations that discriminate against or unduly burden interstate commerce even without explicit federal preemption. Look for requirements that effectively require a national company to comply with one state's rules everywhere.
- agency_jurisdiction: Overlapping or conflicting regulatory authority between agencies (e.g., state AG vs. sector regulator vs. federal agency).
- other: Any other jurisdictional tension not covered above.

OUTPUT FORMAT:
Return a JSON object with a top-level "extractions" array. Each element includes:
- conflict_type: One of the types listed above
- description: Plain-language explanation of the conflict or risk
- related_authority: The preempting or conflicting authority (e.g., "Dec 2025 Federal EO on AI", "US Constitution Art. I Sec. 8", "EU AI Act Art. 6")
- severity: high / medium / low
- preemption_language: Verbatim preemption clause if present in the passage
- section_reference: Section number if identifiable
- jurisdiction: State code
- evidence_spans: Array of {field_name, text} where text is a VERBATIM quote

If the passage contains MULTIPLE conflicts, include one object per conflict.

If the passage contains NO preemption signals or jurisdictional conflicts, return:
{"detected": false, "reason": "<describe why no conflicts were found>"}

CRITICAL RULES:
- Every evidence_spans[].text MUST appear VERBATIM in the source passage
- Use abstention (detected: false) rather than speculating about conflicts not supported by the text
- Severity "high" = direct conflict with existing federal law/EO; "medium" = plausible constitutional challenge; "low" = theoretical tension
- Focus on LEGAL conflicts, not policy disagreements
- Savings clauses ("this act does not preempt federal law") are themselves preemption signals worth extracting — they indicate the legislature anticipated a conflict

EVIDENCE SPAN RULES (IMPORTANT — spans are verified by exact string match):
- Copy text EXACTLY as it appears in the passage — same capitalization, same punctuation, same spacing
- Do NOT paraphrase, summarize, or reword the text"""

    def get_extraction_prompt(self, passage: str, context: dict | None = None) -> str:
        prompt = f"""Analyze the following legislative passage for cross-jurisdictional conflicts,
preemption risks, Commerce Clause tensions, or First Amendment concerns.

If there are multiple conflicts, return each as a separate object in
the "extractions" array.

PASSAGE:
---
{passage}
---"""
        if context:
            if context.get("document_title"):
                prompt += f"\n\nDOCUMENT: {context['document_title']}"
            if context.get("jurisdiction"):
                prompt += f"\nJURISDICTION: {context['jurisdiction']}"
            if context.get("key_requirements"):
                prompt += (
                    f"\n\nKEY REQUIREMENTS (from Orrick AI Law Tracker — use as "
                    f"context to identify scope of regulation):\n"
                    f"{context['key_requirements']}"
                )
        prompt = self._append_bill_context(prompt, context)
        return prompt

    def get_output_schema(self) -> type[BaseModel]:
        return PreemptionSignalPayload
