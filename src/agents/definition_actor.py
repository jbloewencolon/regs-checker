"""Definition & Actor Agent — consolidated from definition + actor_mapping + framework_ref.

Co-extracts definitions, actor role mappings, and framework references in a
single pass because all are "what do the words mean" tasks operating on
preamble/definitions sections (Recommendation #1).
"""

import structlog
from pydantic import BaseModel

from src.agents.base import BaseExtractionAgent
from src.core.text_grounding import _loose_normalize
from src.schemas.extraction import DefinitionActorPayload

logger = structlog.get_logger()


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

    def _postprocess_extraction(self, result: dict, passage: str) -> dict:
        """QA-2: drop actors and framework_refs not grounded in the definition.

        Small instruct models fill the schema's optional arrays with invented
        content: a "Developer" actor attached to a definition that names no
        actor, or a NIST framework_ref cross-contaminated from a *different*
        definition in the same passage. Both patterns share one property —
        the invented name does not appear anywhere in this definition's own
        text. Grounding is checked against the definition context (term +
        definition_text + scope), NOT the whole passage: the schema's
        contract is "actor roles mentioned in this definition context", and
        the observed hallucinations DO appear elsewhere in the passage, so a
        passage-level check would not catch them.
        """
        grounding_source = " ".join(
            str(result.get(k) or "") for k in ("term", "definition_text", "scope")
        )
        grounding, _ = _loose_normalize(grounding_source)
        if not grounding:
            return result

        def _grounded(name: str) -> bool:
            loose_name, _ = _loose_normalize(name)
            if not loose_name:
                return False
            if loose_name in grounding:
                return True
            # Multi-word names (e.g. "National Institute of Standards and
            # Technology") tolerate partial quoting: grounded when at least
            # half of the significant tokens appear.
            tokens = [t for t in loose_name.split() if len(t) >= 4]
            if len(tokens) >= 2:
                hits = sum(1 for t in tokens if t in grounding)
                return hits >= (len(tokens) + 1) // 2
            return False

        kept_actors = []
        for actor in result.get("actors") or []:
            if _grounded(actor.get("actor_name", "")):
                kept_actors.append(actor)
            else:
                logger.warning(
                    "definition_actor_ungrounded_actor_dropped",
                    term=result.get("term"),
                    actor_name=actor.get("actor_name"),
                )
        result["actors"] = kept_actors

        kept_refs = []
        for ref in result.get("framework_refs") or []:
            if _grounded(ref.get("framework_name", "")):
                kept_refs.append(ref)
            else:
                logger.warning(
                    "definition_actor_ungrounded_framework_ref_dropped",
                    term=result.get("term"),
                    framework_name=ref.get("framework_name"),
                )
        result["framework_refs"] = kept_refs
        return result
