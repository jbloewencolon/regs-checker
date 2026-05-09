"""Deterministic regex parser for structured facts from Orrick tracker text.

Parses `key_requirements` and `enforcement_penalties` free-text fields from
the Orrick AI Tracker into structured dicts that map to the three bill-level
agent output schemas (enforcement, applicability, compliance_timeline).

Used by _run_bill_level_agents() to skip LLM calls when Orrick already
supplies the data for an agent's domain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _clean_digits(s: str) -> int | None:
    """Extract integer from a string like '$10,000' or '10000'."""
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None


def _parse_dollar_amount(text: str) -> int | None:
    """Find the largest USD amount in text (handles 'million', 'k' suffixes)."""
    # Match patterns like $10,000, $1.5 million, $500k
    pattern = re.compile(
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|m\b|billion|b\b|thousand|k\b)?",
        re.IGNORECASE,
    )
    amounts = []
    for m in pattern.finditer(text):
        raw = float(m.group(1).replace(",", ""))
        suffix = (m.group(2) or "").lower()
        if suffix in ("million", "m"):
            raw *= 1_000_000
        elif suffix in ("billion", "b"):
            raw *= 1_000_000_000
        elif suffix in ("thousand", "k"):
            raw *= 1_000
        amounts.append(int(raw))
    return max(amounts) if amounts else None


def _parse_date(text: str) -> str | None:
    """Parse dates like 'January 1, 2026' or '2026' → ISO 8601 YYYY-MM-DD."""
    # Full date: Month D, YYYY or Month YYYY
    full = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|"
        r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
        r"\.?\s+(\d{1,2}),?\s+(20\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if full:
        month = _MONTH_MAP[full.group(1).lower().rstrip(".")]
        day = full.group(2).zfill(2)
        year = full.group(3)
        return f"{year}-{month}-{day}"

    # Month YYYY (no day)
    month_year = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|"
        r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
        r"\.?\s+(20\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if month_year:
        month = _MONTH_MAP[month_year.group(1).lower().rstrip(".")]
        year = month_year.group(2)
        return f"{year}-{month}-01"

    # Just a year
    year_only = re.search(r"\b(20\d{2})\b", text)
    if year_only:
        return f"{year_only.group(1)}-01-01"

    return None


# ---------------------------------------------------------------------------
# Enforcement parsing
# ---------------------------------------------------------------------------

_ENFORCING_BODY_PATTERNS = [
    (r"\battorney\s+general\b", "Attorney General"),
    (r"\bdepartment\s+of\s+commerce\b", "Department of Commerce"),
    (r"\bdepartment\s+of\s+justice\b", "Department of Justice"),
    (r"\bftc\b|federal\s+trade\s+commission", "FTC"),
    (r"\bcppa\b|california\s+privacy\s+protection\s+agency", "CPPA"),
    (r"\bdepartment\s+of\s+labor\b", "Department of Labor"),
    (r"\bstate\s+attorney\s+general\b", "State Attorney General"),
    (r"\bsuperintendent\b", "Superintendent"),
    (r"\bcommissioner\b", "Commissioner"),
    (r"\bdivision\s+of\s+consumer\s+protection\b", "Division of Consumer Protection"),
]

_PENALTY_PER_PATTERNS = [
    (r"per\s+day\b|per-day\b", "day"),
    (r"per\s+violation\b|per-violation\b", "violation"),
    (r"per\s+occurrence\b|per-occurrence\b", "occurrence"),
]

_PRIVATE_RIGHT_PATTERNS = [
    (r"no\s+private\s+right\s+of\s+action", False),
    (r"private\s+right\s+of\s+action", True),
]

_CRIMINAL_PATTERNS = [
    (r"no\s+criminal\s+penalt", False),
    (r"criminal\s+penalt|class\s+[a-z]\s+(?:felony|misdemeanor)|imprisonment|jail", True),
]

_CURE_PATTERN = re.compile(
    r"(\d+)[-\s]?day(?:s)?\s+(?:cure|notice|opportunity\s+to\s+cure|to\s+cure)\b"
    r"|cure\s+period\s+of\s+(\d+)\s+days?",
    re.IGNORECASE,
)


def parse_enforcement_facts(enforcement_text: str) -> dict[str, Any]:
    """Extract structured enforcement facts from Orrick enforcement_penalties text."""
    t = enforcement_text or ""
    result: dict[str, Any] = {
        "max_civil_penalty_usd": None,
        "penalty_per": None,
        "cure_period_days": None,
        "enforcing_body": None,
        "private_right_of_action": None,
        "criminal_penalties": None,
        "enforcement_text": t[:300] if t else None,
        "_source": "orrick",
    }

    if not t.strip():
        return result

    # Dollar amount — use the largest found (most likely the max penalty)
    result["max_civil_penalty_usd"] = _parse_dollar_amount(t)

    # Penalty period
    for pat, val in _PENALTY_PER_PATTERNS:
        if re.search(pat, t, re.IGNORECASE):
            result["penalty_per"] = val
            break

    # Cure period
    cure_m = _CURE_PATTERN.search(t)
    if cure_m:
        days_str = cure_m.group(1) or cure_m.group(2)
        result["cure_period_days"] = int(days_str) if days_str else None

    # Enforcing body
    for pat, name in _ENFORCING_BODY_PATTERNS:
        if re.search(pat, t, re.IGNORECASE):
            result["enforcing_body"] = name
            break

    # Private right of action
    for pat, val in _PRIVATE_RIGHT_PATTERNS:
        if re.search(pat, t, re.IGNORECASE):
            result["private_right_of_action"] = val
            break

    # Criminal penalties
    for pat, val in _CRIMINAL_PATTERNS:
        if re.search(pat, t, re.IGNORECASE):
            result["criminal_penalties"] = val
            break

    return result


def enforcement_coverage(facts: dict) -> bool:
    """Return True if Orrick enforcement facts are sufficient to skip the LLM agent."""
    return bool(
        facts.get("max_civil_penalty_usd") is not None
        or facts.get("enforcing_body") is not None
        or facts.get("private_right_of_action") is not None
    )


# ---------------------------------------------------------------------------
# Applicability parsing
# ---------------------------------------------------------------------------

_ENTITY_TYPE_PATTERNS = [
    (r"\bdeveloper[s]?\b", "developer"),
    (r"\bdeployer[s]?\b", "deployer"),
    (r"\bprovider[s]?\b", "provider"),
    (r"\boperator[s]?\b", "operator"),
    (r"\bemployer[s]?\b", "employer"),
    (r"\bcontractor[s]?\b", "contractor"),
    (r"\bstate\s+agenc(?:y|ies)\b", "state_agency"),
]

_SECTOR_PATTERNS = [
    (r"\bemployment\b", "employment"),
    (r"\bhousing\b", "housing"),
    (r"\bcredit\b|\blending\b|\bloan[s]?\b", "credit"),
    (r"\beducation\b|\bschool[s]?\b|\buniversit(?:y|ies)\b", "education"),
    (r"\bhealthcare\b|\bhealth\s+care\b|\bmedical\b|\bhospital[s]?\b", "healthcare"),
    (r"\binsurance\b", "insurance"),
    (r"\bcriminal\s+justice\b|\blaw\s+enforcement\b|\bpolic(?:e|ing)\b", "criminal_justice"),
    (r"\bfinancial\s+services\b|\bbanking\b|\bfintech\b", "financial_services"),
    (r"\bgovernment\s+services\b|\bpublic\s+services\b", "government_services"),
]

_EMPLOYEE_THRESHOLD_PATTERN = re.compile(
    r"(\d[\d,]*)\s*(?:or\s+more|[+]|and\s+over)?\s*(?:full[- ]time\s+)?employee[s]?",
    re.IGNORECASE,
)
_REVENUE_THRESHOLD_PATTERN = re.compile(
    # "revenue of $X" or "$X in revenue" or "$X annual revenue"
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|m\b|billion|b\b|thousand|k\b)?"
    r"(?:[^.;]{0,40}?(?:revenue|receipts))"
    r"|(?:revenue|receipts)[^\$\d]{0,20}\$\s*([\d,]+(?:\.\d+)?)\s*(million|m\b|billion|b\b|thousand|k\b)?",
    re.IGNORECASE,
)
_CONSUMER_VOLUME_PATTERN = re.compile(
    r"(\d[\d,]*)\s*(?:or\s+more\s+)?(?:consumers?|individuals?|residents?|users?)",
    re.IGNORECASE,
)


def parse_applicability_facts(key_requirements_text: str) -> dict[str, Any]:
    """Extract structured applicability facts from Orrick key_requirements text."""
    t = key_requirements_text or ""
    result: dict[str, Any] = {
        "covered_entity_types": [],
        "covered_sectors": [],
        "size_thresholds": {
            "revenue_usd": None,
            "employee_count": None,
            "consumer_data_volume": None,
            "compute_flops": None,
        },
        "government_only": None,
        "applicability_summary": t[:500] if t else None,
        "_source": "orrick",
    }

    if not t.strip():
        return result

    # Entity types
    for pat, label in _ENTITY_TYPE_PATTERNS:
        if re.search(pat, t, re.IGNORECASE):
            result["covered_entity_types"].append(label)

    # Sectors
    for pat, label in _SECTOR_PATTERNS:
        if re.search(pat, t, re.IGNORECASE):
            result["covered_sectors"].append(label)

    # Employee threshold — take smallest (most likely the trigger)
    employee_matches = [
        _clean_digits(m.group(1)) for m in _EMPLOYEE_THRESHOLD_PATTERN.finditer(t)
    ]
    employee_matches = [v for v in employee_matches if v is not None and v > 0]
    if employee_matches:
        result["size_thresholds"]["employee_count"] = min(employee_matches)

    # Revenue threshold — pattern has two alternations; groups 1+2 for "$X...revenue", 3+4 for "revenue...$X"
    rev_m = _REVENUE_THRESHOLD_PATTERN.search(t)
    if rev_m:
        amount_str = rev_m.group(1) or rev_m.group(3) or ""
        suffix_str = (rev_m.group(2) or rev_m.group(4) or "").lower()
        if amount_str:
            raw = float(amount_str.replace(",", ""))
            if suffix_str in ("million", "m"):
                raw *= 1_000_000
            elif suffix_str in ("billion", "b"):
                raw *= 1_000_000_000
            elif suffix_str in ("thousand", "k"):
                raw *= 1_000
            result["size_thresholds"]["revenue_usd"] = int(raw)

    # Consumer data volume — take smallest
    consumer_matches = [
        _clean_digits(m.group(1)) for m in _CONSUMER_VOLUME_PATTERN.finditer(t)
    ]
    consumer_matches = [v for v in consumer_matches if v is not None and v > 0]
    if consumer_matches:
        result["size_thresholds"]["consumer_data_volume"] = min(consumer_matches)

    # Government-only detection
    if re.search(r"\bgovernment\s+only\b|\bonly\s+(?:applies?\s+to\s+)?(?:government|public\s+sector)", t, re.IGNORECASE):
        result["government_only"] = True
    elif re.search(r"\bprivate\s+sector\b|\bprivate\s+entit(?:y|ies)\b|\bcommercial\b", t, re.IGNORECASE):
        result["government_only"] = False

    return result


def applicability_coverage(facts: dict) -> bool:
    """Return True if Orrick applicability facts are sufficient to skip the LLM agent."""
    return bool(
        facts.get("covered_entity_types")
        or facts["size_thresholds"].get("employee_count") is not None
        or facts["size_thresholds"].get("revenue_usd") is not None
        or facts["size_thresholds"].get("consumer_data_volume") is not None
    )


# ---------------------------------------------------------------------------
# Compliance timeline parsing
# ---------------------------------------------------------------------------

_EFFECTIVE_DATE_PATTERN = re.compile(
    r"(?:effective|takes?\s+effect|effective\s+date[:\s]+)"
    r"\s*(?:on\s+|upon\s+)?([^\n.;]{5,60})",
    re.IGNORECASE,
)

_UPON_ENACTMENT_PATTERN = re.compile(
    r"effective\s+upon\s+(?:enactment|passage|signing|approval)",
    re.IGNORECASE,
)

_ENFORCEMENT_START_PATTERN = re.compile(
    r"(?:enforcement\s+(?:begins?|starts?|commences?|effective)|"
    r"compliance\s+(?:required|deadline)[:\s]+)"
    r"\s*([^\n.;]{5,60})",
    re.IGNORECASE,
)

_RESPONSE_DAYS_PATTERN = re.compile(
    r"(\d+)\s*(?:calendar\s+|business\s+)?days?\s+(?:to\s+)?(?:respond|response|comply|complete)",
    re.IGNORECASE,
)

_ASSESSMENT_FREQ_PATTERN = re.compile(
    r"(?:annual|annually|(?:every|each)\s+year|yearly)",
    re.IGNORECASE,
)


def parse_timeline_facts(key_requirements_text: str, enforcement_text: str = "") -> dict[str, Any]:
    """Extract structured compliance timeline facts from Orrick text."""
    t = (key_requirements_text or "") + " " + (enforcement_text or "")
    result: dict[str, Any] = {
        "law_effective_date": None,
        "enforcement_start_date": None,
        "key_deadlines": [],
        "impact_assessment_frequency_months": None,
        "consumer_request_response_days": None,
        "cure_period_days": None,
        "first_compliance_action": None,
        "_source": "orrick",
    }

    if not t.strip():
        return result

    # Effective upon enactment — no specific date
    if _UPON_ENACTMENT_PATTERN.search(t):
        result["law_effective_date"] = "upon_enactment"

    # Effective date with a real date
    eff_m = _EFFECTIVE_DATE_PATTERN.search(t)
    if eff_m and result["law_effective_date"] is None:
        date_fragment = eff_m.group(1)
        parsed = _parse_date(date_fragment)
        if parsed:
            result["law_effective_date"] = parsed

    # Enforcement start date (may differ from effective)
    enf_m = _ENFORCEMENT_START_PATTERN.search(t)
    if enf_m:
        date_fragment = enf_m.group(1)
        parsed = _parse_date(date_fragment)
        if parsed:
            result["enforcement_start_date"] = parsed

    # Consumer response days
    resp_m = _RESPONSE_DAYS_PATTERN.search(t)
    if resp_m:
        result["consumer_request_response_days"] = int(resp_m.group(1))

    # Cure period (shared extraction)
    cure_m = _CURE_PATTERN.search(t)
    if cure_m:
        days_str = cure_m.group(1) or cure_m.group(2)
        result["cure_period_days"] = int(days_str) if days_str else None

    # Annual assessment frequency
    if _ASSESSMENT_FREQ_PATTERN.search(t):
        result["impact_assessment_frequency_months"] = 12

    return result


def timeline_coverage(facts: dict) -> bool:
    """Return True if Orrick timeline facts are sufficient to skip the LLM agent."""
    return bool(facts.get("law_effective_date") is not None)


# ---------------------------------------------------------------------------
# Top-level public API
# ---------------------------------------------------------------------------

@dataclass
class OrrickFacts:
    """Structured facts parsed from Orrick tracker text."""

    enforcement: dict[str, Any] = field(default_factory=dict)
    applicability: dict[str, Any] = field(default_factory=dict)
    timeline: dict[str, Any] = field(default_factory=dict)

    enforcement_covered: bool = False
    applicability_covered: bool = False
    timeline_covered: bool = False


def parse_orrick_facts(bill_context: dict) -> OrrickFacts:
    """Parse all structured facts from bill_context Orrick fields.

    Args:
        bill_context: The context dict built by _build_passage_context(), which
            includes key_requirements and enforcement_summary from Orrick.

    Returns:
        OrrickFacts with parsed data and per-domain coverage flags.
    """
    key_reqs = bill_context.get("key_requirements", "")
    enforcement_text = bill_context.get("enforcement_summary", "")

    enf_facts = parse_enforcement_facts(enforcement_text)
    app_facts = parse_applicability_facts(key_reqs)
    tl_facts = parse_timeline_facts(key_reqs, enforcement_text)

    facts = OrrickFacts(
        enforcement=enf_facts,
        applicability=app_facts,
        timeline=tl_facts,
        enforcement_covered=enforcement_coverage(enf_facts),
        applicability_covered=applicability_coverage(app_facts),
        timeline_covered=timeline_coverage(tl_facts),
    )

    logger.debug(
        "orrick_facts_parsed",
        enforcement_covered=facts.enforcement_covered,
        applicability_covered=facts.applicability_covered,
        timeline_covered=facts.timeline_covered,
        has_penalty=enf_facts.get("max_civil_penalty_usd") is not None,
        has_entity_types=bool(app_facts.get("covered_entity_types")),
        has_effective_date=bool(tl_facts.get("law_effective_date")),
    )

    return facts
