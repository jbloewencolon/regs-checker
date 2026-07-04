"""Enforcement normalizer (Phase 2c).

Enforcement facts for a law are scattered across four sources:

  1. The bill-level ``enforcement_agent`` row (one per law, full-bill scope).
  2. ``obligation.enforcement`` (``EnforcementInfo``) embedded on every
     obligation extraction — passage-scoped, often partial.
  3. Orrick parsed facts (``parse_enforcement_facts``) — the trusted tracker.
  4. (Future) IAPP parsed facts — same shape, ingested in Phase 4b.

Because no single source is complete, a law's enforcement picture looks
sparse when you read any one of them (root cause of C-8 enforcement
sparsity).  This module merges them into **one enforcement record per law**
without re-running any LLM agent.

Trust model: every merged field records which source supplied it
(``_provenance``).  Field precedence follows the trust bar — Orrick (and
later IAPP) is the most trusted, then the dedicated bill-level agent, then
the passage-level obligation rows.  The first non-null value in precedence
order wins, so a fact captured by *any* source surfaces rather than being
lost.
"""

from __future__ import annotations

from typing import Any

# Canonical enforcement fields in the normalized record.
ENFORCEMENT_FIELDS = (
    "enforcing_body",
    "max_civil_penalty_usd",
    "penalty_per",
    "cure_period_days",
    "private_right_of_action",
    "criminal_penalties",
    "criminal_penalty_description",
    "enforcement_text",
)

# Source labels, ordered most-trusted first. Field precedence walks this list.
SOURCE_PRECEDENCE = ("orrick", "iapp", "bill_level", "obligation")

# EA5-2: fields where cross-source disagreement is worth a human look even
# though precedence already resolves *a* value. Precedence answers "which
# source do we trust more"; it says nothing about whether the losing
# source's value is a typo, a stale draft, or a genuine substantive
# difference (e.g. Orrick says no private right of action, but the bill text
# itself creates one) — that distinction matters for a legal-defensibility
# product and is currently invisible once precedence silently picks a
# winner. Scoped to the three fields the EA5-2 review finding named, not
# every field, to avoid flooding review with cosmetic differences (e.g.
# `enforcement_text` free-text quotes will almost always differ verbatim
# across sources without being a substantive disagreement).
ENFORCEMENT_CONFLICT_FIELDS = (
    "max_civil_penalty_usd",
    "private_right_of_action",
    "cure_period_days",
)


def _detect_enforcement_conflicts(
    by_source: dict[str, dict[str, Any]],
    record: dict[str, Any],
    provenance: dict[str, str],
) -> dict[str, Any]:
    """Find fields where two or more sources reported different values.

    Returns {field: {selected_value, selected_source, contributions}} for
    each conflicting field, where contributions lists every source that
    populated the field and what it said (in precedence order) — not just
    the value that lost.
    """
    conflicts: dict[str, Any] = {}
    for field in ENFORCEMENT_CONFLICT_FIELDS:
        contributions = []
        for source in SOURCE_PRECEDENCE:
            val = by_source[source].get(field)
            if val is not None and val != "":
                contributions.append({"source": source, "value": val})

        distinct_values = {c["value"] for c in contributions}
        if len(distinct_values) > 1:
            conflicts[field] = {
                "selected_value": record.get(field),
                "selected_source": provenance.get(field),
                "contributions": contributions,
            }
    return conflicts


