"""Bill-level Enforcement Agent.

Runs once per law (DocumentVersion) and produces one structured enforcement
record covering the entire bill.  Designed to populate law_enforcement_details
in the Policy Navigator.

Output schema maps directly to:
  law_enforcement_details.max_civil_penalty_usd
  law_enforcement_details.penalty_per
  law_enforcement_details.cure_period_days
  law_enforcement_details.enforcing_body
  law_enforcement_details.private_right_of_action
  law_enforcement_details.criminal_penalties
"""

from __future__ import annotations

import json

from src.agents.bill_level_base import BillLevelAgent

_PROMPT_TEMPLATE = """\
You are a legal analyst extracting enforcement and penalty information from AI legislation.

Analyze the following bill text and extract a single enforcement summary for the ENTIRE bill.

BILL TEXT:
{full_text}

Extract the following fields as a JSON object. Use null for any field not specified in the bill.

{{
  "enforcing_body": "Name of the agency or official responsible for enforcement (e.g. 'Attorney General', 'Department of Commerce')",
  "max_civil_penalty_usd": integer or null — Maximum civil penalty per violation in US dollars. Extract the number only (e.g. 10000, not '$10,000'). If a range is given, use the maximum.,
  "penalty_per": "violation" | "day" | "occurrence" | null — The unit for the penalty (per violation, per day, per occurrence),
  "cure_period_days": integer or null — Number of days to cure a violation before a penalty is assessed,
  "private_right_of_action": true | false | null — Whether the law creates a private right of action for individuals,
  "criminal_penalties": true | false | null — Whether criminal penalties (fines or imprisonment) are possible,
  "criminal_penalty_description": "Brief description of criminal penalties if any, else null",
  "enforcement_text": "A short verbatim quote (under 300 chars) from the bill that best describes the enforcement mechanism"
}}

Rules:
- Output ONLY the JSON object, no explanation or markdown.
- If the bill contains no enforcement provisions, return all fields as null.
- Do not invent numbers — only extract values explicitly stated in the bill text.
"""


class EnforcementAgent(BillLevelAgent):
    """Extracts enforcement/penalty structure from full bill text."""

    agent_name = "enforcement_agent"
    max_tokens_override = 1024

    def get_prompt(self, full_text: str, context: dict) -> str:
        return _PROMPT_TEMPLATE.format(full_text=full_text)

    def parse_response(self, raw: str) -> dict:
        data = self._parse_json_payload(raw)

        # Coerce types — LLMs sometimes return strings for ints
        for int_field in ("max_civil_penalty_usd", "cure_period_days"):
            val = data.get(int_field)
            if isinstance(val, str):
                digits = "".join(c for c in val if c.isdigit())
                data[int_field] = int(digits) if digits else None
            elif val is not None and not isinstance(val, int):
                data[int_field] = None

        for bool_field in ("private_right_of_action", "criminal_penalties"):
            val = data.get(bool_field)
            if isinstance(val, str):
                data[bool_field] = val.lower() in ("true", "yes", "1")

        return data
