"""Agent routing — pure text-signal functions for selecting agents per passage.

Deliberately free of SQLAlchemy, agent instances, and any I/O so every function
here is directly unit-testable without a database fixture or LLM.

Public API
----------
is_boilerplate(text)           → bool
route_by_signal(text, names, triage_result) → set[str] | None
select_agent_names(text, names, triage_result, recall_sample_rate) → set[str]

The caller (extractor.py) maps the returned name-set back to actual agent
objects:
    selected = {k: v for k, v in all_agents.items() if k in selected_names}
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Passage-exclusion patterns
# ---------------------------------------------------------------------------

_BOILERPLATE_PATTERN = re.compile(
    r"^\s*("
    r"table\s+of\s+contents"
    r"|chapter\s+\d+"
    r"|part\s+\d+\s*[-—]\s*$"
    r"|article\s+\d+\s*$"
    r"|_{5,}"       # separator lines
    r"|\.{5,}"      # dot leaders (TOC)
    r"|page\s+\d+"
    r")\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_ENACTING_CLAUSE_PATTERN = re.compile(
    r"^\s*(be\s+it\s+enacted|the\s+people\s+of\s+the\s+state\s+of"
    r"|this\s+act\s+(shall\s+be\s+known\s+as|may\s+be\s+cited\s+as)"
    r"|approved\s+(by\s+the\s+governor|on)"
    r"|signed\s+(by\s+the\s+governor|into\s+law)"
    r"|effective\s+immediately)\b",
    re.IGNORECASE,
)

_DEFINITIONS_SECTION_HEADER = re.compile(
    r"^\s*(definitions|as\s+used\s+in\s+this\s+(act|section|chapter|article|part))\s*[:.]?\s*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Signal patterns — maps to the agent(s) each pattern activates
# ---------------------------------------------------------------------------

_DEFINITION_SIGNALS = re.compile(
    r'\b(?:defin(?:e[sd]?|ition|ing)|means\b|as used in|for (?:the )?purposes? of)\b',
    re.IGNORECASE,
)
_OBLIGATION_SIGNALS = re.compile(
    r'\b(?:shall|must|require[sd]?|obligat|mandate[sd]?|prohibit|may not|'
    r'responsible for|duty to|ensure that|is the policy|it is the policy)\b',
    re.IGNORECASE,
)
_RIGHTS_SIGNALS = re.compile(
    r'\b(?:right to|entitled|opt[- ]?out|notice to|consent|'
    r'appeal|recourse|due process|grievance|redress)\b',
    re.IGNORECASE,
)
_THRESHOLD_SIGNALS = re.compile(
    r'\b(?:threshold[s]?|exception[s]?|exempt(?:ion[s]?|ed)?|exclusion[s]?|waiver[s]?|'
    r'does not apply|not subject to|carve[- ]?out[s]?|'
    r'fewer than|more than|exceed[s]?|minimum|maximum)\b',
    re.IGNORECASE,
)
_COMPLIANCE_SIGNALS = re.compile(
    r'\b(?:enforc\w*|penalt\w*|fine[sd]?|violation[s]?|compliance|audit[s]?|'
    r'inspection[s]?|reporting|register|certif\w*|oversight|'
    r'attorney general|commission|agency)\b',
    re.IGNORECASE,
)
_PREEMPTION_SIGNALS = re.compile(
    r'\b(?:preempt|pre-empt|supersede|federal|supremacy|'
    r'state law|local (?:law|ordinance)|uniform|'
    r'notwithstanding any (?:other|state|local))\b',
    re.IGNORECASE,
)

# Ordered list of (pattern, agent_names_it_signals).
_SIGNAL_MAP: list[tuple[re.Pattern, list[str]]] = [
    (_DEFINITION_SIGNALS,  ["definition_actor"]),
    (_OBLIGATION_SIGNALS,  ["obligation"]),
    (_RIGHTS_SIGNALS,      ["rights_protection"]),
    (_THRESHOLD_SIGNALS,   ["threshold_exception"]),
    (_COMPLIANCE_SIGNALS,  ["compliance_mechanism"]),
    (_PREEMPTION_SIGNALS,  ["preemption"]),
    # _AMBIGUITY_SIGNALS removed — ambiguity agent retired
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_boilerplate(text: str) -> bool:
    """Return True if the passage is structural boilerplate with no extractable content.

    Catches table-of-contents entries, bare chapter/article/part headers, page
    numbers, separator lines, and short enacting/signing clauses.
    """
    stripped = text.strip()
    if _BOILERPLATE_PATTERN.fullmatch(stripped):
        return True
    if _ENACTING_CLAUSE_PATTERN.match(stripped) and len(stripped) < 300:
        return True
    return False


def route_by_signal(
    text: str,
    all_agent_names: set[str],
    triage_result=None,
) -> set[str] | None:
    """Use text signals + triage metadata to select a subset of agent names.

    Returns a subset of ``all_agent_names`` when routing is conclusive, or
    ``None`` when signals are absent or so broad that running all agents is
    the safer choice.

    Args:
        text: Raw passage text (pre-stripped is fine; this function strips internally).
        all_agent_names: The full set of agent names available for this run.
        triage_result: Optional SectionTriageResult ORM object (or any object with
            ``ai_signals`` and ``llm_reasoning`` str attributes).  Its text is
            appended to the signal corpus to enable richer matching.

    Returns:
        Subset of agent names when routing is conclusive; None otherwise.
    """
    signal_text = text.strip().lower()

    if triage_result is not None:
        if getattr(triage_result, "ai_signals", None):
            signal_text += " " + triage_result.ai_signals.lower()
        if getattr(triage_result, "llm_reasoning", None):
            signal_text += " " + triage_result.llm_reasoning.lower()

    signaled: set[str] = set()
    for pattern, agent_names in _SIGNAL_MAP:
        if pattern.search(signal_text):
            signaled.update(agent_names)

    # No signals → don't filter; run everything (recall-safe).
    if not signaled:
        return None

    # Nearly all agents signaled → not worth filtering; run everything.
    if len(signaled) >= len(all_agent_names) - 1:
        return None

    # Return only names that are both signaled and in the active agent set.
    return signaled & all_agent_names


@dataclass(frozen=True)
class RoutingDecision:
    """SFH-1d (audit SF-02): full routing decision, sampling made visible.

    ``selected`` is what actually runs. ``routed`` is what signal routing
    would have selected regardless of sampling — kept so the recall delta
    (extractions produced by agents routing would have SKIPPED) is computable.
    ``bypassed`` is True when this passage was recall-sampled to the full
    battery.
    """

    selected: frozenset
    routed: frozenset
    bypassed: bool


def select_agent_names_with_decision(
    text: str,
    all_agent_names: set[str],
    triage_result=None,
    recall_sample_rate: float = 0.0,
) -> RoutingDecision:
    """Like select_agent_names, but returns the full RoutingDecision.

    RR7c pays for 5% of passages to run the full agent battery specifically
    so routing false-narrowing can be measured — but nothing ever computed
    the measurement (audit SF-02). This variant always computes what routing
    *would* have chosen, so the caller can compare it against what the full
    battery actually found on sampled passages.
    """
    if is_boilerplate(text):
        empty = frozenset()
        return RoutingDecision(selected=empty, routed=empty, bypassed=False)

    stripped = text.strip()

    # Bare definitions section header → definition_actor only (deterministic, like boilerplate).
    if _DEFINITIONS_SECTION_HEADER.fullmatch(stripped):
        only_def = frozenset({"definition_actor"} & all_agent_names)
        return RoutingDecision(selected=only_def, routed=only_def, bypassed=False)

    # What routing would choose, computed unconditionally (None = ambiguous →
    # all agents; that's routing's own fallback, not a sampling bypass).
    routed = route_by_signal(text, all_agent_names, triage_result)
    routed_set = frozenset(routed) if routed is not None else frozenset(all_agent_names)

    # RR7c — Recall sampling: bypass routing for a random fraction of passages.
    if recall_sample_rate > 0.0 and random.random() < recall_sample_rate:
        return RoutingDecision(
            selected=frozenset(all_agent_names),
            routed=routed_set,
            bypassed=True,
        )

    return RoutingDecision(selected=routed_set, routed=routed_set, bypassed=False)


def select_agent_names(
    text: str,
    all_agent_names: set[str],
    triage_result=None,
    recall_sample_rate: float = 0.0,
) -> set[str]:
    """Select the set of agent names that should run on a passage.

    Returns an empty set if the passage is pure boilerplate (no agents should
    run at all).  Returns a subset of ``all_agent_names`` based on text signals
    when routing is conclusive; returns all names when routing is ambiguous.

    The ``recall_sample_rate`` parameter implements RR7c recall sampling: a
    random fraction of passages bypass signal-based routing entirely so that
    abstention false-negatives can be measured over time.

    Thin wrapper over select_agent_names_with_decision (SFH-1d) — callers that
    need the recall delta should use that variant.

    Args:
        text: Passage text.
        all_agent_names: Available agent names for this run.
        triage_result: Optional triage result for richer signal matching.
        recall_sample_rate: Fraction [0, 1] of passages that bypass routing.

    Returns:
        Set of agent names to run (may be empty).
    """
    return set(
        select_agent_names_with_decision(
            text, all_agent_names, triage_result, recall_sample_rate
        ).selected
    )
