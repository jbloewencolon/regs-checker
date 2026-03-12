"""Jurisdiction cross-check — validates source document state matches expected law state.

Rejects extractions where the fetched document belongs to a different state than
the law it was mapped to. This prevents wrong-state contamination, which was the
root cause of three known bad document families:
  - Law 188 (SC): Pipeline fetched SC Real Estate Licensing Law instead of AI law
  - Law 159 (NY): Pipeline fetched CT transportation pricing statute instead of NY law
  - Law 60 (CT):  Full text is CT transportation network pricing statute

The cross-check runs during extraction (before LLM calls) and during sync
(before inserting into Policy Navigator's synced_extractions table).

Two strategies:
  1. Metadata check: Compare the source's jurisdiction_code with the document family's
     expected jurisdiction. Fast, no LLM call needed.
  2. Text signal check: Scan passage text for state-specific markers (state names,
     legislature references, statute citation patterns) that contradict the expected
     jurisdiction. Catches mis-fetched documents that metadata alone can't detect.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# State name → code mapping for text-based detection
# ---------------------------------------------------------------------------

STATE_NAME_TO_CODE: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "puerto rico": "PR",
}

# Reverse: code → name (for logging)
STATE_CODE_TO_NAME: dict[str, str] = {v: k.title() for k, v in STATE_NAME_TO_CODE.items()}

# Patterns that strongly indicate a specific state's legislation
_STATE_LEGISLATURE_PATTERNS: dict[str, re.Pattern] = {
    "CT": re.compile(r"(?:connecticut general statutes|conn\.?\s*gen\.?\s*stat|§\s*13b-116)", re.I),
    "NY": re.compile(r"(?:new york .{0,30} law|N\.?Y\.?\s+[A-Z]\.?\s+Law)", re.I),
    "SC": re.compile(r"(?:south carolina code|S\.?C\.?\s+Code\s+Ann|§\s*40-57)", re.I),
    "CO": re.compile(r"(?:colorado revised statutes|C\.?R\.?S\.?\s+§)", re.I),
    "CA": re.compile(r"(?:california civil code|cal\.?\s+civ\.?\s+code|CCPA)", re.I),
}


class JurisdictionMismatch(Exception):
    """Raised when the source document state does not match the expected jurisdiction."""

    def __init__(
        self,
        expected_code: str,
        detected_code: str,
        reason: str,
        document_family_id: int | None = None,
    ):
        self.expected_code = expected_code
        self.detected_code = detected_code
        self.reason = reason
        self.document_family_id = document_family_id
        super().__init__(
            f"Jurisdiction mismatch: expected {expected_code}, "
            f"detected {detected_code}. {reason}"
        )


def check_jurisdiction_metadata(
    expected_jurisdiction: str,
    source_jurisdiction: str,
) -> bool:
    """Fast metadata-based jurisdiction check.

    Compares the document family's source jurisdiction_code with the expected
    jurisdiction. Returns True if they match, False otherwise.
    """
    if not expected_jurisdiction or not source_jurisdiction:
        return True  # Can't check without data — pass through

    return expected_jurisdiction.upper().strip() == source_jurisdiction.upper().strip()


def detect_jurisdiction_from_text(
    text: str,
    sample_size: int = 5000,
) -> str | None:
    """Detect the most likely jurisdiction from passage text.

    Scans the first `sample_size` characters for state names and legislature
    patterns. Returns the two-letter state code with the strongest signal,
    or None if no clear signal is found.

    This is a heuristic — not a definitive classifier. It's meant to catch
    obvious mismatches (e.g., CT statute text for a NY law record).
    """
    sample = text[:sample_size].lower()
    state_mentions: dict[str, int] = {}

    # Count state name mentions
    for state_name, code in STATE_NAME_TO_CODE.items():
        count = len(re.findall(r"\b" + re.escape(state_name) + r"\b", sample))
        if count > 0:
            state_mentions[code] = state_mentions.get(code, 0) + count

    # Check legislature-specific patterns (stronger signal, weighted 3x)
    for code, pattern in _STATE_LEGISLATURE_PATTERNS.items():
        matches = len(pattern.findall(text[:sample_size]))
        if matches > 0:
            state_mentions[code] = state_mentions.get(code, 0) + matches * 3

    if not state_mentions:
        return None

    # Return the state with the strongest signal
    top_code = max(state_mentions, key=state_mentions.get)
    top_count = state_mentions[top_code]

    # Require a minimum signal strength to avoid false positives
    if top_count < 2:
        return None

    return top_code


def validate_extraction_jurisdiction(
    expected_jurisdiction: str,
    source_jurisdiction: str,
    passage_text: str | None = None,
    document_family_id: int | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Full jurisdiction validation combining metadata and text checks.

    Args:
        expected_jurisdiction: The jurisdiction code the law is assigned to.
        source_jurisdiction: The jurisdiction code from the document's source record.
        passage_text: Optional passage text for text-based cross-check.
        document_family_id: Optional doc family ID for error reporting.
        strict: If True, raise JurisdictionMismatch on failure. If False, return result dict.

    Returns:
        Dict with keys: valid (bool), expected, detected, method, reason.

    Raises:
        JurisdictionMismatch: If strict=True and validation fails.
    """
    result: dict[str, Any] = {
        "valid": True,
        "expected": expected_jurisdiction,
        "detected": source_jurisdiction,
        "method": "metadata",
        "reason": None,
    }

    # Step 1: Metadata check
    if not check_jurisdiction_metadata(expected_jurisdiction, source_jurisdiction):
        result["valid"] = False
        result["reason"] = (
            f"Source record jurisdiction ({source_jurisdiction}) does not match "
            f"expected law jurisdiction ({expected_jurisdiction})"
        )
        logger.warning(
            "jurisdiction_mismatch_metadata",
            expected=expected_jurisdiction,
            detected=source_jurisdiction,
            family_id=document_family_id,
        )
        if strict:
            raise JurisdictionMismatch(
                expected_code=expected_jurisdiction,
                detected_code=source_jurisdiction,
                reason=result["reason"],
                document_family_id=document_family_id,
            )
        return result

    # Step 2: Text-based cross-check (if passage text is available)
    if passage_text:
        detected = detect_jurisdiction_from_text(passage_text)
        if detected and detected != expected_jurisdiction.upper():
            result["valid"] = False
            result["detected"] = detected
            result["method"] = "text_signal"
            result["reason"] = (
                f"Passage text signals jurisdiction {detected} "
                f"({STATE_CODE_TO_NAME.get(detected, detected)}), "
                f"but law is assigned to {expected_jurisdiction}"
            )
            logger.warning(
                "jurisdiction_mismatch_text",
                expected=expected_jurisdiction,
                detected=detected,
                family_id=document_family_id,
            )
            if strict:
                raise JurisdictionMismatch(
                    expected_code=expected_jurisdiction,
                    detected_code=detected,
                    reason=result["reason"],
                    document_family_id=document_family_id,
                )
            return result

    return result
