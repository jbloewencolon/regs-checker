"""QA-9a/QA-9c: restatement-scoped relevance (plan Phases 2/2b,
docs/qa8_qa9_phased_plan.md).

STATUS: engine implemented and tested against the real corpus (SB 926, AB
2355). WIRED into the sync path (src/core/payload_adapter.py) behind
``settings.qa9a_scope_filter_enabled``, which defaults to False — unlike
QA-6/QA-7's mechanical guards, the in-scope rules below are a relevance
judgment (they decide which parts of a bill's own text get treated as
"about AI"), and the plan requires RPR/product ratification before they can
start hiding real extraction rows (Phase 2 step 4). A human flips the flag
post-ratification; until then every consumer of this module is a no-op in
production.

QA-9c (Phase 2b) moves the scope *computation* to parse time:
``annotate_restatement_scope()`` classifies a restatement's whole
subdivision tree in one pass and the parser stores the result in
``NormalizedSourceRecord.metadata_["restatement_scope"]``. Parse time is
the only pipeline stage holding the whole document — which is exactly the
context rule 2(b) (references to sections this bill adds) needs and the
sync path lacks. Consumers (sync-time hiding, QA-9b pre-extraction
slicing) read the stored annotation; ``assess_extraction_scope`` remains
as the on-the-fly fallback for rows ingested before annotation existed,
reimplemented on top of the annotation machinery so there is exactly one
implementation of the rules. Stored annotations carry
``SCOPE_ENGINE_VERSION``; a version mismatch (rules/vocabulary changed
since annotation) makes consumers treat the annotation as absent rather
than silently applying stale verdicts.

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

# QA-9c: bump whenever SCOPE_KEYWORDS or the in-scope rules change. Stored
# annotations stamped with an older version are treated as absent by
# consumers (annotation_is_current() → fall back to on-the-fly assessment),
# so a rule/vocabulary change can never silently apply stale verdicts.
SCOPE_ENGINE_VERSION = 1

# Deterministic iteration order so the keyword named in a region's `reason`
# is stable across processes (set iteration order varies with string hash
# randomization) — stored annotations must be reproducible.
_SORTED_SCOPE_KEYWORDS = sorted(SCOPE_KEYWORDS)

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


def _keyword_hit(text_lower: str) -> str | None:
    """First matching scope keyword, in deterministic (sorted) order."""
    for kw in _SORTED_SCOPE_KEYWORDS:
        if kw in text_lower:
            return kw
    return None


def _added_section_hit(top_text: str, added_section_numbers: set[str]) -> str | None:
    """Rule (b): does this TOP-LEVEL span cite a section this bill adds?

    Checked against the whole top-level span, not just a leaf: AB 2355's
    § 84504.2(a) cites the added § 84514 once in its lead sentence ("shall
    include the disclosures required by ... Section 84514"), and every
    formatting paragraph beneath it — (a)(1)'s "solid white background",
    (a)(2)'s type size, etc. — implements that cited disclosure without
    repeating the citation itself. Checking only the leaf would silently
    hide exactly the "operative body of the AI-disclosure regime" the
    plan's fact 0.3 identified as the over-filtering trap.
    """
    for sec_num in sorted(added_section_numbers):
        if re.search(rf"\bSection\s+{re.escape(sec_num)}\b", top_text):
            return sec_num
    return None


def _classify(scoped_text: str, added_reference: str | None) -> tuple[bool, str]:
    """Rules (a) then (b) on one region's own text.

    `added_reference` is the enclosing top-level span's rule-(b) hit (or
    None) — precomputed by the caller because it applies to every region
    inside that span.
    """
    kw = _keyword_hit(scoped_text.lower())
    if kw:
        return True, f"keyword:{kw}"
    if added_reference:
        return True, f"references_added_section:{added_reference}"
    return False, "no_ai_domain_signal"


def annotate_restatement_scope(
    restatement_text: str,
    added_section_numbers: set[str] | None = None,
) -> dict:
    """QA-9c: classify a restatement's whole subdivision tree in one pass.

    Returns a JSON-serializable annotation:
        {"engine_version": SCOPE_ENGINE_VERSION,
         "added_section_numbers": sorted list,
         "regions": [{"label", "start", "end", "in_scope", "reason"}, ...]}

    Regions partition [0, len(restatement_text)) in document order, with
    character offsets valid against `restatement_text` exactly as passed
    (the parser calls this with `text_content` as stored, so offsets stay
    valid against the DB row). `scope_for_offset()` over these regions
    reproduces `assess_extraction_scope`'s per-evidence verdicts exactly —
    the pre-refactor tests in tests/unit/test_restatement_scope.py are the
    parity proof.
    """
    added = added_section_numbers or set()
    regions: list[dict] = []

    top_spans = parse_top_level_subdivisions(restatement_text)
    if not top_spans:
        regions.append({
            "label": None, "start": 0, "end": len(restatement_text),
            "in_scope": True, "reason": "no_subdivision_structure",
        })
        return {
            "engine_version": SCOPE_ENGINE_VERSION,
            "added_section_numbers": sorted(added),
            "regions": regions,
        }

    if top_spans[0].start > 0:
        # Before the first top-level marker — the shared lead-in ("647.
        # Except as provided ... is guilty of a misdemeanor:") that every
        # subdivision depends on. Always in scope.
        regions.append({
            "label": None, "start": 0, "end": top_spans[0].start,
            "in_scope": True, "reason": "shared_preamble",
        })

    for top in top_spans:
        top_text = restatement_text[top.start : top.end]
        top_label = f"({top.label})"
        added_reference = _added_section_hit(top_text, added)
        second_spans = parse_second_level_subdivisions(top_text)

        if not second_spans:
            in_scope, reason = _classify(top_text, added_reference)
            regions.append({
                "label": top_label, "start": top.start, "end": top.end,
                "in_scope": in_scope, "reason": reason,
            })
            continue

        # Lead-in before the first child marker — scored against ONLY the
        # lead-in text, not the whole top-level span (which would smuggle a
        # child's keyword into a sentence that never mentions it, and make
        # the adjacency rule below unreachable).
        lead_text = top_text[: second_spans[0].start]
        in_scope, reason = _classify(lead_text, added_reference)
        if not in_scope:
            # Rule (c) adjacency: the shared lead-in of a top-level
            # subdivision stays in scope if ANY of that subdivision's own
            # children carry a keyword — siblings are judged independently
            # and never swept in by this rule.
            if any(
                _keyword_hit(top_text[c.start : c.end].lower())
                for c in second_spans
            ):
                in_scope, reason = True, "adjacent_to_in_scope_sibling"
        regions.append({
            "label": top_label,
            "start": top.start, "end": top.start + second_spans[0].start,
            "in_scope": in_scope, "reason": reason,
        })

        for child in second_spans:
            child_text = top_text[child.start : child.end]
            in_scope, reason = _classify(child_text, added_reference)
            regions.append({
                "label": f"{top_label}({child.label})",
                "start": top.start + child.start,
                "end": top.start + child.end,
                "in_scope": in_scope, "reason": reason,
            })

    return {
        "engine_version": SCOPE_ENGINE_VERSION,
        "added_section_numbers": sorted(added),
        "regions": regions,
    }


def scope_for_offset(annotation: dict, offset: int) -> dict:
    """Resolve a character offset to its region's verdict.

    Returns {"in_scope": bool, "subdivision": str | None, "reason": str} —
    the same shape assess_extraction_scope returns. An offset outside every
    region (malformed annotation, or text changed since annotation — which
    engine_version bumps should prevent) gets the safe in-scope default.
    """
    for region in annotation.get("regions", []):
        if region["start"] <= offset < region["end"]:
            return {
                "in_scope": region["in_scope"],
                "subdivision": region["label"],
                "reason": region["reason"],
            }
    return {"in_scope": True, "subdivision": None, "reason": "offset_out_of_annotation"}


def annotation_is_current(annotation: object) -> bool:
    """Is this a well-formed annotation from the CURRENT engine version?

    A stale or malformed annotation must be treated as absent (fall back to
    on-the-fly assessment), never silently applied.
    """
    return (
        isinstance(annotation, dict)
        and annotation.get("engine_version") == SCOPE_ENGINE_VERSION
        and isinstance(annotation.get("regions"), list)
    )


def assess_with_annotation(
    evidence_text: str,
    restatement_text: str,
    annotation: dict,
) -> dict:
    """Verdict for one evidence span, using a stored (parse-time) annotation.

    Same contract as assess_extraction_scope; the caller is responsible for
    checking annotation_is_current() first.
    """
    offset = find_evidence_offset(evidence_text, restatement_text)
    if offset is None:
        # Can't locate the evidence in this restatement — don't guess.
        # Leaving it in scope is the safe default: a false "in scope"
        # costs nothing extra (the status quo before this engine existed),
        # a false "out of scope" would silently hide a real row.
        return {"in_scope": True, "subdivision": None, "reason": "evidence_not_located"}
    return scope_for_offset(annotation, offset)


def assess_extraction_scope(
    evidence_text: str,
    restatement_text: str,
    *,
    added_section_numbers: set[str] | None = None,
) -> dict:
    """Is the extraction anchored at `evidence_text` in-scope of this
    restatement's AI/domain provisions?

    Returns {"in_scope": bool, "subdivision": str | None, "reason": str}.

    On-the-fly path: computes a fresh annotation and resolves the evidence
    against it. Callers holding a stored parse-time annotation (QA-9c)
    should use assess_with_annotation() instead and skip the recompute.
    """
    return assess_with_annotation(
        evidence_text,
        restatement_text,
        annotate_restatement_scope(restatement_text, added_section_numbers or set()),
    )


def build_inscope_excerpt(
    restatement_text: str,
    annotation: dict,
    *,
    section_label: str | None = None,
) -> str | None:
    """QA-9b (plan Phase 3, gated on the EA1-3 baseline): reduced agent
    input built from a restatement's in-scope regions.

    Returns the excerpt — a one-line context header, then the in-scope
    regions verbatim in document order with "[...]" markers where
    out-of-scope text was elided — or None when there is nothing useful to
    do: annotation not current, everything already in scope (full text is
    fine), or nothing in scope at all (feeding agents an empty shell is
    worse than the status quo; conservative fallback to full text).

    Every kept chunk is a verbatim slice of `restatement_text`, so evidence
    spans quoted from the excerpt still string-verify against the full
    stored passage — span verification MUST keep running against the full
    text, only the agent prompt input shrinks.
    """
    if not annotation_is_current(annotation):
        return None
    regions = annotation["regions"]
    kept = [r for r in regions if r.get("in_scope")]
    if len(kept) == len(regions) or not kept:
        return None

    header = (
        f"[Restatement excerpt — {section_label or 'restated code section'}: "
        "subdivisions outside this bill's AI/domain scheme are omitted; "
        "omitted text restates existing law unchanged.]"
    )
    parts: list[str] = [header]
    prev_end: int | None = None
    for region in kept:
        if prev_end is not None and region["start"] > prev_end:
            parts.append("[...]")
        elif prev_end is None and region["start"] > 0:
            parts.append("[...]")
        parts.append(restatement_text[region["start"] : region["end"]])
        prev_end = region["end"]
    if prev_end is not None and prev_end < len(restatement_text):
        parts.append("[...]")
    return "\n".join(parts)