def _coalesce_obligation_enforcements(
    obligation_enforcements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Collapse many passage-level ``EnforcementInfo`` dicts into one.

    Obligation rows are partial and numerous. For each field, take the first
    non-null value across the rows; for ``max_civil_penalty_usd`` take the
    maximum (the largest stated penalty is the law's ceiling).
    """
    merged: dict[str, Any] = {f: None for f in ENFORCEMENT_FIELDS}
    if not obligation_enforcements:
        return merged

    max_penalty: int | None = None
    for enf in obligation_enforcements:
        if not enf:
            continue
        for field in ENFORCEMENT_FIELDS:
            if field == "max_civil_penalty_usd":
                val = enf.get(field)
                if isinstance(val, int):
                    max_penalty = val if max_penalty is None else max(max_penalty, val)
                continue
            if merged[field] is None and enf.get(field) is not None:
                merged[field] = enf.get(field)

    merged["max_civil_penalty_usd"] = max_penalty
    return merged


def normalize_enforcement(
    bill_level: dict[str, Any] | None = None,
    obligation_enforcements: list[dict[str, Any]] | None = None,
    orrick_facts: dict[str, Any] | None = None,
    iapp_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge enforcement facts from all sources into one record per law.

    Args:
        bill_level: Payload from the bill-level ``enforcement_agent`` row.
        obligation_enforcements: List of ``EnforcementInfo`` dicts pulled from
            each obligation extraction's ``enforcement`` field.
        orrick_facts: Output of ``parse_enforcement_facts`` for this law.
        iapp_facts: Same shape as ``orrick_facts`` (Phase 4b; optional now).

    Returns:
        A dict with the canonical enforcement fields, plus:
          ``_provenance``      — {field: source} for each populated field
          ``_sources_present`` — sources that contributed at least one value
          ``_has_enforcement`` — True if any field is populated
          ``_enforcement_conflicts`` — {field: detail} for fields in
              ``ENFORCEMENT_CONFLICT_FIELDS`` where sources disagree
          ``_has_enforcement_conflict`` — True if any conflict was found
    """
    by_source: dict[str, dict[str, Any]] = {
        "orrick": orrick_facts or {},
        "iapp": iapp_facts or {},
        "bill_level": bill_level or {},
        "obligation": _coalesce_obligation_enforcements(obligation_enforcements or []),
    }

    record: dict[str, Any] = {f: None for f in ENFORCEMENT_FIELDS}
    provenance: dict[str, str] = {}
    sources_present: set[str] = set()

    for field in ENFORCEMENT_FIELDS:
        for source in SOURCE_PRECEDENCE:
            val = by_source[source].get(field)
            if val is not None and val != "":
                record[field] = val
                provenance[field] = source
                sources_present.add(source)
                break

    record["_provenance"] = provenance
    record["_sources_present"] = [
        s for s in SOURCE_PRECEDENCE if s in sources_present
    ]
    record["_has_enforcement"] = bool(provenance)

    conflicts = _detect_enforcement_conflicts(by_source, record, provenance)
    record["_enforcement_conflicts"] = conflicts
    record["_has_enforcement_conflict"] = bool(conflicts)
    return record


def normalize_enforcement_for_law(db, document_version_id: int) -> dict[str, Any]:
    """Gather all enforcement sources for one law and normalize them.

    DB-facing wrapper around :func:`normalize_enforcement`. Pulls:
      - the bill-level ``enforcement_agent`` row,
      - every obligation extraction's embedded ``enforcement`` block,
      - Orrick parsed enforcement facts (via bill context),
    then merges them.  Returns the normalized record (does not persist).
    """
    from sqlalchemy import select

    from src.db.models import (
        BillLevelExtraction,
        Extraction,
        ExtractionType,
        NormalizedSourceRecord,
    )

    # 1. Bill-level enforcement_agent row
    bill_row = db.scalars(
        select(BillLevelExtraction).where(
            BillLevelExtraction.document_version_id == document_version_id,
            BillLevelExtraction.agent_name == "enforcement_agent",
        )
    ).first()
    bill_payload = bill_row.payload if bill_row else None

    # 2. Embedded obligation.enforcement across this law's obligation rows
    obligation_payloads = db.scalars(
        select(Extraction.payload)
        .join(
            NormalizedSourceRecord,
            Extraction.source_record_id == NormalizedSourceRecord.id,
        )
        .where(
            NormalizedSourceRecord.document_version_id == document_version_id,
            Extraction.extraction_type == ExtractionType.obligation,
        )
    ).all()
    obligation_enforcements = [
        p["enforcement"]
        for p in obligation_payloads
        if isinstance(p, dict) and isinstance(p.get("enforcement"), dict)
    ]

    # 3. Orrick parsed enforcement facts (best-effort)
    orrick_facts = None
    try:
        from src.core.bill_context import get_or_build_bill_context
        from src.ingestion.orrick_facts_parser import parse_orrick_facts

        bill_ctx = get_or_build_bill_context(db, document_version_id)
        orrick_facts = parse_orrick_facts(bill_ctx).enforcement or None
    except Exception:
        orrick_facts = None

    return normalize_enforcement(
        bill_level=bill_payload,
        obligation_enforcements=obligation_enforcements,
        orrick_facts=orrick_facts,
    )
