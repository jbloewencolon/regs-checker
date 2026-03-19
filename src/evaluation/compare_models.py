"""Parallel model comparison — evaluate Claude Haiku vs local LLMs.

Processes gold-standard passages through both provider configurations
side by side and compares extraction quality metrics:

  - Evidence span verification rate (do local models quote verbatim?)
  - Confidence tier distributions (A/B/C/D)
  - JSON validity rate (does the output parse cleanly?)
  - Field-level precision/recall

Usage:
    python -m src.evaluation.compare_models [--limit N] [--gold-dir PATH]
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from src.agents.ambiguity import AmbiguityAgent
from src.agents.base import BaseExtractionAgent, ExtractionResult
from src.agents.definition_actor import DefinitionActorAgent
from src.agents.obligation import ObligationAgent
from src.agents.threshold_exception import ThresholdExceptionAgent
from src.core.config import settings
from src.core.llm_provider import (
    AnthropicProvider,
    BaseLLMProvider,
    LocalLLMProvider,
    get_provider,
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ModelMetrics:
    """Metrics collected for a single provider configuration."""

    model_label: str
    total_calls: int = 0
    json_valid: int = 0
    json_invalid: int = 0
    total_extractions: int = 0
    evidence_spans_total: int = 0
    evidence_spans_verified: int = 0
    confidence_tiers: dict[str, int] = field(
        default_factory=lambda: {"A": 0, "B": 0, "C": 0, "D": 0}
    )
    agent_errors: int = 0
    total_latency_ms: float = 0.0

    @property
    def json_validity_rate(self) -> float:
        total = self.json_valid + self.json_invalid
        return self.json_valid / total if total > 0 else 0.0

    @property
    def evidence_verification_rate(self) -> float:
        if self.evidence_spans_total == 0:
            return 0.0
        return self.evidence_spans_verified / self.evidence_spans_total

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.total_calls if self.total_calls > 0 else 0.0


@dataclass
class ComparisonResult:
    """Full side-by-side comparison result."""

    anthropic_metrics: ModelMetrics
    local_metrics: ModelMetrics
    passages_evaluated: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent wrappers that allow provider override
# ---------------------------------------------------------------------------


def _make_agents_with_provider(
    provider: BaseLLMProvider,
    use_model_overrides: bool,
) -> dict[str, BaseExtractionAgent]:
    """Create agents sharing a specific provider.

    When use_model_overrides=False (Anthropic path), agents ignore their
    model_override attributes.  When True (local path), they use them.
    """
    agent_classes = {
        "obligation": ObligationAgent,
        "definition_actor": DefinitionActorAgent,
        "threshold_exception": ThresholdExceptionAgent,
        "ambiguity": AmbiguityAgent,
    }

    agents: dict[str, BaseExtractionAgent] = {}
    for name, cls in agent_classes.items():
        agent = cls.__new__(cls)
        # Manually init to inject the provider
        agent._provider = provider
        from src.agents.prompt_loader import load_prompt_template
        agent._template = load_prompt_template(agent.agent_name)

        if not use_model_overrides:
            # For Anthropic: suppress model_override so it uses the API model
            agent.model_override = None

        agents[name] = agent

    return agents


# ---------------------------------------------------------------------------
# Core comparison logic
# ---------------------------------------------------------------------------


def _run_agent_safe(
    agent: BaseExtractionAgent,
    passage: str,
    context: dict,
    metrics: ModelMetrics,
) -> ExtractionResult | None:
    """Run an agent with error handling and metrics collection."""
    metrics.total_calls += 1
    start = time.monotonic()

    try:
        result = agent.extract(passage, context)
        elapsed_ms = (time.monotonic() - start) * 1000
        metrics.total_latency_ms += elapsed_ms
        metrics.json_valid += 1

        if result.abstention is not None:
            return result

        metrics.total_extractions += len(result.extractions)

        for item in result.extractions:
            spans = item.get("evidence_spans", [])
            metrics.evidence_spans_total += len(spans)
            metrics.evidence_spans_verified += sum(
                1 for s in spans if s.get("verified")
            )

        return result

    except json.JSONDecodeError:
        metrics.json_invalid += 1
        metrics.total_latency_ms += (time.monotonic() - start) * 1000
        return None
    except Exception as e:
        metrics.agent_errors += 1
        metrics.total_latency_ms += (time.monotonic() - start) * 1000
        logger.warning("comparison_agent_error", error=str(e))
        return None


def run_comparison(
    gold_standard_dir: str | None = None,
    limit: int | None = None,
) -> ComparisonResult:
    """Run side-by-side comparison of Anthropic vs local models.

    Args:
        gold_standard_dir: Path to gold-standard JSON fixtures.
        limit: Max passages to evaluate (None = all).

    Returns:
        ComparisonResult with metrics for both configurations.
    """
    gold_dir = Path(gold_standard_dir or settings.gold_standard_dir)

    # Load test cases
    cases = []
    if gold_dir.exists():
        for filepath in sorted(gold_dir.glob("*.json")):
            with open(filepath) as f:
                cases.append(json.load(f))

    if not cases:
        print(f"No gold-standard cases found in {gold_dir}")
        return ComparisonResult(
            anthropic_metrics=ModelMetrics(model_label="anthropic"),
            local_metrics=ModelMetrics(model_label="local"),
        )

    if limit:
        cases = cases[:limit]

    print(f"Evaluating {len(cases)} passages...")

    # --- Set up providers ---
    anthropic_metrics = ModelMetrics(model_label=f"anthropic:{settings.extraction_model}")
    local_metrics = ModelMetrics(model_label="local:per-agent-models")

    # Create Anthropic agents (if API key available)
    has_anthropic = bool(settings.anthropic_api_key)
    anthropic_agents = None
    if has_anthropic:
        try:
            anthropic_provider = get_provider("anthropic")
            anthropic_agents = _make_agents_with_provider(
                anthropic_provider, use_model_overrides=False
            )
        except Exception as e:
            print(f"Warning: Could not init Anthropic provider: {e}")
            has_anthropic = False

    # Create local agents (if URL configured)
    has_local = bool(settings.local_llm_url)
    local_agents = None
    if has_local:
        try:
            local_provider = get_provider("local")
            local_agents = _make_agents_with_provider(
                local_provider, use_model_overrides=True
            )
        except Exception as e:
            print(f"Warning: Could not init local provider: {e}")
            has_local = False

    if not has_anthropic and not has_local:
        print("Error: Neither Anthropic nor local provider is configured.")
        return ComparisonResult(
            anthropic_metrics=anthropic_metrics,
            local_metrics=local_metrics,
        )

    result = ComparisonResult(
        anthropic_metrics=anthropic_metrics,
        local_metrics=local_metrics,
    )

    # --- Process each passage ---
    agent_names = ["obligation", "definition_actor", "threshold_exception", "ambiguity"]

    for i, case in enumerate(cases):
        passage = case["passage_text"]
        context = {
            "document_title": case.get("source_document"),
            "section_path": case.get("section_path"),
        }
        passage_id = case.get("passage_id", f"case_{i}")

        print(f"\n[{i + 1}/{len(cases)}] {passage_id}")

        for agent_name in agent_names:
            expected = case.get("expected_extractions", {}).get(agent_name)

            # Run Anthropic
            if has_anthropic and anthropic_agents:
                agent = anthropic_agents[agent_name]
                _run_agent_safe(agent, passage, context, anthropic_metrics)

            # Run Local
            if has_local and local_agents:
                agent = local_agents[agent_name]
                _run_agent_safe(agent, passage, context, local_metrics)

        result.passages_evaluated += 1

    return result


def print_comparison_report(result: ComparisonResult) -> str:
    """Generate a human-readable comparison report."""
    lines = [
        "",
        "=" * 72,
        "MODEL COMPARISON REPORT",
        f"Passages evaluated: {result.passages_evaluated}",
        "=" * 72,
    ]

    for label, m in [
        ("ANTHROPIC", result.anthropic_metrics),
        ("LOCAL LLM", result.local_metrics),
    ]:
        lines.append(f"\n--- {label}: {m.model_label} ---")
        lines.append(f"  Total LLM calls:           {m.total_calls}")
        lines.append(f"  JSON validity rate:         {m.json_validity_rate:.1%}")
        lines.append(f"  Total extractions:          {m.total_extractions}")
        lines.append(f"  Evidence verification rate:  {m.evidence_verification_rate:.1%}")
        lines.append(f"  Evidence spans (verified/total): "
                     f"{m.evidence_spans_verified}/{m.evidence_spans_total}")
        lines.append(f"  Agent errors:               {m.agent_errors}")
        lines.append(f"  Avg latency:                {m.avg_latency_ms:.0f}ms")

        tier_str = ", ".join(f"{k}={v}" for k, v in sorted(m.confidence_tiers.items()))
        lines.append(f"  Confidence tiers:           {tier_str}")

    # --- Delta summary ---
    am = result.anthropic_metrics
    lm = result.local_metrics

    lines.append("\n--- DELTA (Local - Anthropic) ---")

    if am.total_calls > 0 and lm.total_calls > 0:
        ev_delta = lm.evidence_verification_rate - am.evidence_verification_rate
        json_delta = lm.json_validity_rate - am.json_validity_rate
        lines.append(f"  Evidence verification rate:  {ev_delta:+.1%}")
        lines.append(f"  JSON validity rate:          {json_delta:+.1%}")
        lines.append(f"  Avg latency:                 "
                     f"{lm.avg_latency_ms - am.avg_latency_ms:+.0f}ms")
    else:
        lines.append("  (insufficient data for delta — need both providers configured)")

    if result.errors:
        lines.append("\n--- ERRORS ---")
        for err in result.errors:
            lines.append(f"  {err}")

    report = "\n".join(lines)
    print(report)
    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for model comparison."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare Claude Haiku vs local LLMs on gold-standard passages"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max passages to evaluate (default: all)"
    )
    parser.add_argument(
        "--gold-dir", type=str, default=None,
        help="Path to gold-standard fixtures directory"
    )
    args = parser.parse_args()

    result = run_comparison(
        gold_standard_dir=args.gold_dir,
        limit=args.limit,
    )
    print_comparison_report(result)


if __name__ == "__main__":
    main()
