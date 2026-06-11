"""Phase 4c: Recompute confidence scores using v3 weights against a gold fixture set.

v3 Weight Formula
-----------------
Component               Weight   Source
--------------------    ------   ------
Orrick alignment        30%      OrrickSimilarityResult.combined_score
IAPP alignment          20%      _IAPP_STATUS_TO_SCORE mapping
Evidence grounding      15%      Evidence spans verified ratio
Citation quality        10%      CitationVerifier score
Cross-validation        10%      CrossValidationAgent score
Gap penalty             5%       1 - gap_fraction from GapDetector
Analyst score           10%      Human-reviewer quality label [0.0-1.0]

GATING RULES (inherited from v2, unchanged in v3)
- If orrick_score is None AND iapp_score is None → Tier D forced (Orrick gate)
- If orrick_score is None but iapp_score is present → score capped below Tier B (< 0.70)

USAGE
-----
    python -m src.scripts.recompute_confidence \
        --fixture tests/unit/fixtures/gold_confidence.json \
        [--out results/v3_recompute.json]

STATUS: scaffold — the `analyst_score` component is placeholder until ANALYSIS-1
(lawyer eval set) is complete.  Running before that will treat analyst_score=0.0,
which underweights Tier A candidates.  The --skip-analyst flag leaves analyst
weight unallocated and redistributes it proportionally.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# v3 weights
# ---------------------------------------------------------------------------

V3_WEIGHTS: dict[str, float] = {
    "orrick": 0.30,
    "iapp": 0.20,
    "evidence": 0.15,
    "citation": 0.10,
    "cross_validation": 0.10,
    "gap": 0.05,
    "analyst": 0.10,
}

TIER_A = 0.85
TIER_B = 0.70
TIER_C = 0.50

# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------


def compute_v3_score(
    orrick_score: float | None,
    iapp_score: float | None,
    evidence_score: float,
    citation_score: float,
    cv_score: float | None,
    gap_score: float | None,
    analyst_score: float | None,
    schema_valid: bool,
    field_completeness: float,
    skip_analyst: bool = False,
) -> tuple[float, str]:
    """Apply v3 weights and return (total_score, tier).

    When a component score is None (not yet run), its weight is redistributed
    proportionally among available components to preserve the 0-1 range.

    Args:
        orrick_score: Raw Orrick combined_score [0.0-1.0], None if absent.
        iapp_score: IAPP alignment score [0.0-1.0], None if absent.
        evidence_score: Evidence grounding score [0.0-1.0].
        citation_score: Citation quality score [0.0-1.0].
        cv_score: Cross-validation score [0.0-1.0], None if not run.
        gap_score: Gap score (1 - gap_fraction) [0.0-1.0], None if not run.
        analyst_score: Human reviewer quality score [0.0-1.0], None until ANALYSIS-1.
        schema_valid: Whether Pydantic schema validation passed.
        field_completeness: Fraction of optional fields populated [0.0-1.0].
        skip_analyst: If True, redistribute analyst weight to other components.

    Returns:
        Tuple of (total_score [0.0-1.0], tier ["A"|"B"|"C"|"D"]).
    """
    # Orrick gate: force Tier D when no tracker data exists
    if orrick_score is None and iapp_score is None:
        return 0.0, "D"

    # Effective Orrick: interpolate from raw similarity (mirrors v2 logic)
    if orrick_score is not None:
        effective_orrick = _interpolate_orrick(orrick_score)
    else:
        effective_orrick = None

    effective_iapp = iapp_score  # already [0.0-1.0] from _IAPP_STATUS_TO_SCORE

    # Gather scored components with their weights
    components: list[tuple[str, float, float]] = [
        ("evidence", evidence_score, V3_WEIGHTS["evidence"]),
        ("citation", citation_score, V3_WEIGHTS["citation"]),
        ("completeness", field_completeness, 0.0),  # folded into gap weight if gap absent
    ]

    if effective_orrick is not None:
        components.append(("orrick", effective_orrick, V3_WEIGHTS["orrick"]))

    if effective_iapp is not None:
        components.append(("iapp", effective_iapp, V3_WEIGHTS["iapp"]))

    if cv_score is not None:
        components.append(("cross_validation", cv_score, V3_WEIGHTS["cross_validation"]))

    if gap_score is not None:
        components.append(("gap", gap_score, V3_WEIGHTS["gap"]))
    else:
        # Gap not run → promote field_completeness to gap weight
        components.append(("completeness_as_gap", field_completeness, V3_WEIGHTS["gap"]))

    if analyst_score is not None and not skip_analyst:
        components.append(("analyst", analyst_score, V3_WEIGHTS["analyst"]))
    elif not skip_analyst:
        # Analyst not yet labeled → treat as 0.0 (conservative until ANALYSIS-1)
        components.append(("analyst", 0.0, V3_WEIGHTS["analyst"]))
    # skip_analyst → weight redistributed proportionally below

    # Redistribute weights so they sum to 1.0 among present components
    total_weight = sum(w for _, _, w in components)
    if total_weight == 0:
        return 0.0, "D"

    score = sum(s * (w / total_weight) for _, s, w in components)
    score = max(0.0, min(1.0, round(score, 4)))

    # IAPP-only cap: without Orrick data, score cannot reach Tier B
    if effective_orrick is None and effective_iapp is not None:
        score = min(score, TIER_B - 0.001)

    tier = _score_to_tier(score)
    return score, tier


def _interpolate_orrick(raw: float) -> float:
    """Map Orrick combined_score to orrick_alignment [0.0-1.0].

    Mirrors the interpolation in compute_confidence():
    - raw < 0.10  → 0.0
    - raw [0.10, 0.25] → linear interpolation 0.0-1.0
    - raw > 0.25  → 1.0
    """
    if raw < 0.10:
        return 0.0
    if raw > 0.25:
        return 1.0
    return round((raw - 0.10) / (0.25 - 0.10), 4)


def _score_to_tier(score: float) -> str:
    if score >= TIER_A:
        return "A"
    if score >= TIER_B:
        return "B"
    if score >= TIER_C:
        return "C"
    return "D"


# ---------------------------------------------------------------------------
# Fixture evaluation
# ---------------------------------------------------------------------------


def evaluate_fixture(fixture_path: Path, skip_analyst: bool = False) -> dict[str, Any]:
    """Load a gold fixture file and compute v3 scores for all rows.

    Returns a results dict with per-fixture predictions and aggregate metrics.
    """
    data = json.loads(fixture_path.read_text())
    fixtures = data.get("fixtures", [])

    results: list[dict] = []
    correct = 0
    total_labeled = 0

    for row in fixtures:
        if row.get("_comment"):
            pass  # placeholder rows: still evaluated

        v3_score, v3_tier = compute_v3_score(
            orrick_score=row.get("orrick_similarity_score"),
            iapp_score=row.get("iapp_alignment_score"),
            evidence_score=row.get("evidence_grounding_score") or 0.0,
            citation_score=row.get("citation_quality_score") or 0.0,
            cv_score=row.get("cross_validation_score"),
            gap_score=row.get("gap_score"),
            analyst_score=row.get("analyst_score"),
            schema_valid=row.get("schema_valid", True),
            field_completeness=row.get("field_completeness") or 0.0,
            skip_analyst=skip_analyst,
        )

        gold_tier = row.get("gold_tier")
        match = (v3_tier == gold_tier) if gold_tier else None

        if gold_tier:
            total_labeled += 1
            if match:
                correct += 1

        results.append({
            "fixture_id": row.get("fixture_id"),
            "law_id": row.get("law_id"),
            "agent_name": row.get("agent_name"),
            "gold_tier": gold_tier,
            "v3_predicted_tier": v3_tier,
            "v3_total_score": v3_score,
            "tier_match": match,
        })

    accuracy = correct / total_labeled if total_labeled > 0 else None

    return {
        "schema_version": data.get("_schema_version", "unknown"),
        "v3_weights": V3_WEIGHTS,
        "skip_analyst": skip_analyst,
        "total_fixtures": len(fixtures),
        "labeled_fixtures": total_labeled,
        "tier_accuracy": accuracy,
        "correct_predictions": correct,
        "results": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Recompute confidence scores with v3 weights against gold fixtures."
    )
    p.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/unit/fixtures/gold_confidence.json"),
        help="Path to gold fixture JSON file.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write results JSON to this path (default: stdout).",
    )
    p.add_argument(
        "--skip-analyst",
        action="store_true",
        default=False,
        help="Redistribute analyst weight rather than scoring it as 0.0.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not args.fixture.exists():
        print(f"ERROR: fixture file not found: {args.fixture}", file=sys.stderr)
        return 1

    report = evaluate_fixture(args.fixture, skip_analyst=args.skip_analyst)
    output = json.dumps(report, indent=2)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output)
        print(f"Results written to {args.out}")
        if report["tier_accuracy"] is not None:
            print(
                f"Tier accuracy: {report['tier_accuracy']:.1%} "
                f"({report['correct_predictions']}/{report['labeled_fixtures']})"
            )
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
