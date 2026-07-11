"""Bill-level Compliance Timeline Agent.

Runs once per law and extracts all compliance deadlines, effective dates,
frequencies, and sequencing information for the entire bill.

Output schema maps to:
  law_obligation_flags.impact_assessment_frequency_months
  law_enforcement_details.cure_period_days
  LawCard deadline sequencing view
"""

from __future__ import annotations

from src.agents.bill_level_base import BillLevelAgent

# SFH-1j (audit B5): same input-targeting fix EA5-3 landed for
# enforcement_agent. Effective-date and phase-in clauses conventionally sit
# at the very END of state bills ("This act takes effect...") — exactly what
# bill_level_base's head truncation drops on long bills. bill_context has no
# timeline-pattern section, so this agent uses a head+tail hybrid: dates can
# open a bill (definitions of compliance periods) and almost always close
# one. Bills at or under 2×window are sent in full, unchanged.
_WINDOW_CHARS = 20_000

_PROMPT_TEMPLATE = """\
You are a legal analyst extracting compliance timeline information from AI legislation.

Analyze the following bill text and extract all key dates, deadlines, and recurring
obligations for the ENTIRE bill.

BILL TEXT:
{full_text}

Extract the following fields as a JSON object.

{{
  "law_effective_date": "YYYY-MM-DD" or null — When the law takes effect,
  "enforcement_start_date": "YYYY-MM-DD" or null — When enforcement begins (may differ from effective date),
  "sunset_date": "YYYY-MM-DD" or null — When the law expires if specified,
  "key_deadlines": [
    {{
      "action": "Plain English description of what must be done",
      "deadline_type": "before_deployment" | "after_enactment" | "recurring" | "event_triggered" | "one_time",
      "relative_days": integer or null — Days relative to trigger event (e.g. 90 for '90 days after'),
      "frequency_months": integer or null — Recurrence interval in months (e.g. 12 for annual),
      "trigger_event": "Description of what triggers this deadline, if event_triggered"
    }}
  ],
  "impact_assessment_frequency_months": integer or null — How often impact/risk assessments must be renewed (12 = annual),
  "consumer_request_response_days": integer or null — Days to respond to a consumer rights request,
  "cure_period_days": integer or null — Days to cure a violation before enforcement action,
  "first_compliance_action": "Description of the first thing a covered entity must do and when"
}}

Rules:
- Output ONLY the JSON object, no explanation or markdown.
- Use ISO 8601 date format (YYYY-MM-DD) for all dates.
- If the bill specifies a year but not month/day, use January 1 of that year.
- If a field is not specified in the bill, use null.
- Include all recurring obligations in key_deadlines (annual reports, periodic assessments, etc).
- Do not invent deadlines not present in the bill text.
"""


class ComplianceTimelineAgent(BillLevelAgent):
    """Extracts compliance deadlines and sequencing from full bill text."""

    agent_name = "compliance_timeline_agent"
    max_tokens_override = 2048

    def get_prompt(self, full_text: str, context: dict) -> str:
        return _PROMPT_TEMPLATE.format(
            full_text=self._build_bill_excerpt(full_text, context)
        )

    @staticmethod
    def _build_bill_excerpt(full_text: str, context: dict) -> str:
        """SFH-1j: timeline input without head-truncation bias (head + tail)."""
        if len(full_text) <= 2 * _WINDOW_CHARS:
            return full_text
        head = full_text[:_WINDOW_CHARS]
        tail = full_text[-_WINDOW_CHARS:]
        return (
            "OPENING-OF-BILL EXCERPT:\n"
            f"{head}\n\n"
            "END-OF-BILL EXCERPT (effective-date and phase-in clauses "
            "conventionally sit here):\n"
            f"{tail}"
        )

    def parse_response(self, raw: str) -> dict:
        data = self._parse_json_payload(raw)

        # Ensure key_deadlines is a list of dicts
        deadlines = data.get("key_deadlines")
        if not isinstance(deadlines, list):
            data["key_deadlines"] = []
        else:
            cleaned = []
            for d in deadlines:
                if isinstance(d, dict):
                    # Coerce relative_days and frequency_months to int
                    for int_field in ("relative_days", "frequency_months"):
                        val = d.get(int_field)
                        if isinstance(val, str):
                            digits = "".join(c for c in val if c.isdigit())
                            d[int_field] = int(digits) if digits else None
                        elif val is not None and not isinstance(val, int):
                            d[int_field] = None
                    cleaned.append(d)
            data["key_deadlines"] = cleaned

        # Coerce top-level int fields
        for int_field in ("impact_assessment_frequency_months",
                          "consumer_request_response_days", "cure_period_days"):
            val = data.get(int_field)
            if isinstance(val, str):
                digits = "".join(c for c in val if c.isdigit())
                data[int_field] = int(digits) if digits else None
            elif val is not None and not isinstance(val, int):
                data[int_field] = None

        return data
