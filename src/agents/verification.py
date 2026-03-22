"""Verification Agent — lightweight URL correction via search results.

When the Discovery Agent classifies fetched content as non-AI-legislation
(meaning the URL was stale or incorrect), this agent examines web search
results to identify the correct official URL for the bill.

Uses the discovery provider (local 3B model) to keep overhead low — this
is a simple ranking/selection task, not a precision extraction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog

from src.core.llm_provider import get_discovery_provider

logger = structlog.get_logger()

VERIFICATION_SYSTEM_PROMPT = """\
You are a legal metadata auditor. A previous URL for a legislative bill was \
incorrect or stale — the fetched content did not contain the expected bill text.

Given the bill metadata and a set of web search results, identify the most \
likely correct URL for the official PDF or HTML text of the bill.

Respond with a JSON object containing:
- "suggested_url": the best URL from the search results (or null if none are suitable)
- "confidence": float 0.0-1.0
- "reasoning": brief explanation of your choice (1-2 sentences)

Prefer URLs from .gov domains or official state legislature websites.
Return only raw JSON, no markdown formatting or code fences."""


@dataclass
class VerificationResult:
    """Result of URL verification via search."""

    suggested_url: str | None
    confidence: float
    reasoning: str
    input_tokens: int
    output_tokens: int
    model_id: str


class VerificationAgent:
    """Agent for verifying/correcting bill URLs using search results.

    Uses the discovery provider (local 3B model) since this is a simple
    selection task that doesn't need the precision of extraction models.
    """

    def __init__(self) -> None:
        self._provider = get_discovery_provider()

    def verify_url(
        self,
        bill_metadata: dict[str, Any],
        search_results: list[dict[str, str]],
    ) -> VerificationResult:
        """Given bill metadata and search results, identify the correct URL.

        Args:
            bill_metadata: Dict with keys like "title", "jurisdiction",
                          "bill_number" describing the expected bill.
            search_results: List of dicts with "title", "url", "snippet" keys.

        Returns:
            VerificationResult with suggested URL and confidence.
        """
        # Build user prompt
        meta_lines = []
        for key, val in bill_metadata.items():
            if val:
                meta_lines.append(f"  {key}: {val}")
        meta_str = "\n".join(meta_lines)

        results_lines = []
        for i, r in enumerate(search_results, 1):
            results_lines.append(
                f"  [{i}] {r.get('title', 'No title')}\n"
                f"      URL: {r.get('url', '')}\n"
                f"      Snippet: {r.get('snippet', '')}"
            )
        results_str = "\n".join(results_lines)

        user_prompt = (
            f"The following bill URL was incorrect. Please identify the "
            f"correct URL from the search results below.\n\n"
            f"BILL METADATA:\n{meta_str}\n\n"
            f"SEARCH RESULTS:\n{results_str}"
        )

        try:
            response = self._provider.call(
                system_prompt=VERIFICATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=512,
                temperature=0.0,
            )
        except Exception as exc:
            logger.warning(
                "verification_llm_fallback",
                error=str(exc)[:200],
            )
            # Retry without model specifics — let provider use defaults
            response = self._provider.call(
                system_prompt=VERIFICATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=1024,
                temperature=0.0,
            )

        parsed = self._parse_json(response.text)

        return VerificationResult(
            suggested_url=parsed.get("suggested_url"),
            confidence=float(parsed.get("confidence", 0.0)),
            reasoning=parsed.get("reasoning", ""),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model_id=response.model_id,
        )

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Parse JSON from LLM response, handling code fences."""
        text = text.strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:])
            text = text.rsplit("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(
                "verification_json_parse_failed",
                text_preview=text[:200],
            )
            return {}
