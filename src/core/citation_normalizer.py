"""Jurisdiction-aware statutory citation normalizer (RR4c).

Maps raw citation strings from LLM extraction payloads to canonical forms
that can be matched against NormalizedSourceRecord.section_path values.

Per-state patterns:
  CO  — C.R.S. § / Colo. Rev. Stat. § / § XX-XX-XXX
  CA  — Cal. [Code] Code § / Cal. Bus. & Prof. Code §
  NY  — N.Y. [Code] Law § / New York Laws Ch. XXXX
  TX  — Tex. [Code] Code § / V.T.C.A. [Code] §
  CT  — C.G.S. § / Conn. Gen. Stat. §
  IL  — XXX ILCS XXX/X.X / X Ill. Comp. Stat. §
  UT  — Utah Code Ann. § / § XX-XX-XXX
  Federal — Pub. L. No. XX-XX / XX U.S.C. § / E.O. XXXX
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Per-jurisdiction prefix normalizers
# These strip jurisdiction-specific boilerplate and return a bare reference
# that looks like "§ 6-1-1702(3)(a)" or "Section 4" — matching the parser's
# section_path format.
# ---------------------------------------------------------------------------

_CO_PATTERNS = [
    # C.R.S. § 6-1-1702  →  § 6-1-1702
    (re.compile(r"C\.R\.S\.?\s*§\s*", re.I), "§ "),
    (re.compile(r"Colo(?:rado)?\.?\s+Rev\.?\s+Stat\.?\s*§?\s*", re.I), "§ "),
]

_CA_PATTERNS = [
    # Cal. Bus. & Prof. Code § 22575  →  § 22575
    (re.compile(r"Cal(?:ifornia)?\.?\s+\w[\w\s&\.]*\s+Code\s*§\s*", re.I), "§ "),
    # Cal. Code Regs. tit. 11, § 999.302  →  § 999.302
    (re.compile(r"Cal(?:ifornia)?\.?\s+Code\s+Regs\.?\s+tit\.?\s*\d+[,\s]*§\s*", re.I), "§ "),
]

_NY_PATTERNS = [
    # N.Y. Gen. Bus. Law § 899-aa  →  § 899-aa
    (re.compile(r"N\.Y\.?\s+[\w\s\.&]+Law\s*§\s*", re.I), "§ "),
    # New York Laws Chapter 202 of 2021  →  Section Chapter 202 of 2021
    (re.compile(r"New\s+York\s+Laws\s+", re.I), "Section "),
]

_TX_PATTERNS = [
    # Tex. Bus. & Com. Code § 503.001  →  § 503.001
    (re.compile(r"Tex(?:as)?\.?\s+[\w\s&\.]+\s+Code\s*(?:Ann\.?)?\s*§\s*", re.I), "§ "),
    # V.T.C.A. Bus. & Com. § 503.001  →  § 503.001
    (re.compile(r"V\.T\.C\.A\.?\s+[\w\s&\.]*§\s*", re.I), "§ "),
]

_CT_PATTERNS = [
    # C.G.S. § 36a-701b  →  § 36a-701b
    (re.compile(r"C\.G\.S\.?\s*§\s*", re.I), "§ "),
    (re.compile(r"Conn(?:ecticut)?\.?\s+Gen\.?\s+Stat\.?\s*§?\s*", re.I), "§ "),
]

_IL_PATTERNS = [
    # 820 ILCS 5/820-110  →  § 820-110
    (re.compile(r"\d+\s+ILCS\s+[\d/\.\-]+", re.I), lambda m: "§ " + m.group(0).split("/")[-1]),
    # 5 Ill. Comp. Stat. 820/820-110  →  § 820-110
    (re.compile(r"\d+\s+Ill\.?\s+Comp\.?\s+Stat\.?\s*[\d/]*\s*§?\s*", re.I), "§ "),
]

_UT_PATTERNS = [
    # Utah Code Ann. § 13-37-201  →  § 13-37-201
    (re.compile(r"Utah\s+Code\s+(?:Ann\.?)?\s*§?\s*", re.I), "§ "),
]

_FEDERAL_PATTERNS = [
    # 15 U.S.C. § 6501  →  § 6501
    (re.compile(r"\d+\s+U\.S\.C\.?\s*§\s*", re.I), "§ "),
    # Pub. L. No. 115-96  →  Pub. L. No. 115-96 (kept as-is — no section_path match expected)
    # E.O. 13859  →  E.O. 13859 (kept as-is)
]

_JURISDICTION_PATTERNS: dict[str, list] = {
    "CO": _CO_PATTERNS,
    "CA": _CA_PATTERNS,
    "NY": _NY_PATTERNS,
    "TX": _TX_PATTERNS,
    "CT": _CT_PATTERNS,
    "IL": _IL_PATTERNS,
    "UT": _UT_PATTERNS,
    "US": _FEDERAL_PATTERNS,
}

# ---------------------------------------------------------------------------
# Generic cleanup applied after jurisdiction-specific stripping
# ---------------------------------------------------------------------------

_GENERIC_PREFIX_PATTERNS = [
    # "Section 4(a)(2)" → "§ 4(a)(2)"
    (re.compile(r"^(?:Sec(?:tion)?|Sec\.)\s+", re.I), "§ "),
    # "Subsection 4(a)" → "§ 4(a)"
    (re.compile(r"^Subsection\s+", re.I), "§ "),
    # Normalise § symbol variants
    (re.compile(r"&#167;|&sect;", re.I), "§ "),
    # Collapse multiple spaces
    (re.compile(r"\s{2,}"), " "),
]


def normalize_citation(raw_ref: str, jurisdiction_code: str | None = None) -> str:
    """Normalize a raw citation string to a canonical form.

    Strips jurisdiction-specific boilerplate (C.R.S., Cal. Bus. & Prof. Code, etc.)
    and returns a bare reference that approximates the parser's section_path format.

    Examples:
        "C.R.S. § 6-1-1702(3)(a)"  →  "§ 6-1-1702(3)(a)"
        "Cal. Bus. & Prof. Code § 22575"  →  "§ 22575"
        "Section 4(a)(2)"  →  "§ 4(a)(2)"
    """
    if not raw_ref:
        return ""

    result = raw_ref.strip()

    # Apply jurisdiction-specific prefixes first
    jcode = (jurisdiction_code or "").upper()
    for pattern, replacement in _JURISDICTION_PATTERNS.get(jcode, []):
        if callable(replacement):
            result = pattern.sub(replacement, result)
        else:
            result = pattern.sub(replacement, result)

    # Apply generic cleanup
    for pattern, replacement in _GENERIC_PREFIX_PATTERNS:
        result = pattern.sub(replacement, result)

    return result.strip()


def find_matching_section_path(
    normalized_ref: str,
    section_paths: list[str],
    *,
    min_score: float = 0.5,
) -> str | None:
    """Find the best-matching section_path for a normalized citation.

    Uses substring matching + token overlap scoring. Returns None when no
    path meets the minimum similarity threshold.

    Args:
        normalized_ref: Output of normalize_citation().
        section_paths: List of NormalizedSourceRecord.section_path values.
        min_score: Minimum score (0-1) to accept a match.
    """
    if not normalized_ref or not section_paths:
        return None

    ref_lower = normalized_ref.lower().strip()

    # Extract the numeric/alphanumeric section identifier from the ref.
    # "§ 6-1-1702(3)(a)" → "6-1-1702"
    number_match = re.search(r"[\d][\d\w\-\.]*", ref_lower)
    ref_number = number_match.group(0) if number_match else ""

    best_path: str | None = None
    best_score = 0.0

    for path in section_paths:
        path_lower = path.lower().strip()
        score = 0.0

        # Exact substring match (highest confidence)
        if ref_lower in path_lower or path_lower in ref_lower:
            score = 1.0
        elif ref_number and ref_number in path_lower:
            # Numeric part matches
            score = 0.7
        else:
            # Token overlap
            ref_tokens = set(re.findall(r"\w+", ref_lower))
            path_tokens = set(re.findall(r"\w+", path_lower))
            common = ref_tokens & path_tokens
            if ref_tokens:
                score = len(common) / len(ref_tokens)

        if score > best_score:
            best_score = score
            best_path = path

    return best_path if best_score >= min_score else None


def resolve_citation_to_record_id(
    raw_ref: str,
    jurisdiction_code: str | None,
    section_path_index: dict[str, int],
) -> int | None:
    """Resolve a raw citation to a NormalizedSourceRecord id.

    Args:
        raw_ref: The raw section_reference string from an extraction payload.
        jurisdiction_code: Two-letter jurisdiction code (CO, CA, etc.) or None.
        section_path_index: Dict mapping section_path → source_record_id.
    """
    normalized = normalize_citation(raw_ref, jurisdiction_code)
    matched_path = find_matching_section_path(normalized, list(section_path_index.keys()))
    if matched_path:
        return section_path_index[matched_path]
    return None


# ---------------------------------------------------------------------------
# TMP- placeholder ID detection (Phase 4.1)
# ---------------------------------------------------------------------------

_TMP_PREFIX = "TMP-"


def is_tmp_id(canonical_id: str | None) -> bool:
    """Return True when canonical_id is an unresolved placeholder (starts with TMP-)."""
    return bool(canonical_id and canonical_id.startswith(_TMP_PREFIX))


def resolve_tmp_to_bill(
    canonical_id: str,
    bill_number: str | None,
    jurisdiction_code: str | None,
) -> str | None:
    """Derive a best-guess formal citation for a TMP- law ID.

    When a law has a bill number and jurisdiction, construct a
    jurisdiction-qualified citation ("CO SB 205") that can serve
    as a human-readable placeholder while the canonical ID is pending.
    Returns None when not enough information is available.
    """
    if not is_tmp_id(canonical_id):
        return None
    if bill_number and jurisdiction_code:
        return f"{jurisdiction_code.upper()} {bill_number.strip()}"
    if bill_number:
        return bill_number.strip()
    return None
