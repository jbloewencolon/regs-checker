"""Section Triage Agent — AI-relevance filter between parse and extraction.

Sits between the parse phase and the 6-agent extraction battery.  For each
passage (NormalizedSourceRecord), decides whether it contains AI-relevant
regulatory content worth sending to the full extraction pipeline.

Three-layer approach to minimise false negatives:

  Layer 1 — Keyword pre-screen (free, no LLM):
    Build a keyword set from Orrick/IAPP metadata *for this specific bill*
    plus a base set of generic AI terms.  If ANY keyword matches, the passage
    is marked relevant immediately.  ~60-70% of relevant passages hit here.

  Layer 2 — Orrick-informed LLM triage (1 cheap call per candidate-skip):
    For passages that FAILED the keyword screen, and when Orrick metadata
    exists, ask a fast model: "Given that this bill covers [ai_scope] with
    key requirements around [key_requirements], is this section relevant?"
    This catches non-standard phrasing that keywords miss.

  Layer 3 — Fallback (no Orrick data):
    When no Orrick metadata is available, run a generic AI-relevance check.
    If that's not possible, default to passthrough (current behavior).

PDF quality detection runs on every passage to flag OCR noise, garbled
characters, and encoding issues that could cause extraction failures.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Base AI keyword set — terms that indicate AI-relevant content regardless
# of what Orrick says.  Case-insensitive matching.
# ---------------------------------------------------------------------------

_BASE_AI_KEYWORDS: set[str] = {
    # Core AI terms
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "neural network",
    "generative ai",
    "generative artificial intelligence",
    "large language model",
    "foundation model",
    "algorithmic",
    "algorithm",
    "automated decision",
    "automated decision-making",
    "automated decision making",
    "automated employment decision",
    "automated system",
    "autonomous system",
    "predictive analytics",
    "predictive model",
    "natural language processing",
    "computer vision",
    "facial recognition",
    "biometric",
    # Regulatory / compliance terms often paired with AI
    "high-risk ai",
    "high risk ai",
    "ai system",
    "ai governance",
    "ai audit",
    "ai impact assessment",
    "algorithmic impact assessment",
    "algorithmic accountability",
    "algorithmic discrimination",
    "algorithmic bias",
    "ai transparency",
    "ai deployer",
    "ai developer",
    "deployer",
    "automated final decision",
    "consequential decision",
    # Deepfake / synthetic content
    "deepfake",
    "deep fake",
    "synthetic media",
    "synthetic content",
    # Specific act names that frequently appear
    "artificial intelligence act",
    "ai bill of rights",
}

# Patterns that match even as substrings (compiled once)
_BASE_AI_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(?:a\.?i\.?)\s+(?:system|model|tool|application|technology)", re.IGNORECASE),
    re.compile(r"\bautomat(?:ed|ic)\s+(?:decision|system|process|tool)", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# PDF quality detection
# ---------------------------------------------------------------------------

# Characters that indicate OCR/encoding issues
_GARBLED_CHARS = set("□■▯▮◊♦♣♠♥★☆○●◎◑◒◓◔⊕⊗⊘⊙⊚⊛⊜⊝")
_REPLACEMENT_CHAR = "\ufffd"  # Unicode replacement character

# High ratio of non-ASCII, non-letter characters suggests OCR noise
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_REPEATED_JUNK = re.compile(r"(.)\1{5,}")  # Same char repeated 6+ times
_WORD_PATTERN = re.compile(r"[a-zA-Z]{2,}")  # At least 2-letter words


@dataclass
class PDFQualityReport:
    """Quality assessment for a single passage."""
    score: float  # 0.0 (garbage) to 1.0 (clean)
    flags: list[str] = field(default_factory=list)


def assess_pdf_quality(text: str) -> PDFQualityReport:
    """Score text quality.  Designed for OCR'd PDFs but works on any text.

    Returns a score from 0.0 (unreadable garbage) to 1.0 (clean text).
    Flags specific quality issues found.
    """
    if not text or not text.strip():
        return PDFQualityReport(score=0.0, flags=["empty_passage"])

    flags: list[str] = []
    penalties: list[float] = []

    total_chars = len(text)

    # 1. Replacement characters (encoding failures)
    replacement_count = text.count(_REPLACEMENT_CHAR)
    if replacement_count > 0:
        ratio = replacement_count / total_chars
        flags.append("encoding_errors")
        penalties.append(min(ratio * 10, 0.5))  # up to -0.5

    # 2. Garbled/symbol characters
    garbled_count = sum(1 for c in text if c in _GARBLED_CHARS)
    if garbled_count > 2:
        flags.append("garbled_chars")
        penalties.append(min(garbled_count / total_chars * 8, 0.4))

    # 3. Control characters (shouldn't appear in cleaned text)
    control_count = len(_CONTROL_CHAR_PATTERN.findall(text))
    if control_count > 0:
        flags.append("control_chars")
        penalties.append(min(control_count / total_chars * 10, 0.3))

    # 4. Repeated junk patterns (OCR artifacts)
    junk_matches = _REPEATED_JUNK.findall(text)
    if len(junk_matches) > 2:
        flags.append("repeated_junk")
        penalties.append(0.2)

    # 5. Low word density (garbled text has few recognizable words)
    words = _WORD_PATTERN.findall(text)
    word_chars = sum(len(w) for w in words)
    alpha_ratio = word_chars / total_chars if total_chars > 0 else 0
    if alpha_ratio < 0.4:
        flags.append("low_word_density")
        penalties.append(0.3)

    # 6. Excessive non-ASCII (excluding common accented chars)
    non_ascii = sum(
        1 for c in text
        if ord(c) > 127 and unicodedata.category(c) not in ("Ll", "Lu", "Lt", "Lm", "Lo")
    )
    non_ascii_ratio = non_ascii / total_chars if total_chars > 0 else 0
    if non_ascii_ratio > 0.1:
        flags.append("high_non_ascii")
        penalties.append(min(non_ascii_ratio * 3, 0.3))

    score = max(0.0, 1.0 - sum(penalties))
    return PDFQualityReport(score=round(score, 3), flags=flags)


# ---------------------------------------------------------------------------
# Keyword extraction from Orrick metadata
# ---------------------------------------------------------------------------

def _extract_orrick_terms(context: dict) -> set[str]:
    """Extract searchable terms from Orrick/IAPP metadata.

    Pulls terms from key_requirements, enforcement_summary, ai_scope,
    and iapp_ai_topic fields.  These are the terms Orrick's lawyers
    identified as relevant — using them as search terms gives us a
    strong prior for section-level relevance.
    """
    terms: set[str] = set()

    # AI scope / topic (e.g., "Automated Decision Systems", "Deepfakes")
    for field_name in ("ai_scope", "iapp_ai_topic"):
        val = context.get(field_name, "")
        if val:
            # Split on commas/semicolons for multi-topic fields
            for part in re.split(r"[;,/]", val):
                part = part.strip().lower()
                if len(part) >= 3:
                    terms.add(part)

    # Key requirements — extract noun phrases and regulatory terms
    key_reqs = context.get("key_requirements", "")
    if key_reqs:
        # Extract significant phrases (3+ word sequences)
        for match in re.finditer(r"\b[a-z][a-z\s\-]{4,30}\b", key_reqs.lower()):
            phrase = match.group().strip()
            if len(phrase.split()) >= 2:
                terms.add(phrase)

        # Also extract individual regulatory keywords
        for word in re.findall(r"\b[a-z]{4,}\b", key_reqs.lower()):
            if word in {
                "deployer", "deployers", "developer", "developers",
                "audit", "audits", "assessment", "assessments",
                "transparency", "disclosure", "notice", "consent",
                "discrimination", "bias", "fairness", "accountability",
                "profiling", "surveillance", "biometric", "biometrics",
                "automated", "algorithmic", "algorithm",
            }:
                terms.add(word)

    # Enforcement summary — extract entity names and penalty types
    enforcement = context.get("enforcement_summary", "")
    if enforcement:
        for word in re.findall(r"\b[a-z]{4,}\b", enforcement.lower()):
            if word in {
                "penalty", "penalties", "fine", "fines", "violation",
                "enforcement", "attorney", "commissioner", "injunction",
                "damages", "liability", "compliance",
            }:
                terms.add(word)

    return terms


# ---------------------------------------------------------------------------
# Core triage logic
# ---------------------------------------------------------------------------

@dataclass
class TriageResult:
    """Result of triaging a single passage."""
    decision: str          # "relevant", "not_relevant", "uncertain"
    method: str            # "keyword", "orrick_cross_check", "llm_generic", "quality_fail", "passthrough"
    confidence: float      # 0.0-1.0
    matched_keywords: list[str] = field(default_factory=list)
    orrick_terms_checked: list[str] = field(default_factory=list)
    llm_reasoning: str | None = None
    ai_signals: str | None = None
    pdf_quality_score: float | None = None
    quality_flags: list[str] = field(default_factory=list)
    model_id: str | None = None


def _keyword_screen(text: str, orrick_terms: set[str]) -> tuple[bool, list[str]]:
    """Layer 1: Check if passage contains any AI-relevant keywords.

    Returns (matched, list_of_matched_keywords).
    """
    text_lower = text.lower()
    matched: list[str] = []

    # Check base AI keywords
    for kw in _BASE_AI_KEYWORDS:
        if kw in text_lower:
            matched.append(kw)

    # Check Orrick-derived terms
    for term in orrick_terms:
        if term in text_lower:
            matched.append(f"orrick:{term}")

    # Check regex patterns
    for pattern in _BASE_AI_PATTERNS:
        if pattern.search(text):
            matched.append(f"pattern:{pattern.pattern[:30]}")
            break  # One pattern match is sufficient

    return bool(matched), matched


def _build_bill_context_block(context: dict) -> str:
    """Build a text block summarising bill-level context for the LLM prompt.

    Includes definitions, scope, structure and defined terms when available
    so the triage model can judge relevance against the full bill, not just
    the isolated passage.
    """
    parts: list[str] = []

    bill_defs = context.get("bill_definitions", "")
    if bill_defs:
        parts.append(f"DEFINITIONS FROM THIS BILL (excerpt):\n{bill_defs[:30000]}")

    bill_scope = context.get("bill_scope", "")
    if bill_scope:
        parts.append(f"SCOPE / APPLICABILITY (excerpt):\n{bill_scope[:20000]}")

    defined_terms = context.get("defined_terms")
    if defined_terms:
        terms_str = ", ".join(defined_terms[:40])
        parts.append(f"DEFINED TERMS: {terms_str}")

    bill_structure = context.get("bill_structure", "")
    if bill_structure:
        parts.append(f"BILL STRUCTURE (section outline):\n{bill_structure[:5000]}")

    if not parts:
        return ""

    return "\n\n".join(parts)


def _build_neighbor_block(neighbors: list[str]) -> str:
    """Build a text block showing neighboring passages for context."""
    if not neighbors:
        return ""
    trimmed = [n[:3000] for n in neighbors]
    return "SURROUNDING SECTIONS (for context):\n" + "\n---\n".join(trimmed)


def _build_triage_prompt(
    passage: str,
    context: dict,
    neighbors: list[str] | None = None,
) -> str:
    """Build the LLM prompt for section-level AI-relevance triage.

    When bill-level context (definitions, scope, structure) or neighboring
    passage text is available, include it so the model can judge relevance
    against the *whole* bill rather than the isolated passage.
    """
    ai_scope = context.get("ai_scope", "")
    key_reqs = context.get("key_requirements", "")
    title = context.get("document_title", "Unknown")

    bill_ctx_block = _build_bill_context_block(context)
    neighbor_block = _build_neighbor_block(neighbors or [])

    # Extra context sections (only included when data is available)
    extra_sections = ""
    if bill_ctx_block:
        extra_sections += f"\n\n{bill_ctx_block}"
    if neighbor_block:
        extra_sections += f"\n\n{neighbor_block}"

    if ai_scope or key_reqs:
        # Layer 2: Orrick-informed triage
        prompt = f"""You are a legal triage agent. Your job is to determine whether a
