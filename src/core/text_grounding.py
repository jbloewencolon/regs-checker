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


# ---------------------------------------------------------------------------
# Index-map-aware normalization (EA2-2) — lets Tier 1/2 report char_start/
# char_end against the CANONICAL RAW PASSAGE instead of the normalized
# string. Before this, offsets were always relative to norm_passage (or a
# further-transformed string for Tier 3/4), which is a different length than
# the raw text whenever Unicode substitution or whitespace collapsing
# changed anything — silently wrong offsets for audit-UI highlighting.
# ---------------------------------------------------------------------------


def _normalize_unicode_with_map(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Like _normalize_unicode but also returns a per-output-char raw span map.

    ``spans[i]`` is the ``(raw_start, raw_end)`` half-open range in ``text``
    that produced output character ``i``. Every substitution in
    _UNICODE_CHAR_MAP is either a single-char replacement or a deletion
    (soft hyphen) — never a multi-char expansion — so this mapping is exact.
    """
    out_chars: list[str] = []
    spans: list[tuple[int, int]] = []
    for i, ch in enumerate(text):
        code = ord(ch)
        if code in _UNICODE_CHAR_MAP:
            replacement = _UNICODE_CHAR_MAP[code]
            if replacement:
                out_chars.append(replacement)
                spans.append((i, i + 1))
            # else: deletion (soft hyphen) — no output character, no span entry
        else:
            out_chars.append(ch)
            spans.append((i, i + 1))
    return "".join(out_chars), spans


def _normalize_whitespace_with_map(
    text: str, base_spans: list[tuple[int, int]]
) -> tuple[str, list[tuple[int, int]]]:
    """Collapse whitespace runs to single spaces, composing with base_spans.

    Mirrors ``" ".join(text.split())`` exactly (strips leading/trailing
    whitespace, collapses internal runs to one space) while composing the
    supplied per-character raw-span map so the result maps all the way back
    to the ORIGINAL raw passage, not just to ``text``.
    """
    out_chars: list[str] = []
    out_spans: list[tuple[int, int]] = []
    i = 0
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    while i < n:
        ch = text[i]
        if ch.isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            if j < n:
                # Internal whitespace run — collapses to one space, but the
                # space's raw span covers the ENTIRE raw whitespace run (not
                # just its first character), so a later boundary translation
                # that lands on this space still recovers the true raw extent.
                out_chars.append(" ")
                out_spans.append((base_spans[i][0], base_spans[j - 1][1]))
            i = j
            # Trailing whitespace run (j == n): drop entirely, no output.
        else:
            out_chars.append(ch)
            out_spans.append(base_spans[i])
            i += 1
    return "".join(out_chars), out_spans


def _normalize_text_with_map(text: str) -> tuple[str, list[tuple[int, int]]] | None:
    """Full normalization with a raw-passage span map, or None if unavailable.

    Returns None when NFC normalization changes the string's length — a rare
    edge case (combining-character sequences uncommon in US legislative
    text) where exact position correspondence can't be guaranteed. Callers
    must fall back to _normalize_text() (same resulting string, just without
    raw-offset capability) rather than trust a possibly-wrong map.
    """
    substituted, uni_spans = _normalize_unicode_with_map(text)
    normalized = unicodedata.normalize("NFC", substituted)
    if len(normalized) != len(substituted):
        return None
    collapsed, spans = _normalize_whitespace_with_map(normalized, uni_spans)
    return collapsed, spans


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

    Every verified span carries ``match_tier`` (1-4) and ``loose_match``
    (True for tiers 3-4) so review/audit surfaces can distinguish an exact
    verbatim hit from a punctuation-or-artifact-tolerant one (EA2-2).

    Tier 1/2 ``char_start``/``char_end`` are offsets into the RAW passage
    string passed in (not a normalized intermediate) — safe to slice
    ``passage[char_start:char_end]`` directly for audit-UI highlighting.
    Tier 3/4 leave ``char_start``/``char_end`` as ``None``: their match
    coordinates live in a punctuation-stripped/artifact-stripped
    intermediate string, and precisely inverting those transforms back to
    raw-passage offsets is not implemented — reporting no offset is safer
    than reporting a wrong one for a highlighter to silently mis-render.

    Args:
        spans: List of evidence span dicts (must have at least "text" and "field_name").
        passage: The source passage text to verify against.
        agent_name: Logged in warnings for traceability.

    Returns:
        List of span dicts with added keys: verified (bool), match_tier
        (int, only when verified), loose_match (bool, only when verified),
        char_start, char_end (int or None, only when verified).
    """
    map_result = _normalize_text_with_map(passage)
    if map_result is not None:
        norm_passage, raw_spans = map_result
    else:
        # NFC changed length for this passage (rare) — same normalized
        # string, but Tier 1/2 raw-offset translation is unavailable.
        norm_passage = _normalize_text(passage)
        raw_spans = None
    lower_passage = norm_passage.lower()
    # Tier 3/4 no longer translate offsets back through these maps — see
    # docstring on why char_start/char_end are None for loose matches — so
    # only the reduced strings themselves are needed, not the index maps.
    loose_passage, _ = _loose_normalize(norm_passage)

    # Precompute Tier 4 inputs once per passage
    stripped_passage = strip_revisor_artifacts(norm_passage)
    loose_stripped_passage, _ = _loose_normalize(stripped_passage)

    def _raw_offsets(norm_start: int, norm_end: int) -> tuple[int | None, int | None]:
        """Translate a [norm_start, norm_end) range in norm_passage to raw offsets."""
        if raw_spans is None:
            return None, None
        return raw_spans[norm_start][0], raw_spans[norm_end - 1][1]

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
            end = start + len(norm_span)
            raw_start, raw_end = _raw_offsets(start, end)
            verified.append({
                "field_name": span.field_name,
                "text": span.text,
                "char_start": raw_start,
                "char_end": raw_end,
                "verified": True,
                "match_tier": 1,
                "loose_match": False,
            })
            continue

        # Tier 2 — case-insensitive
        norm_lower = norm_span.lower()
        if norm_lower in lower_passage:
            start = lower_passage.index(norm_lower)
            end = start + len(norm_span)
            raw_start, raw_end = _raw_offsets(start, end)
            verified.append({
                "field_name": span.field_name,
                "text": span.text,
                "char_start": raw_start,
                "char_end": raw_end,
                "verified": True,
                "match_tier": 2,
                "loose_match": False,
            })
            continue

        # Tier 3 — punctuation-insensitive (loose), ≥ 15-char floor.
        # Match coordinates live in loose_passage (alnum-only, norm_passage
        # positions) — not translated further back to the raw passage; see
        # docstring on why char_start/char_end are None for this tier.
        loose_span, _ = _loose_normalize(norm_span)
        if len(loose_span) >= 15 and loose_span in loose_passage:
            verified.append({
                "field_name": span.field_name,
                "text": span.text,
                "char_start": None,
                "char_end": None,
                "verified": True,
                "match_tier": 3,
                "loose_match": True,
            })
            continue

        # Tier 4 — revisor-artifact-stripped loose match, ≥ 25-char floor.
        # Same rationale as Tier 3 — coordinates live in a doubly-transformed
        # intermediate string, not translated back to the raw passage.
        loose_stripped_span, _ = _loose_normalize(strip_revisor_artifacts(norm_span))
        if len(loose_stripped_span) >= 25 and loose_stripped_span in loose_stripped_passage:
            verified.append({
                "field_name": span.field_name,
                "text": span.text,
                "char_start": None,
                "char_end": None,
                "verified": True,
                "match_tier": 4,
                "loose_match": True,
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
