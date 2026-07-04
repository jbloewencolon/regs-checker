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

from src.agents.bill_level_base import BillLevelAgent

# EA5-3: enforcement/penalty sections conventionally sit near the end of
# state AI bills, but bill_level_base.MAX_BILL_TEXT_CHARS truncates
# full_text to a raw prefix — for any bill exceeding that budget, the tail
# (and the enforcement provisions almost always in it) is silently dropped
# before this agent ever sees it (EA0-4 flags the truncation; this closes
# the bias). bill_context["enforcement"] (src/core/bill_context.py) doesn't
# have this bias: it's built by pattern-matching every passage in the bill
# regardless of length or position, so it's the primary input below for
# bills long enough for the distinction to matter. A bounded raw tail is
# still included as a catch-all for enforcement language the pattern
# matcher missed. Short bills (the corpus median is ~11k chars) are sent
# in full, unchanged from prior behavior — there's no truncation-bias risk
# to fix there.
_TAIL_CHARS = 20_000

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
        return _PROMPT_TEMPLATE.format(full_text=self._build_bill_excerpt(full_text, context))

    @staticmethod
    def _build_bill_excerpt(full_text: str, context: dict) -> str:
        if len(full_text) <= _TAIL_CHARS:
            # Whole bill fits in one tail-window — send it all, exactly as
            # before. No prefix/tail distinction to make.
            return full_text

        tail = full_text[-_TAIL_CHARS:]
        enforcement_excerpt = (context or {}).get("enforcement") or ""
        if not enforcement_excerpt:
            # Classifier found no enforcement-pattern passages anywhere in
            # the bill — fall back to the tail alone rather than a raw
            # prefix, since it's the conventional location for enforcement
            # sections and strictly better than guaranteeing they're cut.
            return tail

        return (
            "ENFORCEMENT/PENALTY SECTIONS (matched from across the full bill, "
            "not just a prefix):\n"
            f"{enforcement_excerpt}\n\n"
            "END-OF-BILL EXCERPT (for enforcement language the section above "
            "may have missed):\n"
            f"{tail}"
        )

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
