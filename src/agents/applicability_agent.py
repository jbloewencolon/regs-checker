"""Bill-level Applicability Agent.

Runs once per law and produces one structured applicability record covering
who the law applies to, what sectors it covers, what AI system types are in
scope, and what size/revenue thresholds trigger applicability.

Output schema maps to:
  law_triggering_thresholds (covered_entity_types, covered_sectors,
    ai_system_types_in_scope, size_thresholds, key_exemptions)
  fact_laws.government_only
  anonymous_audit_profiles matching engine (matchReasons)
"""

from __future__ import annotations

from src.agents.bill_level_base import BillLevelAgent

_PROMPT_TEMPLATE = """\
You are a legal analyst extracting applicability information from AI legislation.

Analyze the following bill text and extract a single applicability summary for the ENTIRE bill.
This tells a business or individual whether this law applies to them.

BILL TEXT:
{full_text}

Extract the following fields as a JSON object. Use null or empty arrays for fields not specified.

{{
  "covered_entity_types": ["developer" | "deployer" | "provider" | "operator" | "employer" | "contractor" | "state_agency"] — Array of entity types covered by this law,
  "covered_sectors": ["employment" | "housing" | "credit" | "education" | "healthcare" | "insurance" | "criminal_justice" | "financial_services" | "government_services" | "general"] — Sectors in scope,
  "ai_system_types_in_scope": ["high_risk_ai" | "automated_decision_system" | "generative_ai" | "facial_recognition" | "predictive_policing" | "general_purpose_ai" | "algorithmic_system"] — AI system types covered,
  "size_thresholds": {{
    "revenue_usd": integer or null — Annual revenue threshold in USD that triggers applicability,
    "employee_count": integer or null — Employee count threshold,
    "consumer_data_volume": integer or null — Number of consumers whose data is processed,
    "compute_flops": string or null — Compute threshold in FLOPS (e.g. '10^26') if specified
  }},
  "geographic_scope": "Description of geographic applicability (e.g. 'entities doing business in Colorado')",
  "key_exemptions": ["Brief description of each major exemption or carve-out"],
  "government_only": true | false | null — True if the law applies only to government/public sector entities,
  "applicability_summary": "One sentence plain-language summary of who this law applies to"
}}

Rules:
- Output ONLY the JSON object, no explanation or markdown.
- Use exact strings from the lists above for covered_entity_types, covered_sectors, ai_system_types_in_scope.
- If a field is not addressed in the bill, use null or [].
- Do not invent information not present in the bill text.
"""


class ApplicabilityAgent(BillLevelAgent):
    """Extracts who/what/where the law applies to from full bill text."""

    agent_name = "applicability_agent"
    max_tokens_override = 2048

    def get_prompt(self, full_text: str, context: dict) -> str:
        return _PROMPT_TEMPLATE.format(full_text=full_text)

    def parse_response(self, raw: str) -> dict:
        data = self._parse_json_payload(raw)

        # Ensure list fields are lists
        for list_field in ("covered_entity_types", "covered_sectors",
                           "ai_system_types_in_scope", "key_exemptions"):
            val = data.get(list_field)
            if val is None:
                data[list_field] = []
            elif isinstance(val, str):
                data[list_field] = [val] if val else []

        # Ensure size_thresholds is a dict
        if not isinstance(data.get("size_thresholds"), dict):
            data["size_thresholds"] = {
                "revenue_usd": None,
                "employee_count": None,
                "consumer_data_volume": None,
                "compute_flops": None,
            }

        # Coerce int fields in size_thresholds
        thresholds = data["size_thresholds"]
        for int_field in ("revenue_usd", "employee_count", "consumer_data_volume"):
            val = thresholds.get(int_field)
            if isinstance(val, str):
                digits = "".join(c for c in val if c.isdigit())
                thresholds[int_field] = int(digits) if digits else None
            elif val is not None and not isinstance(val, int):
                thresholds[int_field] = None

        # Coerce government_only
        val = data.get("government_only")
        if isinstance(val, str):
            data["government_only"] = val.lower() in ("true", "yes", "1")

        return data
