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
"""

from __future__ import annotations

import json
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import anthropic
import structlog
from pydantic import BaseModel, ValidationError

from src.agents.prompt_loader import load_prompt_template, render_prompt
from src.core.config import settings
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


class BaseExtractionAgent(ABC):
    """Base class for all extraction agents."""

    agent_name: str = "base"
    max_retries: int = 1

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
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
                raw_output, usage = self._call_llm(prompt, attempt)
                logger.debug(
                    "extraction_pre_parse",
                    agent=self.agent_name,
                    attempt=attempt,
                    raw_output_preview=raw_output[:300],
                )
                parsed = json.loads(raw_output)

                # Check for abstention
                if parsed.get("detected") is False:
                    return ExtractionResult(
                        extractions=[],
                        abstention=AbstentionResult(**parsed),
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        prompt_hash=prompt_hash,
                        model_id=settings.extraction_model,
                        template_version=template_version,
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
                    result["_model_id"] = settings.extraction_model
                    result["_template_version"] = template_version
                    validated_extractions.append(result)

                return ExtractionResult(
                    extractions=validated_extractions,
                    abstention=None,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    prompt_hash=prompt_hash,
                    model_id=settings.extraction_model,
                    template_version=template_version,
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

    def _call_llm(self, prompt: str, attempt: int) -> tuple[str, Any]:
        """Make a single LLM API call. Returns (text, usage)."""
        system_prompt = self._resolve_system_prompt()
        if attempt > 0:
            system_prompt += (
                "\n\nPREVIOUS ATTEMPT FAILED VALIDATION. "
                "Ensure your output is valid JSON matching the required schema exactly. "
                "Double-check all evidence spans are verbatim quotes from the passage."
            )

        response = self.client.messages.create(
            model=settings.extraction_model,
            max_tokens=settings.extraction_max_tokens,
            temperature=settings.extraction_temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract text from the first text block, skipping non-text blocks
        raw_text = ""
        for block in response.content:
            if block.type == "text":
                raw_text = block.text
                break

        logger.debug(
            "llm_raw_response",
            agent=self.agent_name,
            attempt=attempt,
            response_length=len(raw_text),
            stop_reason=response.stop_reason,
            content_types=[b.type for b in response.content],
            raw_text_preview=raw_text[:500] if raw_text else "<empty>",
        )

        if not raw_text.strip():
            raise ValueError(
                f"Empty text response from model "
                f"(stop_reason={response.stop_reason}, "
                f"content_types={[b.type for b in response.content]})"
            )

        return raw_text, response.usage

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