passage from a bill or law contains content that is SPECIFICALLY relevant to
artificial intelligence or automated-decision-system regulation.

BILL: {title}
KNOWN AI SCOPE: {ai_scope or 'Not specified'}
KEY REQUIREMENTS (from Orrick AI Law Tracker):
{key_reqs or 'Not available'}{extra_sections}

PASSAGE TO EVALUATE:
---
{passage}
---

INSTRUCTIONS:
1. First, read the passage carefully. Identify any terms, obligations, or
   concepts that are specifically about AI, automated decision-making, machine
   learning, algorithmic systems, or the actors who build/deploy them.
2. Consider the bill-level context above (definitions, scope, structure).
   A generic procedural section (severability, effective dates, general
   appropriations) is NOT relevant just because it appears in an AI bill.
3. A passage IS relevant if it:
   - Defines, regulates, or creates obligations for AI/automated systems
   - Establishes rights for people affected by AI/automated decisions
   - Sets thresholds, exceptions, or enforcement for AI-specific rules
   - Specifically references the AI-related scope of this bill
4. A passage is NOT relevant if it:
   - Is purely procedural / administrative with no AI-specific content
   - Covers general topics (tax, zoning, appropriations) that happen to be
     in the same bill but do not touch AI regulation
   - Mentions "technology" only in a generic, non-AI sense

