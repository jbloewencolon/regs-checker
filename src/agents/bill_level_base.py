"""Base class for bill-level extraction agents.

Bill-level agents run once per law (DocumentVersion) rather than once per
passage.  They receive the full concatenated bill text and produce a single
structured record per law, stored in bill_level_extractions.

This solves the cross-section context problem: per-passage agents can't see
penalty amounts defined in a different section.  Bill-level agents see the
entire bill and produce one authoritative record per law.

Usage:
    class EnforcementAgent(BillLevelAgent):
        agent_name = "enforcement_agent"

        def get_prompt(self, full_text: str, context: dict) -> str:
            ...

        def parse_response(self, raw: str) -> dict:
            ...

    result = agent.extract_bill(full_text, context={})
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import structlog

from src.core.config import settings
from src.core.llm_provider import get_extraction_provider
from src.core.model_config import get_config

logger = structlog.get_logger()

# Max characters of bill text to send — keeps prompts within context window.
# At ~4 chars/token this is ~32k tokens of input budget.
MAX_BILL_TEXT_CHARS = 128_000


@dataclass
class BillLevelResult:
    """Result from a single bill-level agent run.

    ``truncated`` reflects OUTPUT truncation only (the model hit
    finish_reason=length). ``input_truncated``/``chars_dropped`` (EA0-4)
    reflect the separate, previously-silent failure mode where the bill text
    itself exceeded MAX_BILL_TEXT_CHARS and was cut before the prompt was
    ever built — enforcement sections conventionally sit at the end of state
    bills, so this truncation biases against exactly the fields
    (enforcing_body, max_civil_penalty_usd, etc.) an auditor most needs.
    """

    payload: dict[str, Any]
    model_id: str
    input_tokens: int
    output_tokens: int
    raw_output: str
    truncated: bool = False
    input_truncated: bool = False
    chars_dropped: int = 0


class BillLevelAgent(ABC):
    """Base for agents that run once per law and produce one record."""

    agent_name: str = "bill_level"
    max_retries: int = 1
    model_override: str | None = None
    max_tokens_override: int | None = None
    temperature_override: float | None = None
    reasoning_effort: str | None = None

    def __init__(self) -> None:
        self._provider = get_extraction_provider()
        # Only apply model config overrides when this agent is explicitly
        # present in agent_models.json.  The fallback from get() uses generic
        # extraction defaults (65536 tokens) that override the class-level
        # max_tokens_override set for each bill-level agent.
        cfg_store = get_config()
        if self.agent_name in cfg_store.agents:
            cfg = cfg_store.get(self.agent_name)
            if cfg.model:
                self.model_override = cfg.model
            if cfg.max_tokens:
                self.max_tokens_override = cfg.max_tokens
            if cfg.temperature is not None:
                self.temperature_override = cfg.temperature
            if cfg.reasoning_effort is not None:
                self.reasoning_effort = cfg.reasoning_effort

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def get_prompt(self, full_text: str, context: dict) -> str:
        """Build the prompt to send to the LLM.

        Args:
            full_text: Full concatenated bill text (truncated to budget).
            context: Bill-level context dict (definitions, scope, etc.).
        """

    @abstractmethod
    def parse_response(self, raw: str) -> dict:
        """Parse LLM output into a structured payload dict.

        Should raise ValueError on unparseable output.
        """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_bill(
        self,
        full_text: str,
        context: dict | None = None,
    ) -> BillLevelResult:
        """Run the agent on the full bill text.

        Retries up to max_retries on parse failure.  Returns a BillLevelResult
        with payload={} and truncated=True on unrecoverable failure.
        """
        chars_dropped = max(0, len(full_text) - MAX_BILL_TEXT_CHARS)
        input_truncated = chars_dropped > 0
        text = full_text[:MAX_BILL_TEXT_CHARS]
        prompt = self.get_prompt(text, context or {})
        last_error: Exception | None = None

        if input_truncated:
            logger.warning(
                "bill_level_input_truncated",
                agent=self.agent_name,
                full_text_chars=len(full_text),
                budget_chars=MAX_BILL_TEXT_CHARS,
                chars_dropped=chars_dropped,
            )

        for attempt in range(self.max_retries + 1):
            try:
                raw, input_tokens, output_tokens, model_id, truncated = self._call_llm(
                    prompt, attempt
                )
                payload = self.parse_response(raw)
                if input_truncated:
                    # Surface in the JSONB payload (no migration needed) so
                    # the dashboard/review UI and downstream sync can see it
                    # without a schema change. Never silently overwrite a
                    # field the model itself produced.
                    payload.setdefault("_input_truncated", True)
                    payload.setdefault("_chars_dropped", chars_dropped)
                logger.info(
                    "bill_level_extraction_complete",
                    agent=self.agent_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    truncated=truncated,
                    input_truncated=input_truncated,
                    payload_keys=list(payload.keys()),
                )
                return BillLevelResult(
                    payload=payload,
                    model_id=model_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    raw_output=raw,
                    truncated=truncated,
                    input_truncated=input_truncated,
                    chars_dropped=chars_dropped,
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    "bill_level_extraction_retry",
                    agent=self.agent_name,
                    attempt=attempt,
                    error=str(e)[:300],
                )

        logger.error(
            "bill_level_extraction_failed",
            agent=self.agent_name,
            error=str(last_error)[:300],
        )
        failure_payload: dict[str, Any] = {"_error": str(last_error)}
        if input_truncated:
            failure_payload["_input_truncated"] = True
            failure_payload["_chars_dropped"] = chars_dropped
        return BillLevelResult(
            payload=failure_payload,
            model_id="",
            input_tokens=0,
            output_tokens=0,
            raw_output="",
            truncated=False,
            input_truncated=input_truncated,
            chars_dropped=chars_dropped,
        )

    # ------------------------------------------------------------------
    # LLM plumbing
    # ------------------------------------------------------------------

    def _call_llm(
        self, prompt: str, attempt: int
    ) -> tuple[str, int, int, str, bool]:
        """Call the LLM and return (raw_text, input_tokens, output_tokens, model_id, truncated)."""
        max_tokens = self.max_tokens_override or settings.extraction_max_tokens
        temperature = (
            self.temperature_override
            if self.temperature_override is not None
            else settings.extraction_temperature
        )
        response = self._provider.call(
            system_prompt="You are a legal analyst. Output only valid JSON as instructed.",
            user_prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            model_override=self.model_override,
            reasoning_effort=self.reasoning_effort,
        )
        truncated = response.stop_reason == "length"
        return response.text, response.usage.input_tokens, response.usage.output_tokens, response.model_id, truncated

    # ------------------------------------------------------------------
    # JSON repair helpers (same logic as BaseExtractionAgent)
    # ------------------------------------------------------------------

    @staticmethod
    def _repair_json(text: str) -> str:
        """Strip control chars and extract valid JSON from LLM output."""
        text = text.strip()
        if not text:
            return text

        # Strip invalid control characters (e.g. emitted by Gemma)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)

        # Strip markdown code fences
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
        text = text.strip()

        # Remove trailing commas before closing brackets
        text = re.sub(r',\s*([}\]])', r'\1', text)

        return text

    def _parse_json_payload(self, raw: str) -> dict:
        """Parse JSON from LLM output, attempting repairs on failure."""
        cleaned = self._repair_json(raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Try extracting first JSON object
            match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise ValueError(f"Could not parse JSON from response: {cleaned[:200]}")
