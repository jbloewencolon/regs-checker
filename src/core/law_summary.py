"""PNE-3a — law-level covered-entity rollup (PN Ask 5).

Policy Navigator's applicability engine needs structured, numeric covered-entity
thresholds *per law* (`min_employees`, `min_revenue`, `consumer_count_trigger`,
`small_business_exempt`, `private_right_of_action`) so it can exclude orgs below
a threshold and flag private-right-of-action laws automatically. RC extracts the
underlying signals as many separate per-passage extractions; this module rolls
them up to one record per law.

Emission (operator decision 2026-07-06): the rollup ships as a synthetic
``extraction_type="law_summary"`` row in the existing `synced_extractions`
stream (see `sync_extractions.sync_law_summaries`), keeping RC to
payload-only — PN's ingestion routes it to `fact_laws` columns.

Honesty rule (learned from the coverage audit — 221 PN rows carried a default
`false` that reads as an affirmative "no private right of action" legal claim):
this rollup never asserts a boolean from *absence*. A field is only `True`/`False`
when an extraction positively says so; with no signal it is ``None`` (= not
assessed), so PN can distinguish "assessed: no" from "not assessed".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.core.authority_classifier import classify_authority
from src.core.pn_crosswalk import derive_trigger

# Synthetic system_a_extraction_id space for law_summary rows. Real RC extraction
# ids are DB serials that will never approach this, so BASE + family_id is
# collision-proof and deterministic (re-syncable, idempotent via upsert). The
# id-cursor leg MUST exclude this range (see sync_extractions._get_cursor) or a
# 2-billion synthetic id would poison the MAX(id) watermark and starve real
# extractions. law_summary rows are identifiable by extraction_type OR id range.
LAW_SUMMARY_ID_BASE = 2_000_000_000

# Exception text signalling a small-business / small-entity carve-out.
_SMALL_BUSINESS_MARKERS = (
    "small business",
    "small businesses",
    "small entity",
    "small entities",
    "smaller business",
)


def _numeric(value: Any) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _collect_threshold_minimum(
    triggers: list[dict], trigger_type: str
) -> tuple[float | None, list[str]]:
    """Smallest numeric trigger_value of the given type = the applicability floor.

    A law with "50 employees" and "500 employees" thresholds for different
    obligations begins covering entities at the *smaller* floor (50), so the
    law-level minimum is the min across matching triggers. Non-numeric trigger
    values (e.g. "high-risk systems") are ignored for the numeric floor.
    """
    values: list[float] = []
    for t in triggers:
        if t.get("trigger_type") == trigger_type:
            n = _numeric(t.get("trigger_value"))
            if n is not None:
                values.append(n)
    if not values:
        return None, []
    return min(values), [trigger_type]


def build_law_summary(extractions: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll a law's extractions up to covered-entity fields.

    Args:
        extractions: one dict per extraction for a single law, each shaped
            ``{"extraction_type": str, "payload": dict}`` with the raw RC
            payload (pre-adapter). Obligation payloads carry the enforcement
            block; threshold payloads carry the threshold fields.

    Returns:
        A rollup dict with the five covered-entity fields plus a ``_provenance``
        map recording which signals contributed. Numeric floors are ``None``
        when no numeric threshold of that type exists; booleans are ``None``
        when no extraction positively asserts them (never False-from-absence).
    """
    triggers: list[dict] = []
    proa_true = False
    proa_false = False
    small_business_exempt: bool | None = None
    proa_sources: list[str] = []
    sb_sources: list[str] = []

    for item in extractions:
        etype = item.get("extraction_type") or ""
        payload = item.get("payload") or {}
        if not isinstance(payload, dict):
            continue

        # Threshold signals → trigger predicates (reuse the PNE-2d parser).
        if etype in ("threshold", "exception"):
            trig = derive_trigger(payload)
            if trig:
                triggers.append(trig)
            # Small-business exemption from exception text.
            for exc in payload.get("exceptions") or []:
                text = ""
                if isinstance(exc, dict):
                    text = f"{exc.get('exception_type', '')} {exc.get('description', '')}"
                elif isinstance(exc, str):
                    text = exc
                if any(m in text.lower() for m in _SMALL_BUSINESS_MARKERS):
                    small_business_exempt = True
                    sb_sources.append("exception")

        # Enforcement signals → private right of action (obligation + bill-level).
        enforcement = payload.get("enforcement")
        proa = None
        if isinstance(enforcement, dict):
            proa = enforcement.get("private_right_of_action")
        elif "private_right_of_action" in payload:
            proa = payload.get("private_right_of_action")
        if proa is True:
            proa_true = True
            proa_sources.append(etype or "enforcement")
        elif proa is False:
            proa_false = True

    min_employees, emp_src = _collect_threshold_minimum(triggers, "employee_count")
    min_revenue, rev_src = _collect_threshold_minimum(triggers, "revenue")
    consumer_trigger, con_src = _collect_threshold_minimum(triggers, "consumer_count")

    # Booleans: positive assertion wins; absence stays None (not False).
    if proa_true:
        private_right_of_action: bool | None = True
    elif proa_false:
        private_right_of_action = False
    else:
        private_right_of_action = None

    return {
        "min_employees": int(min_employees) if min_employees is not None else None,
        "min_revenue": min_revenue,
        "consumer_count_trigger": (
            int(consumer_trigger) if consumer_trigger is not None else None
        ),
        "small_business_exempt": small_business_exempt,
        "private_right_of_action": private_right_of_action,
        "_provenance": {
            "min_employees_from": emp_src,
            "min_revenue_from": rev_src,
            "consumer_count_trigger_from": con_src,
            "small_business_exempt_from": sb_sources,
            "private_right_of_action_from": proa_sources,
        },
    }


