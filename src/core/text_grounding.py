"""Artifact-aware text normalization for evidence span grounding (Phase 1).

PDF-extracted legislative text (especially from state revisors' offices) contains
formatting noise that doesn't appear in LLM-generated evidence spans:

  - Margin/line numbers: "5.01  " or "15." prefixed to each extracted line
  - Hyphenated line-breaks: "compli-\nance" in the source PDF
  - Section space artifacts: "SECTIONA1" where A is a PDF glyph
  - Multi-space runs left behind after stripping the above

verify_evidence_spans() implements 4-tier matching with each tier relaxing
formatting assumptions incrementally:

  Tier 1 — Exact match after Unicode + whitespace normalization
  Tier 2 — Case-insensitive match
  Tier 3 — Punctuation-insensitive ("loose") match, ≥ 15-char floor
  Tier 4 — Revisor-artifact-stripped loose match, ≥ 25-char floor

Tiers 1-3 are unchanged from the original BaseAgent._verify_evidence_spans.
Tier 4 is new: it strips PDF margin numbers, de-hyphenates line-breaks, and
repairs SECTION glyphs before re-applying the loose normalizer.
"""

from __future__ import annotations

import re
import unicodedata

import structlog

from src.schemas.extraction import EvidenceSpan

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Revisor artifact patterns
# ---------------------------------------------------------------------------

# Margin/line numbers at the start of a line, e.g. "  5.01  word" or "15. next".
# Only strips digit sequences followed by space (never inside a word).
_MARGIN_NUM = re.compile(
    r"(?m)^[ \t]*\d{1,4}(?:\.\d{1,3})?[ \t]+",
)

# Hyphenation across a line break: "compli-\nance" → "compliance"
_HYPHEN_BREAK = re.compile(r"(\w)-\s*\n\s*(\w)")

# Section glyph artifact: "SECTIONA1" or "SECTION A 1" where the letter between
# SECTION and a digit is a PDF encoding artefact, not real text content.
_SECTION_ARTIFACT = re.compile(r"\bSECTION([A-Z])(\d)", re.I)

# Collapse runs of spaces/tabs to a single space (newlines intentionally kept
# so _MARGIN_NUM can match at line starts).
_MULTI_SPACE = re.compile(r"[ \t]{2,}")

# Smart-quote / dash / NBSP normalization maps
_UNICODE_CHAR_MAP = str.maketrans(
    {
        "‘": "'",   # left single quotation mark
        "’": "'",   # right single quotation mark
        "“": '"',   # left double quotation mark
        "”": '"',   # right double quotation mark
        "–": "-",   # en dash
        "—": "-",   # em dash
        " ": " ",   # non-breaking space
        "­": "",    # soft hyphen
    }
)


# ---------------------------------------------------------------------------
# Normalization primitives
# ---------------------------------------------------------------------------


