"""Base extraction agent with shared logic.

Implements the simplified extraction pipeline:
  - Single LLM call per passage (Rec #2: no separate detection pass)
  - Rule-based validation instead of self-check LLM call (Rec #3)
  - Evidence span verification via string matching (Rec #3)
  - Pydantic v2 strict mode validation
  - Retry on validation failure (not on LLM opinion)
  - Multi-extraction support (multiple items per passage)
  - Token usage tracking per call
  - Versioned prompt templates via YAML + Jinja2
  - Per-agent model override for local LLM routing
"""

from __future__ import annotations

import json
import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError

from src.agents.prompt_loader import load_prompt_template, render_prompt
from src.core.config import settings
from src.core.llm_provider import get_extraction_provider
from src.schemas.extraction import AbstentionResult, EvidenceSpan

logger = structlog.get_logger()


@dataclass
class ExtractionResult:
    """Result of a single agent extraction call, including metadata."""

    extractions: list[dict[str, Any]]
    abstention: AbstentionResult | None
    input_tokens: int
    output_tokens: int
    prompt_hash: str
    model_id: str
    template_version: str | None
    truncated: bool = False  # True when finish_reason=length (output cut off)


class BaseExtractionAgent(ABC):
    """Base class for all extraction agents."""

    agent_name: str = "base"
    max_retries: int = 1
    model_override: str | None = None
    reasoning_effort: str | None = None

    def __init__(self) -> None:
        self._provider = get_extraction_provider()
        self._template = load_prompt_template(self.agent_name)

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this agent (inline fallback)."""

    @abstractmethod
    def get_extraction_prompt(self, passage: str, context: dict | None = None) -> str:
        """Build the extraction prompt for a given passage (inline fallback)."""

    @abstractmethod
    def get_output_schema(self) -> type[BaseModel]:
        """Return the Pydantic model for validating extraction output."""

    def _resolve_system_prompt(self) -> str:
        """Resolve system prompt from template or inline fallback."""
        if self._template and "system_prompt" in self._template:
            return self._template["system_prompt"].strip()
        return self.get_system_prompt()

    def _resolve_extraction_prompt(self, passage: str, context: dict | None = None) -> str:
        """Resolve extraction prompt from template or inline fallback."""
        if self._template and "extraction_prompt" in self._template:
            render_ctx = {"passage": passage}
            if context:
                render_ctx.update(context)
            return render_prompt(self._template["extraction_prompt"], render_ctx)
        return self.get_extraction_prompt(passage, context)

    def extract(
        self, passage: str, context: dict | None = None
    ) -> ExtractionResult:
        """Run extraction on a single passage.

        Returns an ExtractionResult containing either a list of validated
        extractions or an abstention. Supports multi-extraction (multiple
        items from a single passage).
        """
        prompt = self._resolve_extraction_prompt(passage, context)
        prompt_hash = self._prompt_hash(prompt)
        template_version = self._template.get("version") if self._template else None
        attempt = 0

        while attempt <= self.max_retries:
            try:
                raw_output, usage, response_model_id, stop_reason = self._call_llm(prompt, attempt)
                logger.debug(
                    "extraction_pre_parse",
                    agent=self.agent_name,
                    attempt=attempt,
                    raw_output_preview=raw_output[:300],
                )
                cleaned = self._strip_code_fences(raw_output)
                cleaned = self._strip_think_blocks(cleaned)
                parsed = json.loads(cleaned)

                was_truncated = stop_reason == "length"

                # Check for abstention
                if parsed.get("detected") is False:
                    return ExtractionResult(
                        extractions=[],
                        abstention=AbstentionResult(**parsed),
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        prompt_hash=prompt_hash,
                        model_id=response_model_id,
                        template_version=template_version,
                        truncated=was_truncated,
                    )

                # Handle multi-extraction: look for "extractions" array
                items = parsed.get("extractions", [parsed])
                if not isinstance(items, list):
                    items = [items]

                validated_extractions = []
                schema = self.get_output_schema()

                for item in items:
                    validated = schema.model_validate(item)
                    evidence_spans = item.get("evidence_spans", [])
                    verified_spans = self._verify_evidence_spans(evidence_spans, passage)

                    result = validated.model_dump(by_alias=True)
                    result["evidence_spans"] = verified_spans
                    result["_prompt_hash"] = prompt_hash
                    result["_model_id"] = response_model_id
                    result["_template_version"] = template_version
                    validated_extractions.append(result)

                if was_truncated:
                    logger.warning(
                        "extraction_truncated",
                        agent=self.agent_name,
                        model_id=response_model_id,
                        output_tokens=usage.output_tokens,
                        extractions_count=len(validated_extractions),
                    )

                return ExtractionResult(
                    extractions=validated_extractions,
                    abstention=None,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    prompt_hash=prompt_hash,
                    model_id=response_model_id,
                    template_version=template_version,
                    truncated=was_truncated,
                )

            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                logger.warning(
                    "extraction_validation_failed",
                    agent=self.agent_name,
                    attempt=attempt,
                    error=str(e),
                )
                attempt += 1
                if attempt > self.max_retries:
                    raise

        raise RuntimeError("Extraction failed after retries")

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Remove markdown code fences (```json ... ```) wrapping JSON output."""
        text = text.strip()
        if text.startswith("```"):
            # Strip opening ```json or ``` line
            text = "\n".join(text.split("\n")[1:])
            # Strip closing ```
            text = text.rsplit("```", 1)[0].strip()
        return text

    @staticmethod
    def _strip_think_blocks(text: str) -> str:
        """Remove <think>...</think> blocks from model output.

        DeepSeek-R1 and similar reasoning models emit chain-of-thought
        wrapped in <think> tags before the actual JSON response. These
        blocks must be stripped before JSON parsing.
        """
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    def _call_llm(self, prompt: str, attempt: int) -> tuple[str, Any, str]:
        """Make a single LLM API call via the provider abstraction.

        Returns (text, usage, model_id) where usage is an LLMUsage dataclass
        and model_id is the actual model that served the request.

        If a model_override is set and the call fails (e.g. 400 from LM Studio),
        retries once with the provider's default model for resilience.
        """
        system_prompt = self._resolve_system_prompt()
        system_prompt += (
            "\n\nReturn only raw JSON with no markdown formatting, "
            "no code fences, and no preamble."
        )

        # Reasoning models (DeepSeek-R1, Qwen3) can spend thousands of
        # tokens on chain-of-thought before producing JSON.  Ask them to
        # keep their internal reasoning brief so output budget remains
        # available for the actual structured answer.
        effective_model = self.model_override or ""
        is_reasoning = any(
            tag in effective_model.lower()
            for tag in ("deepseek-r1", "qwen3")
        )
        if is_reasoning:
            system_prompt += (
                "\n\nIMPORTANT: Keep your internal reasoning brief and focused. "
                "Do NOT exhaustively analyze every possible interpretation. "
                "Identify the key findings quickly, then produce the JSON output."
            )
        if attempt > 0:
            system_prompt += (
                "\n\nPREVIOUS ATTEMPT FAILED VALIDATION. "
                "Ensure your output is valid JSON matching the required schema exactly. "
                "Double-check all evidence spans are verbatim quotes from the passage."
            )

        # Use lower max_tokens for local models to fit within context window
        max_tokens = settings.extraction_max_tokens
        if settings.extraction_provider == "local":
            max_tokens = min(max_tokens, settings.local_extraction_max_tokens)

        call_kwargs = dict(
            system_prompt=system_prompt,
            user_prompt=prompt,
            max_tokens=max_tokens,
            temperature=settings.extraction_temperature,
            model_override=self.model_override,
            reasoning_effort=self.reasoning_effort,
        )

        try:
            response = self._provider.call(**call_kwargs)
        except Exception as exc:
            if self.model_override is not None:
                fallback_model = self._provider.model_id
                logger.warning(
                    "extraction_model_fallback",
                    agent=self.agent_name,
                    failed_model=self.model_override,
                    fallback_model=fallback_model,
                    error=str(exc)[:300],
                )
                call_kwargs["model_override"] = None
                call_kwargs["reasoning_effort"] = None
                try:
                    response = self._provider.call(**call_kwargs)
                except Exception as fallback_exc:
                    logger.error(
                        "extraction_fallback_also_failed",
                        agent=self.agent_name,
                        failed_model=self.model_override,
                        fallback_model=fallback_model,
                        original_error=str(exc)[:200],
                        fallback_error=str(fallback_exc)[:200],
                    )
                    # Raise the original error — more informative
                    raise exc from fallback_exc
            else:
                raise

        logger.debug(
            "llm_raw_response",
            agent=self.agent_name,
            attempt=attempt,
            response_length=len(response.text),
            stop_reason=response.stop_reason,
            model_id=response.model_id,
            raw_text_preview=response.text[:500] if response.text else "<empty>",
        )

        return response.text, response.usage, response.model_id, response.stop_reason

    def _verify_evidence_spans(
        self, spans: list[dict], passage: str
    ) -> list[dict]:
        """Verify evidence spans via string matching (Rec #3).

        Confirms each evidence span text appears verbatim in the passage.
        This replaces the self-check LLM call and is more reliable for
        detecting hallucinated quotes.
        """
        verified = []
        for span_data in spans:
            span = EvidenceSpan(**span_data)
            if span.text in passage:
                # Update char offsets to actual positions
                start = passage.index(span.text)
                verified.append(
                    {
                        "field_name": span.field_name,
                        "text": span.text,
                        "char_start": start,
                        "char_end": start + len(span.text),
                        "verified": True,
                    }
                )
            else:
                logger.warning(
                    "evidence_span_not_found",
                    agent=self.agent_name,
                    field=span.field_name,
                    span_text=span.text[:80],
                )
                verified.append(
                    {
                        "field_name": span.field_name,
                        "text": span.text,
                        "verified": False,
                    }
                )
        return verified

    def _prompt_hash(self, prompt: str) -> str:
        """Hash the prompt for reproducibility tracking."""
        return hashlib.sha256(prompt.encode()).hexdigest()[:12]
