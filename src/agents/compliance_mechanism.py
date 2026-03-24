"""Compliance Mechanisms Agent — extracts procedural compliance requirements.

Captures impact assessments, bias audits, registration/certification
requirements, record-keeping mandates, and reporting obligations. These are
procedural obligations with specific structure (who audits, how often, what's
assessed, where results go) that the general Obligation Agent flattens into
generic action strings.

Uses GPT (openai/gpt-oss-20b) because parsing structured procedural detail from
dense legal text requires strong instruction following and precision. GPT's
131k context window also handles full-section assessments where audit scope
spans multiple paragraphs.
"""

from pydantic import BaseModel

from src.agents.base import BaseExtractionAgent
from src.schemas.extraction import ComplianceMechanismPayload


class ComplianceMechanismAgent(BaseExtractionAgent):
    agent_name = "compliance_mechanism"
    model_override = "openai/gpt-oss-20b"

    def get_system_prompt(self) -> str:
        return """You are a legal extraction agent specializing in compliance mechanisms in AI legislation.

Your task is to extract PROCEDURAL compliance requirements from legislative text — the specific
processes, assessments, audits, registrations, and reporting mandates that regulated entities
must follow. These go beyond general obligations by specifying HOW compliance is achieved.

TYPES OF COMPLIANCE MECHANISMS:
- impact_assessment: Required assessments of AI system impact (algorithmic impact, civil rights, privacy)
- bias_audit: Required audits for algorithmic discrimination or bias
- registration: Requirements to register AI systems with a regulatory body
- certification: Requirements to obtain certification before deployment
- record_keeping: Requirements to maintain records, logs, or documentation
- reporting: Requirements to file regular reports with authorities
- disclosure: Requirements to publicly disclose AI system information
- notification: Requirements to notify specific parties of AI use or incidents

OUTPUT FORMAT:
Return a JSON object with a top-level "extractions" array. Each element includes:
- mechanism_type: One of the types listed above
- description: Full description of the compliance requirement
- responsible_party: Who must perform this compliance activity
- responsible_party_normalized: Normalized category (developer, deployer, operator, vendor)
- audits: Array of {audit_type, frequency, assessor, scope, reporting_to, public_disclosure}
- record_retention_period: How long records must be kept (if specified)
- reporting_frequency: How often reports must be filed (if specified)
- reporting_recipient: Who receives compliance reports (if specified)
- section_reference: Section/subsection reference
- jurisdiction: Jurisdiction code if identifiable
- evidence_spans: Array of {field_name, text} where text is a VERBATIM quote from the passage

If the passage contains MULTIPLE compliance mechanisms, include one object per mechanism.

If the passage contains NO extractable compliance mechanisms, return:
{"detected": false, "reason": "<describe why no compliance mechanisms were found>"}

CRITICAL RULES:
- Every evidence_spans[].text MUST appear VERBATIM in the source passage
- Use abstention (detected: false) rather than hallucinating requirements
- Distinguish compliance MECHANISMS (how to comply) from general OBLIGATIONS (what to do)
- Capture specific parameters: frequency, assessor, scope, recipients, retention periods
- "Shall conduct an impact assessment" is a compliance mechanism; "shall not discriminate" is an obligation
- Preserve exact legal language — do not paraphrase

EVIDENCE SPAN RULES (IMPORTANT — spans are verified by exact string match):
- Copy text EXACTLY as it appears in the passage — same capitalization, same punctuation, same spacing
- Do NOT paraphrase, summarize, or reword the text
- Do NOT fix typos, grammar, or formatting in the quoted text

EXAMPLE (for a passage containing "The developer shall complete an algorithmic impact assessment prior to deployment."):
  CORRECT: {"field_name": "text", "text": "The developer shall complete an algorithmic impact assessment prior to deployment."}
  WRONG:   {"field_name": "text", "text": "Developers must complete algorithmic impact assessments before deployment."}
The second is wrong because it paraphrases instead of quoting verbatim."""

    def get_extraction_prompt(self, passage: str, context: dict | None = None) -> str:
        prompt = f"""Extract all compliance mechanisms from the following legislative passage.
Focus on procedural requirements: assessments, audits, registrations, certifications,
record-keeping, reporting mandates, and disclosure requirements.

Capture specific parameters when stated: who performs the assessment, how often, what is
assessed, who receives results, and how long records must be retained.

If there are multiple compliance mechanisms, return each as a separate object in the
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
            if context.get("ai_scope"):
                prompt += (
                    f"\n\nAI SCOPE (helps identify which AI systems these "
                    f"compliance mechanisms apply to):\n{context['ai_scope']}"
                )
        prompt = self._append_bill_context(prompt, context)
        return prompt

    def get_output_schema(self) -> type[BaseModel]:
        return ComplianceMechanismPayload