def _normalize_unicode(text: str) -> str:
    """Normalize smart quotes, dashes, and non-breaking spaces to ASCII equivalents."""
    text = text.translate(_UNICODE_CHAR_MAP)
    return unicodedata.normalize("NFC", text)


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace sequences (including newlines) to single spaces."""
    return " ".join(text.split())


def _normalize_text(text: str) -> str:
    """Full normalization: Unicode variants then whitespace collapse."""
    return _normalize_whitespace(_normalize_unicode(text))


def _loose_normalize(text: str) -> tuple[str, list[int]]:
    """Reduce text to lowercase alphanumerics with single-space separators.

    Returns (reduced_string, index_map) where index_map[i] gives the position
    in the original text corresponding to position i in the reduced string.
    Punctuation and casing are erased so a span the model re-punctuated or
    re-cased still matches as long as the same words appear in order.
    """
    out_chars: list[str] = []
    index_map: list[int] = []
    prev_space = True  # suppress leading space
    for i, ch in enumerate(text):
        if ch.isalnum():
            out_chars.append(ch.lower())
            index_map.append(i)
            prev_space = False
        elif not prev_space:
            out_chars.append(" ")
            index_map.append(i)
            prev_space = True
    if out_chars and out_chars[-1] == " ":
        out_chars.pop()
        index_map.pop()
    return "".join(out_chars), index_map


# ---------------------------------------------------------------------------
# Revisor artifact stripping (Tier 4)
# ---------------------------------------------------------------------------


def strip_revisor_artifacts(text: str) -> str:
    """Remove common PDF-revisor artifacts from legislative text.

    Applied symmetrically to both passage and span before Tier 4 matching
    so neither side has an advantage from formatting noise.
    """
    # 1. Dehyphenate across line breaks before anything collapses them
    result = _HYPHEN_BREAK.sub(r"\1\2", text)
    # 2. Strip margin/line numbers at line starts
    result = _MARGIN_NUM.sub("", result)
    # 3. Repair SECTION<letter><digit> glyphs
    result = _SECTION_ARTIFACT.sub(r"SECTION \2", result)
    # 4. Collapse double spaces left behind by stripping
    result = _MULTI_SPACE.sub(" ", result)
    return result.strip()


# ---------------------------------------------------------------------------
# 4-tier evidence span verifier
# ---------------------------------------------------------------------------


def verify_evidence_spans(
    spans: list[dict],
    passage: str,
    *,
    agent_name: str = "unknown",
) -> list[dict]:
    """Verify evidence spans via 4-tier string matching.

    Confirms each span's text appears in the passage; marks verified=True/False.

    Tiers (applied in order, first match wins):
      1. Exact match after Unicode + whitespace normalization
      2. Case-insensitive match
      3. Punctuation-insensitive (loose) match — ≥ 15-char floor
      4. Revisor-artifact-stripped loose match — ≥ 25-char floor

    Args:
        spans: List of evidence span dicts (must have at least "text" and "field_name").
        passage: The source passage text to verify against.
        agent_name: Logged in warnings for traceability.

    Returns:
        List of span dicts with added keys: verified (bool), char_start, char_end
        (the latter two only when verified=True).
    """
    norm_passage = _normalize_text(passage)
    lower_passage = norm_passage.lower()
    loose_passage, loose_passage_map = _loose_normalize(norm_passage)

    # Precompute Tier 4 inputs once per passage
    stripped_passage = strip_revisor_artifacts(norm_passage)
    loose_stripped_passage, stripped_map = _loose_normalize(stripped_passage)

    verified: list[dict] = []
    for span_data in spans:
        if not isinstance(span_data, dict) or not span_data.get("text"):
            continue
        try:
            span = EvidenceSpan(**span_data)
        except Exception:
            continue

        norm_span = _normalize_text(span.text)

        # Tier 1 — exact after normalization
        if norm_span in norm_passage:
            start = norm_passage.index(norm_span)
            verified.append({
                "field_name": span.field_name,
                "text": span.text,
                "char_start": start,
                "char_end": start + len(norm_span),
                "verified": True,
            })
            continue

        # Tier 2 — case-insensitive
        norm_lower = norm_span.lower()
        if norm_lower in lower_passage:
            start = lower_passage.index(norm_lower)
            verified.append({
                "field_name": span.field_name,
                "text": span.text,
                "char_start": start,
                "char_end": start + len(norm_span),
                "verified": True,
            })
            continue

        # Tier 3 — punctuation-insensitive (loose), ≥ 15-char floor
        loose_span, _ = _loose_normalize(norm_span)
        if len(loose_span) >= 15 and loose_span in loose_passage:
            ls = loose_passage.index(loose_span)
            le = ls + len(loose_span) - 1
            verified.append({
                "field_name": span.field_name,
                "text": span.text,
                "char_start": loose_passage_map[ls],
                "char_end": loose_passage_map[le] + 1,
                "verified": True,
            })
            continue

        # Tier 4 — revisor-artifact-stripped loose match, ≥ 25-char floor
        loose_stripped_span, _ = _loose_normalize(strip_revisor_artifacts(norm_span))
        if len(loose_stripped_span) >= 25 and loose_stripped_span in loose_stripped_passage:
            ls = loose_stripped_passage.index(loose_stripped_span)
            le = ls + len(loose_stripped_span) - 1
            verified.append({
                "field_name": span.field_name,
                "text": span.text,
                "char_start": stripped_map[ls],
                "char_end": stripped_map[le] + 1,
                "verified": True,
            })
            continue

        logger.warning(
            "evidence_span_not_found",
            agent=agent_name,
            field=span.field_name,
            span_text=span.text[:80],
        )
        verified.append({
            "field_name": span.field_name,
            "text": span.text,
            "verified": False,
        })

    return verified
