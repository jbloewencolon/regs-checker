"""QA-9a: restatement-scoped relevance (plan Phase 2, docs/qa8_qa9_phased_plan.md).

STATUS: engine implemented and tested against the real corpus (SB 926, AB
2355). NOT YET WIRED into the live sync path (src/core/payload_adapter.py).
Unlike QA-6/QA-7's mechanical guards, the in-scope rules below are a
relevance judgment — they decide which parts of a bill's own text get
treated as "about AI" — and the plan requires RPR/product ratification
before they can start hiding real extraction rows (see the plan's Phase 2
step 4). This module is the ready-to-wire engine; wiring it into
payload_adapter.py (setting ai_nexus/display the way QA-6's
classify_legal_context() does for preemption_signal) is the remaining step,
gated on that sign-off plus generating and reviewing the per-law hide-report
the plan calls for.

Principle (plan fact 0.3, the naive-keyword-filter simulation that hid 98%
of two genuine AI laws): relevance filtering applies ONLY inside a
"restatement" passage — one Phase 1 (QA-8) grouped as a parallel version, or
a single-version restatement large enough to plausibly bury unrelated
content. A bill that is wholly an AI act is never touched by this at all,
because none of its passages trip the scope trigger below.

Granularity: two levels — top-level (a)(b)(c)... and, within each, nested
(1)(2)(3)... or (A)(B)(C).... This matches the plan's stated acceptance bar
for SB 926 ("(j)(4)-connected rows displayed") without attempting the
deeper (A)(i)(I) nesting CA statutes sometimes use; a keyword hit inside
that deeper structure still resolves to its enclosing 2nd-level clause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.agents.section_triage import _BASE_AI_KEYWORDS
from src.core.text_grounding import _loose_normalize

# Domain terms named in the plan that go beyond the triage vocabulary.
# "deepfake" and "digital replica" are already in _BASE_AI_KEYWORDS; listed
# here only where genuinely additive (e.g. bare "synthetic", which the
# triage set only carries as "synthetic media"/"synthetic content").
_DOMAIN_TERMS: set[str] = {
    "synthetic",
    "digitization",
    "digitized",
    "computer-generated",
    "computer generated",
    "intimate image",
    "materially deceptive",
}

SCOPE_KEYWORDS: set[str] = _BASE_AI_KEYWORDS | _DOMAIN_TERMS

# Scope trigger (plan Phase 2 step 1): a single-version restatement this
# large plausibly buries unrelated content the way SB 926's parallel
# versions do, even without a Phase-1 group.
RESTATEMENT_SIZE_THRESHOLD = 6000

_ADDED_SECTION_RE = re.compile(r"Section\s+(\d+(?:\.\d+)*)\s+is\s+added\s+to")

_TOP_LEVEL_RE = re.compile(r"\(([a-z])\)")
_SECOND_LEVEL_NUM_RE = re.compile(r"\((\d+)\)")
_SECOND_LEVEL_ALPHA_RE = re.compile(r"\(([A-Z])\)")

_LOWER_LETTERS = [chr(c) for c in range(ord("a"), ord("z") + 1)]
_UPPER_LETTERS = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
_DIGIT_STRINGS = [str(n) for n in range(1, 51)]


def is_restatement_passage(text: str, *, parallel_version_group: str | None) -> bool:
    """Scope trigger: does this passage count as a "restatement" at all?

    True when Phase 1 (QA-8) already grouped it as a parallel version, or
    it's a California-style full-section restatement large enough
    (>= RESTATEMENT_SIZE_THRESHOLD chars) to plausibly bury unrelated
    content in a single version. Everything else — including whole-AI-act
    bills, which never carry this header shape — is untouched.
    """
    if parallel_version_group:
        return True
    from src.ingestion.parser import _detect_amendment_target

    return (
        _detect_amendment_target(text) is not None
        and len(text) >= RESTATEMENT_SIZE_THRESHOLD
    )


def find_added_section_numbers(document_text: str) -> set[str]:
    """Section numbers this bill ADDS (not amends) anywhere in the document.

    Referencing one of these from inside a restatement keeps the
    referencing subdivision in scope (plan Phase 2 step 2(b)) — this is
    what keeps AB 2355's formatting rules in scope despite not naming AI
    themselves: they cite the bill's new § 84514.
    """
    return {m.group(1) for m in _ADDED_SECTION_RE.finditer(document_text)}


@dataclass
class SubdivisionSpan:
    label: str
    start: int
    end: int  # exclusive, offsets into the text this was parsed from


def _sequential_spans(
    text: str, pattern: re.Pattern, sequence: list[str]
) -> list[SubdivisionSpan]:
    """Match `pattern`, keeping only matches that continue the expected
    sequence (a, b, c, ... or 1, 2, 3, ...).

    This is what distinguishes real structural markers ("(a) An individual
    who...") from prose cross-references ("...of subdivision (b)..."),
    which are common in legislative text and would otherwise fragment the
    tree at every citation. A candidate that doesn't match the next
    expected label is simply not a boundary; scanning continues.
    """
    spans: list[SubdivisionSpan] = []
    next_idx = 0
    for m in pattern.finditer(text):
        if next_idx >= len(sequence):
            break
        if m.group(1) == sequence[next_idx]:
            spans.append(SubdivisionSpan(label=m.group(1), start=m.start(), end=-1))
            next_idx += 1
    for i, span in enumerate(spans):
        span.end = spans[i + 1].start if i + 1 < len(spans) else len(text)
    return spans


def parse_top_level_subdivisions(text: str) -> list[SubdivisionSpan]:
    return _sequential_spans(text, _TOP_LEVEL_RE, _LOWER_LETTERS)


def parse_second_level_subdivisions(text: str) -> list[SubdivisionSpan]:
    """Nested (1)(2)(3)... or (A)(B)(C)... within one top-level span's text.

    CA statutes use both styles; a given subdivision uses one consistently,
    so numeric is tried first (far more common) and alpha only if numeric
    finds nothing.
    """
    numeric = _sequential_spans(text, _SECOND_LEVEL_NUM_RE, _DIGIT_STRINGS)
    if numeric:
        return numeric
    return _sequential_spans(text, _SECOND_LEVEL_ALPHA_RE, _UPPER_LETTERS)


def find_evidence_offset(evidence_text: str, restatement_text: str) -> int | None:
    """Locate `evidence_text` within `restatement_text`, tolerant of
    punctuation/casing drift (mirrors text_grounding's Tier 3 loose match).

    Returns the original-text character offset of the match start, or None
    if it can't be found (e.g. too short to loose-match reliably, or the
    evidence genuinely isn't a substring of this restatement).
    """
    loose_evidence, _ = _loose_normalize(evidence_text)
    if not loose_evidence or len(loose_evidence) < 15:
        return None
    loose_full, index_map = _loose_normalize(restatement_text)
    idx = loose_full.find(loose_evidence)
    if idx == -1:
        return None
    return index_map[idx]


def assess_extraction_scope(
    evidence_text: str,
    restatement_text: str,
    *,
    added_section_numbers: set[str] | None = None,
) -> dict:
    """Is the extraction anchored at `evidence_text` in-scope of this
    restatement's AI/domain provisions?

    Returns {"in_scope": bool, "subdivision": str | None, "reason": str}.
    NOT WIRED INTO SYNC — see module docstring.
    """
    added_section_numbers = added_section_numbers or set()

    offset = find_evidence_offset(evidence_text, restatement_text)
    if offset is None:
        # Can't locate the evidence in this restatement — don't guess.
        # Leaving it in scope is the safe default: a false "in scope"
        # costs nothing extra (the status quo before this engine existed),
        # a false "out of scope" would silently hide a real row.
        return {"in_scope": True, "subdivision": None, "reason": "evidence_not_located"}

    top_spans = parse_top_level_subdivisions(restatement_text)
    if not top_spans:
        return {"in_scope": True, "subdivision": None, "reason": "no_subdivision_structure"}

    top = next((s for s in top_spans if s.start <= offset < s.end), None)
    if top is None:
        # Falls before the first top-level marker — the shared lead-in
        # ("647. Except as provided ... is guilty of a misdemeanor:") that
        # every subdivision depends on. Always in scope.
        return {"in_scope": True, "subdivision": None, "reason": "shared_preamble"}

    top_text = restatement_text[top.start : top.end]
    second_spans = parse_second_level_subdivisions(top_text)
    rel_offset = offset - top.start
    second = None
    if second_spans:
        second = next((s for s in second_spans if s.start <= rel_offset < s.end), None)
        if second is not None:
            scoped_text = top_text[second.start : second.end]
        else:
            # Evidence falls in the lead-in before the first child marker —
            # score it against ONLY that lead-in, not the whole top-level
            # span (which would smuggle a child's keyword into a sentence
            # that never mentions it, and make the adjacency rule below
            # unreachable).
            scoped_text = top_text[: second_spans[0].start]
    else:
        scoped_text = top_text

    label = f"({top.label})" + (f"({second.label})" if second else "")
    lowered = scoped_text.lower()

    for kw in SCOPE_KEYWORDS:
        if kw in lowered:
            return {"in_scope": True, "subdivision": label, "reason": f"keyword:{kw}"}

    # Rule (b) is checked against the whole TOP-LEVEL span, not just the
    # leaf: AB 2355's § 84504.2(a) cites the added § 84514 once in its lead
    # sentence ("shall include the disclosures required by ... Section
    # 84514"), and every formatting paragraph beneath it — (a)(1)'s "solid
    # white background", (a)(2)'s type size, etc. — implements that cited
    # disclosure without repeating the citation itself. Checking only the
    # leaf would silently hide exactly the "operative body of the
    # AI-disclosure regime" the plan's fact 0.3 identified as the
    # over-filtering trap.
    for sec_num in added_section_numbers:
        if re.search(rf"\bSection\s+{re.escape(sec_num)}\b", top_text):
            return {
                "in_scope": True,
                "subdivision": label,
                "reason": f"references_added_section:{sec_num}",
            }

    # Rule (c) adjacency: evidence in the shared lead-in of a top-level
    # subdivision (before its first numbered/lettered clause) stays in
    # scope if ANY of that subdivision's own children are in-scope —
    # siblings are judged independently and never swept in by this rule.
    if second is None and second_spans and rel_offset < second_spans[0].start:
        for child in second_spans:
            child_text = top_text[child.start : child.end].lower()
            if any(kw in child_text for kw in SCOPE_KEYWORDS):
                return {
                    "in_scope": True,
                    "subdivision": f"({top.label})",
                    "reason": "adjacent_to_in_scope_sibling",
                }

    return {"in_scope": False, "subdivision": label, "reason": "no_ai_domain_signal"}