Respond with EXACTLY one JSON object:
{{"relevant": true/false, "confidence": 0.0-1.0, "reasoning": "brief explanation of your decision", "ai_signals": "quote the specific words/phrases from the passage that indicate AI relevance, or explain why none exist — be explicit about whether the connection is direct or indirect"}}

Be CONSERVATIVE — when genuinely in doubt, mark as relevant. But do NOT mark
generic procedural sections as relevant simply because they sit inside an AI bill."""
    else:
        # Layer 3: Generic AI-relevance check (no Orrick data)
        prompt = f"""You are a legal triage agent. Your job is to determine whether a
passage from a bill or law contains content that is SPECIFICALLY relevant to
artificial intelligence or automated-decision-system regulation.

BILL: {title}{extra_sections}

PASSAGE TO EVALUATE:
---
{passage}
---

INSTRUCTIONS:
1. First, read the passage carefully. Identify any terms, obligations, or
   concepts that are specifically about AI, automated decision-making, machine
   learning, algorithmic systems, or the actors who build/deploy them.
2. If bill-level context is shown above, use it to understand the bill's
   purpose. A generic procedural section (severability, effective dates,
   general appropriations) is NOT relevant just because it appears in an
   AI bill.
3. A passage IS relevant if it directly addresses AI, automated systems, ML,
   algorithmic regulation, or the people/entities affected by such systems.
