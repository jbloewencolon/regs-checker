"""Base extraction agent with shared logic.

Implements the simplified extraction pipeline:
  - Single LLM call per passage (Rec #2: no separate detection pass)
  - Rule-based validation instead of self-check LLM call (Rec #3)
  - Evidence span verification via string matching (Rec #3)
  - Pydantic v2 strict mode validation
  - Retry on validation failure (not on LLM opinion)
"""

from __future__ import annotations

import json
import hashlib
from abc import ABC, abstractmethod
from typing import Any

import anthropic
import structlog
from pydantic import BaseModel, ValidationError

from src.core.config import settings
from src.schemas.extraction import AbstentionResult, EvidenceSpan

logger = structlog.get_logger()


class BaseExtractionAgent(ABC):
    """Base class for all extraction agents."""

    agent_name: str = "base"
    max_retries: int = 1

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this agent."""

    @abstractmethod
    def get_extraction_prompt(self, passage: str, context: dict | None = None) -> str:
        """Build the extraction prompt for a given passage."""

    @abstractmethod
    def get_output_schema(self) -> type[BaseModel]:
        """Return the Pydantic model for validating extraction output."""

    def extract(
        self, passage: str, context: dict | None = None
    ) -> dict[str, Any] | AbstentionResult:
        """Run extraction on a single passage.

        This is a single LLM call that handles detection + extraction in one pass.
        If the model determines nothing is extractable, it returns an AbstentionResult.
        Validation is done via Pydantic + evidence span string matching (no self-check LLM call).
        """
        prompt = self.get_extraction_prompt(passage, context)
        attempt = 0

        while attempt <= self.max_retries:
            try:
                raw_output = self._call_llm(prompt, attempt)
                parsed = json.loads(raw_output)

                # Check for abstention
                if parsed.get("detected") is False:
                    return AbstentionResult(**parsed)

                # Validate against Pydantic schema (strict mode)
                schema = self.get_output_schema()
                validated = schema.model_validate(parsed)

                # Evidence span verification via string matching (Rec #3)
                evidence_spans = parsed.get("evidence_spans", [])
                verified_spans = self._verify_evidence_spans(evidence_spans, passage)

                result = validated.model_dump(by_alias=True)
                result["evidence_spans"] = verified_spans
                result["_prompt_hash"] = self._prompt_hash(prompt)
                result["_model_id"] = settings.extraction_model

                return result

            except (json.JSONDecodeError, ValidationError) as e:
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

    def _call_llm(self, prompt: str, attempt: int) -> str:
        """Make a single LLM API call."""
        system_prompt = self.get_system_prompt()
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

        return response.content[0].text

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
