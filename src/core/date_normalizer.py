"""ISO-8601 date normalization for extraction payloads.

Accepts the range of date formats LLMs emit and normalizes to YYYY-MM-DD.
Used by TimelineInfo validators (extraction.py) and the Orrick parser.
Returns None when the input cannot be parsed rather than raising.
"""

from __future__ import annotations

import re

_MONTH_MAP: dict[str, str] = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

_MONTH_PATTERN = (
    r"january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec"
)


def normalize_date(text: str | None) -> str | None:
    """Normalize a date string to ISO-8601 YYYY-MM-DD.

    Handles:
    - Already ISO-8601: ``2026-01-01`` → pass-through
    - Year-month only: ``2026-01`` → ``2026-01-01``
    - Year only:       ``2026``    → ``2026-01-01``
    - Slash notation:  ``1/1/2026`` or ``7/17/34`` → ``2026-01-01`` / ``2034-07-17``
    - Named month:     ``January 1, 2026`` or ``Jan. 1 2026``
    - Month-year:      ``January 2026``
    """
    if not text:
        return None
    text = text.strip()

    # Already full ISO-8601
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text

    # Year-month only (e.g. "2026-01")
    if re.match(r"^\d{4}-\d{2}$", text):
        return text + "-01"

    # Year only (e.g. "2026" or "20\d{2}")
    if re.match(r"^\d{4}$", text) and text.startswith("20"):
        return text + "-01-01"

    # Slash notation: M/D/YYYY, M/D/YY, MM/DD/YYYY
    slash = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$", text)
    if slash:
        m_str, d_str, y_str = slash.groups()
        year = int(y_str)
        if year < 100:
            year += 2000
        try:
            return f"{year:04d}-{int(m_str):02d}-{int(d_str):02d}"
        except ValueError:
            pass

    # Full date: "Month D, YYYY" or "Month D YYYY"
    full = re.search(
        rf"\b({_MONTH_PATTERN})\.?\s+(\d{{1,2}}),?\s+(20\d{{2}})\b",
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
        rf"\b({_MONTH_PATTERN})\.?\s+(20\d{{2}})\b",
        text,
        re.IGNORECASE,
    )
    if month_year:
        month = _MONTH_MAP[month_year.group(1).lower().rstrip(".")]
        year = month_year.group(2)
        return f"{year}-{month}-01"

    # Bare year anywhere in string (e.g. "effective 2026")
    year_only = re.search(r"\b(20\d{2})\b", text)
    if year_only:
        return f"{year_only.group(1)}-01-01"

    return None