def build_law_summary_payload(
    extractions: list[dict[str, Any]],
    bill_number: str | None,
    title: str | None,
    source_url: str | None,
) -> dict[str, Any]:
    """Full law_summary payload: covered-entity rollup + authority classification.

    Combines PNE-3a (Ask 5) rollup fields with PNE-3b (Ask 6) authority fields
    into the single JSONB payload the synthetic law_summary row carries.
    """
    payload = build_law_summary(extractions)
    payload.update(classify_authority(bill_number, title, source_url))
    return payload


def build_law_summary_row(
    *,
    family_id: int,
    law_id: int,
    jurisdiction_code: str,
    extractions: list[dict[str, Any]],
    bill_number: str | None = None,
    title: str | None = None,
    source_url: str | None = None,
    synced_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a complete synced_extractions row for a law's synthetic summary.

    Returns a dict keyed to synced_extractions columns. The synthetic
    ``system_a_extraction_id`` is ``LAW_SUMMARY_ID_BASE + family_id`` so it is
    stable, collision-proof, and idempotent under upsert.

    confidence_score/confidence_tier are required (NOT NULL) columns that carry
    no probabilistic meaning here — a law_summary is a deterministic rollup, not
    a scored model extraction — so they are set to a fixed 1.0 / "A" sentinel.
    PN can special-case extraction_type='law_summary' rather than reading these
    as a model confidence.
    """
    payload = build_law_summary_payload(extractions, bill_number, title, source_url)
    return {
        "system_a_extraction_id": LAW_SUMMARY_ID_BASE + family_id,
        "law_id": law_id,
        "extraction_type": "law_summary",
        "payload": payload,
        "evidence_spans": [],
        # Deterministic rollup — the 1.0/"A" is a NOT-NULL sentinel, not a model score.
        "confidence_score": 1.0,
        "confidence_tier": "A",
        "jurisdiction_code": jurisdiction_code,
        "review_status": None,
        "model_id": None,
        "section_reference": None,
        "source_text_excerpt": None,
        "system_a_created_at": None,
        "synced_at": synced_at or datetime.now(UTC),
    }
