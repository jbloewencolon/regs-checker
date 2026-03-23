"""Definition & Actor Agent — consolidated from definition + actor_mapping + framework_ref.

Co-extracts definitions, actor role mappings, and framework references in a
single pass because all are "what do the words mean" tasks operating on
preamble/definitions sections (Recommendation #1).
"""

from pydantic import BaseModel

from src.agents.base import BaseExtractionAgent
from src.schemas.extraction import DefinitionActorPayload


class DefinitionActorAgent(BaseExtractionAgent):
    agent_name = "definition_actor"
    model_override = "openai/gpt-oss-20b"

    def get_system_prompt(self) -> str:
        return """You are a legal extraction agent specializing in definitions, actor roles, and framework references.

Your task is to extract structured definition data from legislative text passages.
You MUST co-extract:
1. The defined term and its full definition text
2. Any actor roles mentioned (e.g., "developer", "deployer", "regulator") with their responsibilities
3. Any references to external frameworks or standards (e.g., NIST AI RMF, ISO standards)

OUTPUT FORMAT:
Return a JSON object with a top-level "extractions" array. Each element includes:
- term: The defined term
- definition_text: The full definition
- scope: Scope or applicability of the definition
- cross_references: List of other sections that reference this definition
- actors: Array of {actor_name, actor_type, responsibilities}
- framework_refs: Array of {framework_name, section_or_standard, relationship}
- evidence_spans: Array of {field_name, text} where text is a VERBATIM quote from the passage

If the passage defines MULTIPLE terms, include one object per definition.

If the passage contains NO extractable definition, return:
{"detected": false, "reason": "<describe why no definitions were found>"}

CRITICAL RULES:
- Every evidence_spans[].text MUST appear VERBATIM in the source passage
- Use abstention (detected: false) rather than hallucinating definitions
- Preserve the exact legal language of definitions — do not paraphrase
- Capture the full definition, not a summary

EVIDENCE SPAN RULES (IMPORTANT — spans are verified by exact string match):
- Copy text EXACTLY as it appears in the passage — same capitalization, same punctuation, same spacing
- Do NOT paraphrase, summarize, or reword the text
- Do NOT fix typos, grammar, or formatting in the quoted text

EXAMPLE (for a passage containing "'Artificial intelligence system' means a machine-based system that generates outputs."):
  CORRECT: {"field_name": "text", "text": "'Artificial intelligence system' means a machine-based system that generates outputs."}
  WRONG:   {"field_name": "text", "text": "Artificial intelligence system: a machine-based system that generates outputs."}
The second is wrong because it reformats the definition instead of quoting verbatim."""

    def get_extraction_prompt(self, passage: str, context: dict | None = None) -> str:
        prompt = f"""Extract all definitions, actor role mappings, and framework references from
the following legislative passage.

If there are multiple definitions, return each as a separate object in the
"extractions" array.

PASSAGE:
---
{passage}
---"""
        if context:
            if context.get("document_title"):
                prompt += f"\n\nDOCUMENT: {context['document_title']}"
            if context.get("section_path"):
                prompt += f"\nSECTION: {context['section_path']}"
            if context.get("key_requirements"):
                prompt += (
                    f"\n\nKEY REQUIREMENTS (from Orrick AI Law Tracker — use as "
                    f"context to improve extraction accuracy):\n{context['key_requirements']}"
                )
        prompt = self._append_bill_context(prompt, context)
        return prompt

    def get_output_schema(self) -> type[BaseModel]:
        return DefinitionActorPayload