4. A passage is NOT relevant if it covers general topics that do not touch
   AI regulation, even if the surrounding bill is about AI.

Respond with EXACTLY one JSON object:
{{"relevant": true/false, "confidence": 0.0-1.0, "reasoning": "brief explanation of your decision", "ai_signals": "quote the specific words/phrases from the passage that indicate AI relevance, or explain why none exist — be explicit about whether the connection is direct or indirect"}}

Be CONSERVATIVE — when genuinely in doubt, mark as relevant. But do NOT mark
generic procedural sections as relevant simply because they sit inside an AI bill."""

    return prompt


def triage_passage(
    text: str,
    context: dict,
    llm_provider=None,
    neighbors: list[str] | None = None,
) -> TriageResult:
    """Triage a single passage for AI-relevance.

    Args:
        text: The passage text to evaluate.
        context: Build context dict (from _build_context) with Orrick metadata.
            May also include bill-level keys (bill_definitions, bill_scope,
            bill_structure, defined_terms) when the caller has built bill
            context — these give the LLM the full picture of the bill.
        llm_provider: Optional LLM provider for Layer 2/3 triage.
            If None, passages that fail keyword screening default to "uncertain"
        neighbors: Optional list of neighboring passage texts (before/after)
            to give the model surrounding context.
            (conservative — they'll still be sent to extraction).

    Returns:
        TriageResult with decision, method, and supporting data.
    """
    import json

    # Step 0: PDF quality check
    quality = assess_pdf_quality(text)
    quality_score = quality.score
    quality_flags = quality.flags

    # If quality is too low, skip triage — extraction would fail anyway
    if quality_score < 0.3:
        return TriageResult(
            decision="not_relevant",
            method="quality_fail",
            confidence=quality_score,
            pdf_quality_score=quality_score,
            quality_flags=quality_flags,
        )

    # Step 1: Extract Orrick terms for this bill
    orrick_terms = _extract_orrick_terms(context)

    # Step 2: Keyword pre-screen (Layer 1)
    matched, matched_keywords = _keyword_screen(text, orrick_terms)
    if matched:
        return TriageResult(
            decision="relevant",
            method="keyword",
            confidence=min(0.7 + len(matched_keywords) * 0.05, 0.95),
            matched_keywords=matched_keywords,
            orrick_terms_checked=sorted(orrick_terms)[:20],
            pdf_quality_score=quality_score,
            quality_flags=quality_flags,
        )

    # Step 3: LLM triage (Layer 2 or 3)
    if llm_provider is None:
        # No LLM available — conservative default
        return TriageResult(
            decision="uncertain",
            method="passthrough",
            confidence=0.5,
            orrick_terms_checked=sorted(orrick_terms)[:20],
            pdf_quality_score=quality_score,
            quality_flags=quality_flags,
        )

    has_orrick = bool(context.get("ai_scope") or context.get("key_requirements"))
    method = "orrick_cross_check" if has_orrick else "llm_generic"

    try:
        prompt = _build_triage_prompt(text, context, neighbors=neighbors)
        llm_response = llm_provider.call(
            system_prompt="You are a legal text triage agent. Respond only with valid JSON.",
            user_prompt=prompt,
            max_tokens=1024,
            temperature=0.0,
        )

        # Parse LLM response
        response_text = llm_response.text.strip()
        # Strip think blocks from reasoning models
        response_text = re.sub(
            r"<think>.*?</think>", "", response_text, flags=re.DOTALL
        ).strip()
        # Strip special tokens that some models emit
        response_text = re.sub(
            r"<\|[^|]+\|>", "", response_text
        ).strip()

        # Find JSON in response — look for object with "relevant" key specifically
        # to avoid matching random {braces} in think blocks or garbage output
        json_match = re.search(r'\{[^{}]*"relevant"\s*:[^{}]*\}', response_text)
        if not json_match:
            # Fallback: try any JSON-like object
            json_match = re.search(r"\{[^{}]+\}", response_text)
        if json_match:
            try:
                result = json.loads(json_match.group())
            except json.JSONDecodeError:
                try:
                    # Try fixing common issues: trailing commas, unquoted values
                    cleaned = re.sub(r",\s*}", "}", json_match.group())
                    cleaned = re.sub(r":\s*(true|false)\b", lambda m: ": " + m.group(1).lower(), cleaned)
                    result = json.loads(cleaned)
                except json.JSONDecodeError:
                    # Completely unparseable — treat as parse failure
                    logger.warning("triage_json_decode_failed", raw=json_match.group()[:200])
                    return TriageResult(
                        decision="uncertain",
                        method="passthrough",
                        confidence=0.3,
                        orrick_terms_checked=sorted(orrick_terms)[:20],
                        llm_reasoning=f"JSON decode failed: {json_match.group()[:300]}",
                        pdf_quality_score=quality_score,
                        quality_flags=quality_flags + ["llm_parse_failed"],
                    )

            is_relevant = result.get("relevant", True)  # Default to relevant (conservative)
            conf = float(result.get("confidence", 0.5))
            reasoning = result.get("reasoning", "")
            ai_signals = result.get("ai_signals", "")

            if is_relevant:
                decision = "relevant"
            elif conf >= 0.8:
                decision = "not_relevant"
            else:
                # Low confidence "not relevant" → uncertain (send to extraction anyway)
                decision = "uncertain"

            return TriageResult(
                decision=decision,
                method=method,
                confidence=conf,
                orrick_terms_checked=sorted(orrick_terms)[:20],
                llm_reasoning=reasoning,
                ai_signals=ai_signals,
                pdf_quality_score=quality_score,
                quality_flags=quality_flags,
                model_id=llm_provider.model_id if hasattr(llm_provider, "model_id") else None,
            )
        else:
            logger.warning("triage_llm_parse_failed", response=response_text[:200])
            return TriageResult(
                decision="uncertain",
                method="passthrough",
                confidence=0.3,
                orrick_terms_checked=sorted(orrick_terms)[:20],
                llm_reasoning=f"LLM returned unparseable response: {response_text[:300]}",
                pdf_quality_score=quality_score,
                quality_flags=quality_flags + ["llm_parse_failed"],
            )

    except Exception as exc:
        logger.exception("triage_llm_error")
        error_msg = str(exc)[:300]
        return TriageResult(
            decision="uncertain",
            method="passthrough",
            confidence=0.3,
            orrick_terms_checked=sorted(orrick_terms)[:20],
            llm_reasoning=f"LLM call failed: {error_msg}",
            pdf_quality_score=quality_score,
            quality_flags=quality_flags + ["llm_error"],
        )
