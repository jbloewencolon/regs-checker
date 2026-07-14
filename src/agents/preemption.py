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

import structlog
from pydantic import BaseModel

from src.agents.base import BaseExtractionAgent
from src.core.legal_context import assess_preemption_credibility
from src.schemas.extraction import PreemptionSignalPayload

logger = structlog.get_logger()


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
- related_authority: The preempting or conflicting authority, EXACTLY as the passage names it (a federal statute citation, a named federal act or executive order, or another state's law). Use null if the passage names no such authority — NEVER invent one.
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
- A citation to the SAME state's own codes (e.g., a California bill referencing the California Penal Code) is a cross-law reference, NOT a jurisdictional conflict — do not report it
- A cross_state_conflict requires a specific OTHER jurisdiction's law; "may conflict with other states' laws" with no named law is speculation — abstain instead
- If you conclude a passage does NOT conflict, that is an abstention (detected: false), not a signal to report

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

    def _postprocess_extraction(self, result: dict, passage: str) -> dict | None:
        """QA-6: drop conflict assertions the payload cannot support.

        The 2026-07-13 run produced 81 preemption signals from the 8B model,
        dominated by three deterministic junk patterns: (1) descriptions that
        negate themselves ("...does not appear to conflict with federal
        law"), (2) the prompt's example authorities parroted into
        related_authority ("Dec 2025 Federal EO on AI"), and (3) the law's
        own state codes reported as a cross_state_conflict ("references the
        Penal Code, which may conflict with other states' laws"). The shared
        credibility assessment in legal_context keeps only signals anchored
        to a verbatim preemption clause, a concrete federal citation, or a
        named other state; the same assessment hides stored rows at sync
        time.

        One extraction-time-only strengthening: a preemption_language that
        does not appear in the passage is a fabricated clause — null it
        before assessing, so it cannot rubber-stamp credibility.
        """
        from src.core.text_grounding import _loose_normalize

        pl = (result.get("preemption_language") or "").strip()
        if pl:
            loose_pl, _ = _loose_normalize(pl)
            loose_passage, _ = _loose_normalize(passage)
            if loose_pl and loose_pl not in loose_passage:
                logger.warning(
                    "preemption_language_not_in_passage_nulled",
                    conflict_type=result.get("conflict_type"),
                    preemption_language=pl[:120],
                )
                result["preemption_language"] = None

        credibility = assess_preemption_credibility(result)
        if not credibility["credible"]:
            logger.warning(
                "preemption_signal_dropped",
                reason=credibility["reason"],
                conflict_type=result.get("conflict_type"),
                related_authority=result.get("related_authority"),
                description=(result.get("description") or "")[:120],
            )
            return None
        return result
