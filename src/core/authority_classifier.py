"""PNE-3b — instrument-authority classification (PN Ask 6).

Migration 056 flags a legal-overclaim risk: a binding statute renders identically
to non-binding agency guidance or an enforcement action. This module classifies a
law's *authority* deterministically from seed metadata (bill number, document
title, source URL) so PN's AuthorityTypeBadge can fire without a human
transcribing every record.

Operator decision (2026-07-06): heuristics + review queue. A confident,
signal-backed classification ships a label; anything ambiguous ships
``authority_type="unknown"`` with ``needs_review=True`` rather than a guessed
label — never silently assert "statute"/"binding" without positive evidence.
No LLM: this is a ~one-time, ~232-law metadata classification, not a per-run
extraction task. The residue (``authority_confidence in {"low"}`` /
``needs_review``) is the manual queue — surface it by filtering the emitted
law_summary rows.
"""

from __future__ import annotations

import re
from typing import Any

# Legislative bill-number prefixes (US state + federal chambers). A bill number
# is the strongest single signal that an instrument is enacted legislation.
_BILL_NUMBER_RE = re.compile(
    r"^\s*(sb|hb|ab|sf|hf|ld|hr|sr|sjr|hjr|scr|hcr|sm|hm|s|h|a)\s*\.?\s*-?\s*\d+",
    re.IGNORECASE,
)

# Title keyword → (authority_type, binding_effect, confidence). Order matters:
# the first matching rule wins, most specific first.
_TITLE_RULES: list[tuple[tuple[str, ...], str, str, str]] = [
    (("executive order",), "executive_order", "binding", "high"),
    (("guidance", "guidelines", "advisory", "best practices",
      "recommendation", "faq", "frequently asked"), "guidance", "non_binding", "high"),
    (("ordinance",), "ordinance", "binding", "high"),
    (("consent order", "enforcement action", "settlement", "cease and desist"),
     "enforcement_action", "binding", "medium"),
    (("rulemaking", "regulation", "final rule", "proposed rule"),
     "regulation", "binding", "high"),
    (("opinion",), "court_opinion", "binding", "medium"),
    (("act", "law", "statute", "bill", "code"), "statute", "binding", "high"),
]

_UNKNOWN = {
    "authority_type": "unknown",
    "binding_effect": "unknown",
    "issuing_body": None,
    "authority_confidence": "low",
    "needs_review": True,
}


def _title_signal(title: str) -> dict[str, Any] | None:
    low = title.lower()
    # Court opinions often read "X v. Y" with no other keyword.
    if re.search(r"\bv\.?\s+\w", low) and "act" not in low:
        return _make("court_opinion", "binding", "medium")
    for keywords, atype, binding, conf in _TITLE_RULES:
        if any(k in low for k in keywords):
            # A "proposed" instrument isn't in force yet — surface that rather
            # than labelling it binding.
            if binding == "binding" and "proposed" in low:
                binding = "proposed"
            return _make(atype, binding, conf)
    return None


def _url_signal(url: str) -> dict[str, Any] | None:
    low = url.lower()
    if "federalregister.gov" in low:
        binding = "proposed" if "proposed" in low else "binding"
        return _make("regulation", binding, "medium")
    if any(d in low for d in ("legislature", "capitol", "leg.state", "/bills/", "legis.")):
        return _make("statute", "binding", "medium")
    return None


def _make(atype: str, binding: str, conf: str) -> dict[str, Any]:
    return {
        "authority_type": atype,
        "binding_effect": binding,
        "issuing_body": None,
        "authority_confidence": conf,
        "needs_review": conf == "low",
    }


def classify_authority(
    bill_number: str | None,
    title: str | None,
    source_url: str | None,
) -> dict[str, Any]:
    """Classify a law's authority from metadata.

    Returns a dict with authority_type, binding_effect, issuing_body,
    authority_confidence (high/medium/low), and needs_review. A bill number
    is treated as decisive evidence of a statute; otherwise title keywords,
    then source-URL domain, are consulted. With no positive signal the result
    is ``unknown`` + ``needs_review`` — no fabricated label.
    """
    # A legislative bill number is the strongest signal — but a title that
    # clearly names a non-statute instrument (e.g. "guidance") should still win,
    # since a bill number can appear in tracker metadata for a guidance doc.
    title_sig = _title_signal(title) if title and title.strip() else None
    if title_sig and title_sig["authority_type"] not in ("statute",):
        return title_sig

    if bill_number and _BILL_NUMBER_RE.match(bill_number):
        return _make("statute", "binding", "high")

    if title_sig:  # statute-by-title
        return title_sig

    url_sig = _url_signal(source_url) if source_url and source_url.strip() else None
    if url_sig:
        return url_sig

    return dict(_UNKNOWN)
