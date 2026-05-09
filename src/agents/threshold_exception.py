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
        return """You are a legal extraction agent specializing in thresholds and exceptions in AI legislation.

Your task is to extract boundary conditions from legislative text and classify each into
one of three sub-types:

SCOPE THRESHOLDS (threshold_sub_type: "scope")
Conditions that determine WHO or WHAT the law applies to based on size, volume, or sector:
- Entity size: revenue (revenue_threshold_usd), employee count (employee_threshold),
  consumers' data processed (consumer_data_threshold)
- Compute: FLOPS thresholds for model training (compute_flops + compute_description)
- Sector: consequential decision sectors (sector_applicability)
- Entity type: developer-only, deployer-only, government-only

TEMPORAL THRESHOLDS (threshold_sub_type: "temporal")
Conditions that determine WHEN an obligation kicks in:
- "applies only after January 1, 2026", "effective 18 months after enactment"
- Note: purely administrative deadlines ("report within 30 days") belong in compliance_mechanism.
  Extract temporal thresholds when they gate WHETHER an obligation applies at all.

EXEMPTIONS (threshold_sub_type: "exemption")
Conditions under which the law does NOT apply:
- "does not apply to small businesses", "excludes nonprofits", "except for research use"
- Safe harbors that shield entities from liability

OUTPUT FORMAT:
Return a JSON object with a top-level "extractions" array. Each element includes:
- threshold_sub_type: "scope" | "temporal" | "exemption" | "other" — ALWAYS set this
- threshold_type: Specific type (numeric, categorical, monetary, date, compute, entity_type,
  sector, carve_out, safe_harbor, use_exemption, etc.)
- threshold_value: The threshold value as a string (e.g. "50", "25000000")
- threshold_unit: Unit (employees, USD, consumers, months, FLOPS, etc.)
- threshold_condition: The full condition expression
- applies_to_obligation: Which obligation this threshold modifies
- exceptions: Array of {exception_type, description, conditions, scope} — for exemption sub-type
- compute_flops: Numeric FLOPS value (e.g., 1e26). Null if not a compute threshold.
- compute_description: Human-readable compute threshold text. Null if not applicable.
- sector_applicability: Array of sectors: "healthcare", "employment", "credit", "housing",
  "insurance", "criminal_justice", "education", "government". Null if not sector-specific.
- revenue_threshold_usd: Integer USD for revenue scope thresholds. Null otherwise.
- employee_threshold: Integer employee count for size scope thresholds. Null otherwise.
- consumer_data_threshold: Integer consumer count for data-volume scope thresholds. Null otherwise.
- evidence_spans: Array of {field_name, text} where text is VERBATIM from the passage

If the passage contains MULTIPLE boundary conditions, include one object per condition.

If the passage contains NO extractable thresholds or exceptions, return:
{"detected": false, "reason": "<describe why>"}

CRITICAL RULES:
- Every evidence_spans[].text MUST appear VERBATIM in the source passage
- Always set threshold_sub_type — never leave it null in an extraction
- For scope thresholds: populate revenue_threshold_usd, employee_threshold, or
  consumer_data_threshold as integers when the bill states them explicitly
- Copy evidence spans EXACTLY — same capitalization, punctuation, spacing; do NOT paraphrase

EXAMPLE (passage: "This section does not apply to a covered entity that employs fewer than 50 employees."):
  CORRECT: {"threshold_sub_type": "exemption", "threshold_type": "numeric", "threshold_value": "50",
    "threshold_unit": "employees", "employee_threshold": 50,
    "evidence_spans": [{"field_name": "threshold_condition",
      "text": "This section does not apply to a covered entity that employs fewer than 50 employees."}]}"""

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
        prompt = self._append_bill_context(prompt, context)
        return prompt

    def get_output_schema(self) -> type[BaseModel]:
        return ThresholdExceptionPayload
