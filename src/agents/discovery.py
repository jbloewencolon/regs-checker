"""Discovery agent — uses local LLM for bill classification and metadata extraction.

This agent runs on a local Llama 3.1 8B model (Q4/Q5 quantized) via an
OpenAI-compatible API server (LM Studio, llama.cpp, vLLM). It handles two tasks:

1. Bill Classification: Given raw text from a scraped web page or document,
   determine if it is AI-related legislation worth ingesting.

2. Metadata Extraction: Extract structured metadata (title, jurisdiction,
   bill number, effective date, status) from classified bills.

These are "discovery" tasks that don't require the legal precision of
extraction agents (which use Anthropic Haiku). The local 8B model provides
sufficient quality for classification and basic metadata at zero API cost.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

from src.core.llm_provider import get_discovery_provider, LocalLLMProvider, LLMResponse

logger = structlog.get_logger()


CLASSIFICATION_SYSTEM_PROMPT = """\
You are a legislative bill classifier. Your job is to determine whether a
piece of text is AI-related legislation (a bill, law, regulation, or executive
order that regulates artificial intelligence, automated decision-making,
algorithmic systems, or machine learning).

Respond with a JSON object containing:
- "is_ai_legislation": true/false
- "confidence": float 0.0-1.0
- "reasoning": brief explanation (1-2 sentences)
- "ai_topics": list of AI topics found (e.g. ["facial recognition", "automated hiring"])

If the text is not legislation at all (news article, blog post, etc.), set
is_ai_legislation to false with high confidence.

Return only raw JSON, no markdown formatting or code fences."""

METADATA_SYSTEM_PROMPT = """\
You are a legislative metadata extractor. Given the text of an AI-related bill
or law, extract structured metadata.

Respond with a JSON object containing:
- "title": official title or short description of the bill/law
- "jurisdiction_code": two-letter US state code (e.g. "CA", "NY") or "US" for federal
- "bill_number": bill/law identifier (e.g. "SB 1047", "HB 2094")
- "effective_date": effective date if mentioned (ISO format YYYY-MM-DD or null)
- "status": one of "introduced", "passed_committee", "passed_chamber", "enacted", "signed", "vetoed", "unknown"
- "ai_scope": brief description of what AI activities are covered (1-2 sentences)
- "key_requirements": list of key requirements or obligations (max 5 items, brief)

Use null for any field you cannot determine from the text.
Return only raw JSON, no markdown formatting or code fences."""


@dataclass
class ClassificationResult:
    """Result of bill classification."""

    is_ai_legislation: bool
    confidence: float
    reasoning: str
    ai_topics: list[str]
    input_tokens: int
    output_tokens: int
    model_id: str


@dataclass
class MetadataResult:
    """Result of metadata extraction from a bill."""

    title: str | None
    jurisdiction_code: str | None
    bill_number: str | None
    effective_date: str | None
    status: str | None
    ai_scope: str | None
    key_requirements: list[str]
    input_tokens: int
    output_tokens: int
    model_id: str


class DiscoveryAgent:
    """Agent for bill discovery tasks using local LLM.

    Uses the discovery provider (default: local) for classification and
    metadata extraction. For large documents, automatically routes to a
    large-context model if available.
    """

    # Model to use for large documents (131k context window).
    # Set to None to always use the default discovery model.
    large_context_model: str | None = "openai/gpt-oss-20b"

    # Text length threshold (chars) above which the large-context model is used.
    large_context_threshold: int = 8000

    def __init__(self) -> None:
        self._provider = get_discovery_provider()
        logger.info(
            "discovery_agent_init",
            provider_model=self._provider.model_id,
        )

    def _model_for_text(self, text: str) -> str | None:
        """Return model_override for large texts, or None for default."""
        if (
            self.large_context_model
            and len(text) > self.large_context_threshold
            and isinstance(self._provider, LocalLLMProvider)
        ):
            logger.info(
                "discovery_using_large_context_model",
                text_length=len(text),
                model=self.large_context_model,
            )
            return self.large_context_model
        return None

    def _call_with_fallback(
        self,
        system_prompt: str,
        user_prompt_template: str,
        text: str,
        max_chars: int,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Call LLM with automatic fallback from large-context model to default.

        If the large-context model is selected but fails (e.g. 400 from LM Studio),
        retries with the default model, truncating text to fit its context window.
        """
        truncated = text[:max_chars] if len(text) > max_chars else text
        model_override = self._model_for_text(truncated)
        user_prompt = user_prompt_template.format(text=truncated)

        if model_override is not None:
            try:
                return self._provider.call(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                    temperature=0.0,
                    model_override=model_override,
                )
            except Exception as exc:
                # Truncate to default model's context and retry without override
                default_max = self.large_context_threshold
                fallback_text = text[:default_max] if len(text) > default_max else text
                user_prompt = user_prompt_template.format(text=fallback_text)
                logger.warning(
                    "large_context_model_fallback",
                    error=str(exc)[:200],
                    original_len=len(truncated),
                    fallback_len=len(fallback_text),
                )
                return self._provider.call(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                    temperature=0.0,
                )

        return self._provider.call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=0.0,
        )

    def classify_bill(self, text: str, max_chars: int = 32000) -> ClassificationResult:
        """Classify whether text contains AI-related legislation.

        Args:
            text: Raw text from a scraped page or document.
            max_chars: Truncate text to this length to stay within model context.

        Returns:
            ClassificationResult with is_ai_legislation flag and confidence.
        """
        response = self._call_with_fallback(
            system_prompt=CLASSIFICATION_SYSTEM_PROMPT,
            user_prompt_template="Classify the following text:\n\n{text}",
            text=text,
            max_chars=max_chars,
        )

        parsed = self._parse_json(response.text)

        return ClassificationResult(
            is_ai_legislation=parsed.get("is_ai_legislation", False),
            confidence=float(parsed.get("confidence", 0.0)),
            reasoning=parsed.get("reasoning", ""),
            ai_topics=parsed.get("ai_topics", []),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model_id=response.model_id,
        )

    def extract_metadata(self, text: str, max_chars: int = 32000) -> MetadataResult:
        """Extract structured metadata from bill text.

        Args:
            text: Text of an AI-related bill/law.
            max_chars: Truncate text to this length for model context.

        Returns:
            MetadataResult with extracted bill metadata.
        """
        response = self._call_with_fallback(
            system_prompt=METADATA_SYSTEM_PROMPT,
            user_prompt_template="Extract metadata from this legislation:\n\n{text}",
            text=text,
            max_chars=max_chars,
        )

        parsed = self._parse_json(response.text)

        return MetadataResult(
            title=parsed.get("title"),
            jurisdiction_code=parsed.get("jurisdiction_code"),
            bill_number=parsed.get("bill_number"),
            effective_date=parsed.get("effective_date"),
            status=parsed.get("status"),
            ai_scope=parsed.get("ai_scope"),
            key_requirements=parsed.get("key_requirements", []),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model_id=response.model_id,
        )

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Parse JSON from LLM response, handling code fences and think blocks."""
        text = text.strip()
        # Strip <think>...</think> blocks from reasoning models (Qwen, DeepSeek)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:])
            text = text.rsplit("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(
                "discovery_json_parse_failed",
                text_preview=text[:200],
            )
            return {}
