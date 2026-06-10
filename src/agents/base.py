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


def _repair_truncated_json(text: str) -> str:
    """Attempt to salvage truncated JSON by closing open brackets.

    When the LLM hits max_tokens, output is cut mid-JSON like:
        {"extractions":[{"a":1,...},{"b":2,"nested":{...
    This function:
      1. Strips back to the last complete array element (last "},")
      2. Closes any remaining open brackets/braces

    Returns the repaired text, or the original if repair isn't possible.
    """
    text = text.rstrip()
    if not text or text[0] not in "{[":
        return text

    # Strategy 1: Find the last complete object in an "extractions" array.
    # Look for the pattern "},{ which indicates an array element boundary.
    # Truncate to just after the last complete "}" before an incomplete element.
    last_complete = -1
    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 1 and ch == "}":
                # This closes an object at depth 1 (array element level).
                # Check if this is followed by a comma (next element).
                rest = text[i + 1:].lstrip()
                if rest.startswith(","):
                    last_complete = i

    if last_complete > 0:
        # Truncate to just after the last complete array element
        truncated = text[:last_complete + 1]
        # Close the open containers
        # Count what's still open
        depth = 0
        in_string = False
        escape_next = False
        for ch in truncated:
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1

        # Close remaining open brackets (reverse order: ] then })
        # We need to figure out what type of brackets are open.
        # Recount with a stack.
        stack = []
        in_string = False
        escape_next = False
        for ch in truncated:
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                stack.append("}")
            elif ch == "[":
                stack.append("]")
            elif ch in "}]" and stack:
                stack.pop()

        # Close in reverse order
        closing = "".join(reversed(stack))
        return truncated + closing

    # Strategy 2: Close unterminated strings and open brackets.
    # When the model truncates mid-JSON-string (e.g. "value that was cu),
    # we must close the string before closing brackets.
    stack = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack:
            stack.pop()

    suffix = ""
    if in_string:
        suffix = '"'
    if stack or in_string:
        return text + suffix + "".join(reversed(stack))

    return text


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
    model_reasoning: str | None = None  # Chain-of-thought from <think> blocks


class BaseExtractionAgent(ABC):
    """Base class for all extraction agents."""

    agent_name: str = "base"
    max_retries: int = 2
    model_override: str | None = None
    reasoning_effort: str | None = None
    max_tokens_override: int | None = None
    temperature_override: float | None = None

    def __init__(self) -> None:
        self._provider = get_extraction_provider()
        self._template = load_prompt_template(self.agent_name)
        # Apply per-agent config overrides from agent_models.json
        from src.core.model_config import get_config
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
        """Resolve extraction prompt from template or inline fallback.

        When a YAML extraction_prompt exists it takes precedence over the
        inline get_extraction_prompt() method.  Bill-level context blocks
        (definitions, scope, enforcement) are too large for Jinja2 variable
        substitution, so _append_bill_context() is called after rendering
        regardless of which path produced the base prompt.
        """
        if self._template and "extraction_prompt" in self._template:
            render_ctx = {"passage": passage}
            if context:
                render_ctx.update(context)
            prompt = render_prompt(self._template["extraction_prompt"], render_ctx)
            # Append bill-level context blocks — these are NOT in the Jinja2
            # templates (too large), so must be appended after rendering.
            return self._append_bill_context(prompt, context)
        return self.get_extraction_prompt(passage, context)

    @staticmethod
    def _append_bill_context(prompt: str, context: dict | None) -> str:
        """Append bill-level context (definitions, scope, defined terms) to a prompt.

        Called by each agent's get_extraction_prompt() after building its
        agent-specific context block.  Adds the bill's definitions and scope
        sections so the model can resolve cross-references and understand
        actor terminology used elsewhere in the bill.
        """
        if not context:
            return prompt

        parts: list[str] = []

        defined_terms = context.get("defined_terms")
        if defined_terms:
            parts.append(
                f"DEFINED TERMS IN THIS BILL: {', '.join(defined_terms)}"
            )

        bill_defs = context.get("bill_definitions")
        if bill_defs:
            parts.append(
                "BILL DEFINITIONS (verbatim from the bill's definitions section — "
                "use to resolve terms referenced in the passage above):\n"
                f"{bill_defs}"
            )

        bill_scope = context.get("bill_scope")
        if bill_scope:
            parts.append(
                "BILL SCOPE & APPLICABILITY (verbatim from the bill — "
                "use to understand what entities and systems this bill covers):\n"
                f"{bill_scope}"
            )

        bill_enforcement = context.get("bill_enforcement")
        if bill_enforcement:
            parts.append(
                "BILL ENFORCEMENT & PENALTIES (verbatim from the bill — "
                "use to populate enforcement fields such as max_civil_penalty_usd, "
                "cure_period_days, enforcing_body, and private_right_of_action when "
                "the passage above references these provisions):\n"
                f"{bill_enforcement}"
            )

        if parts:
            prompt += "\n\n" + "\n\n".join(parts)

        return prompt

    def extract(
        self, passage: str, context: dict | None = None,
        call_max_tokens: int | None = None,
    ) -> ExtractionResult:
        """Run extraction on a single passage.

        Returns an ExtractionResult containing either a list of validated
        extractions or an abstention. Supports multi-extraction (multiple
        items from a single passage).

        call_max_tokens: optional per-call token cap (overrides max_tokens_override).
        Used by the extraction pipeline to scale budgets based on passage length.
        """
        prompt = self._resolve_extraction_prompt(passage, context)
        prompt_hash = self._prompt_hash(prompt)
        template_version = self._template.get("version") if self._template else None
        attempt = 0
        # current_max_tokens is mutable — doubles on truncation so short passages
        # start with a scaled budget and escalate only when the model actually
        # needs more tokens.
        current_max_tokens = call_max_tokens

        while attempt <= self.max_retries:
            try:
                raw_output, usage, response_model_id, stop_reason = self._call_llm(
                    prompt, attempt, call_max_tokens=current_max_tokens
                )
                logger.debug(
                    "extraction_pre_parse",
                    agent=self.agent_name,
                    attempt=attempt,
                    raw_output_preview=raw_output[:300],
                )
                cleaned = self._strip_code_fences(raw_output)
                model_reasoning = self._extract_think_blocks(cleaned)
                cleaned = self._strip_think_blocks(cleaned)
                cleaned = self._repair_json(cleaned)
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
                        model_reasoning=model_reasoning,
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

                # Deduplicate extractions — models in output loops
                # produce identical items.  Use a content fingerprint
                # (JSON of the extraction minus metadata keys).
                if len(validated_extractions) > 1:
                    seen: set[str] = set()
                    unique: list[dict] = []
                    _meta = {"_prompt_hash", "_model_id", "_template_version", "evidence_spans"}
                    for ext in validated_extractions:
                        fp = json.dumps(
                            {k: v for k, v in ext.items() if k not in _meta},
                            sort_keys=True,
                            default=str,
                        )
                        if fp not in seen:
                            seen.add(fp)
                            unique.append(ext)
                    if len(unique) < len(validated_extractions):
                        logger.warning(
                            "extraction_duplicates_removed",
                            agent=self.agent_name,
                            original_count=len(validated_extractions),
                            unique_count=len(unique),
                        )
                        validated_extractions = unique

                if was_truncated:
                    # Retry with doubled budget when we have attempts left and
                    # the budget can still be increased.  Cheap passages start
                    # at a scaled budget and escalate only when they need to.
                    _prev = current_max_tokens or settings.extraction_max_tokens
                    _cap = settings.local_extraction_max_tokens
                    _doubled = min(_prev * 2, _cap)
                    if _doubled > _prev and attempt < self.max_retries:
                        logger.warning(
                            "extraction_truncated_retrying",
                            agent=self.agent_name,
                            attempt=attempt,
                            prev_budget=_prev,
                            new_budget=_doubled,
                        )
                        current_max_tokens = _doubled
                        attempt += 1
                        continue
                    logger.warning(
                        "extraction_truncated",
                        agent=self.agent_name,
                        model_id=response_model_id,
                        output_tokens=usage.output_tokens,
                        extractions_count=len(validated_extractions),
                        budget_exhausted=(_doubled == _prev),
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
                    model_reasoning=model_reasoning,
                )

            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                logger.warning(
                    "extraction_validation_failed",
                    agent=self.agent_name,
                    attempt=attempt,
                    error=str(e),
                )
                self._last_error = str(e)

                # Escalate the token budget when the failure was caused by the
                # model running out of output tokens.  Two shapes of that failure
                # reach here:
                #   1. JSONDecodeError with stop_reason="length" — output truncated
                #      mid-JSON.
                #   2. ValueError "Empty response ... finish_reason=length" — the
                #      model spent its whole budget on reasoning and produced no
                #      content (the provider raises this before any parse).
                # Both mean "re-running at the same budget will fail identically",
                # so double the budget before retrying.
                _last_stop = locals().get("stop_reason")
                _length_exhausted = (
                    (isinstance(e, json.JSONDecodeError) and _last_stop == "length")
                    or "finish_reason=length" in str(e)
                )
                if _length_exhausted:
                    _prev = current_max_tokens or settings.extraction_max_tokens
                    _cap = settings.local_extraction_max_tokens
                    _doubled = min(_prev * 2, _cap)
                    if _doubled > _prev:
                        logger.warning(
                            "extraction_parse_failed_escalating_tokens",
                            agent=self.agent_name,
                            attempt=attempt,
                            prev_budget=_prev,
                            new_budget=_doubled,
                        )
                        current_max_tokens = _doubled

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
    def _extract_think_blocks(text: str) -> str | None:
        """Extract reasoning text from <think>...</think> blocks.

        Returns the concatenated reasoning content, or None if no blocks found.
        """
        blocks = re.findall(r"<think>(.*?)</think>", text, flags=re.DOTALL)
        if not blocks:
            return None
        return "\n".join(b.strip() for b in blocks if b.strip()) or None

    @staticmethod
    def _strip_think_blocks(text: str) -> str:
        """Remove <think>...</think> blocks from model output.

        DeepSeek-R1 and similar reasoning models emit chain-of-thought
        wrapped in <think> tags before the actual JSON response. These
        blocks must be stripped before JSON parsing.
        """
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    @staticmethod
    def _repair_json(text: str) -> str:
        """Attempt to repair common JSON issues from local LLMs.

        Handles four patterns that local models produce:

        1. **Invalid control characters**: Models like Gemma emit raw
           control chars (\\x00-\\x1f except \\t, \\n, \\r) inside JSON
           strings. Strip them before parsing.

        2. **Extra data after first object**: Model outputs two JSON objects
           concatenated (e.g., ``{"a":1}{"b":2}``).  We extract just the
           first valid top-level object/array.

        3. **Stringified objects in arrays**: Model wraps inner objects in
           quotes instead of embedding them directly, producing arrays like
           ``[{...}, "{...}", "{...}"]``.  We parse the escaped strings
           back into proper objects.

        4. **Trailing commas**: ``[1, 2, 3,]`` → ``[1, 2, 3]``
        """
        text = text.strip()
        if not text:
            return text

        # --- Fix 0: Strip absolute junk control chars ---
        # Remove chars that are never valid in JSON regardless of context.
        import re as _re
        text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)

        # --- Fix 0b: String-aware escape and control-char repair ---
        # Walk the JSON tracking string state.  Inside strings:
        #   - Invalid escape sequences (\d, \s, \p…) → double the backslash
        #   - Raw \n \r \t inside strings → proper JSON escape sequences
        # This handles "Invalid \escape" and "Invalid control character" errors.
        def _repair_string_contents(s: str) -> str:
            _VALID_ESCAPES = set('"\\\/bfnrtu')
            _CONTROL_ESCAPES = {'\n': '\\n', '\r': '\\r', '\t': '\\t',
                                '\b': '\\b', '\f': '\\f'}
            out: list[str] = []
            in_str = False
            idx = 0
            while idx < len(s):
                ch = s[idx]
                if not in_str:
                    out.append(ch)
                    if ch == '"':
                        in_str = True
                    idx += 1
                else:
                    if ch == '\\':
                        nxt = s[idx + 1] if idx + 1 < len(s) else ''
                        if nxt in _VALID_ESCAPES:
                            out.append(ch)
                            out.append(nxt)
                            idx += 2
                            # Pass through full \uXXXX sequence
                            if nxt == 'u':
                                for _ in range(4):
                                    if idx < len(s):
                                        out.append(s[idx])
                                        idx += 1
                        else:
                            out.append('\\\\')  # double the backslash
                            idx += 1            # next char processed on its own
                    elif ch == '"':
                        in_str = False
                        out.append(ch)
                        idx += 1
                    elif ch in _CONTROL_ESCAPES:
                        # Raw newline/tab/CR inside a string — escape it
                        out.append(_CONTROL_ESCAPES[ch])
                        idx += 1
                    else:
                        out.append(ch)
                        idx += 1
            return ''.join(out)

        try:
            # Only pay the O(n) walk cost when standard parse fails
            json.loads(text)
        except json.JSONDecodeError:
            text = _repair_string_contents(text)

        # --- Fix 1: Extract first complete JSON object/array ---
        # If json.loads fails on the full text, try to find the first
        # complete top-level structure by bracket matching.
        initial_valid = False
        try:
            json.loads(text)
            initial_valid = True
        except json.JSONDecodeError:
            pass

        # Try to extract the first complete JSON structure (only if invalid)
        if not initial_valid and text[0] in "{[":
            depth = 0
            in_string = False
            escape_next = False

            for i, ch in enumerate(text):
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch in "{[":
                    depth += 1
                elif ch in "}]":
                    depth -= 1
                    if depth == 0:
                        candidate = text[: i + 1]
                        try:
                            json.loads(candidate)
                            if candidate != text:
                                logger.debug(
                                    "json_repair_extracted_first_object",
                                    original_len=len(text),
                                    extracted_len=len(candidate),
                                )
                            text = candidate
                            break
                        except json.JSONDecodeError:
                            pass

        # --- Fix 2: Trailing commas ---
        if not initial_valid:
            text = re.sub(r",\s*([}\]])", r"\1", text)

        # --- Fix 3: Truncated JSON (incomplete output from token limit) ---
        # When the LLM hits max_tokens, the JSON is cut off mid-object.
        # Try to salvage by:
        #   a) Stripping back to the last complete object in an array
        #   b) Closing any open brackets/braces
        try:
            json.loads(text)
        except json.JSONDecodeError:
            repaired_text = _repair_truncated_json(text)
            if repaired_text != text:
                try:
                    json.loads(repaired_text)
                    logger.debug(
                        "json_repair_closed_truncated",
                        original_len=len(text),
                        repaired_len=len(repaired_text),
                    )
                    text = repaired_text
                except json.JSONDecodeError:
                    pass

        # --- Fix 4: Stringified objects in arrays ---
        # Pattern: the "extractions" array contains string elements that
        # are actually JSON objects (double-encoded).  This can happen even
        # when the outer JSON is syntactically valid.
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text  # Give up, let the caller handle it

        if isinstance(parsed, dict) and "extractions" in parsed:
            items = parsed["extractions"]
            if isinstance(items, list):
                repaired = []
                did_repair = False
                for item in items:
                    if isinstance(item, str):
                        try:
                            repaired.append(json.loads(item))
                            did_repair = True
                        except json.JSONDecodeError:
                            repaired.append(item)
                    else:
                        repaired.append(item)
                if did_repair:
                    parsed["extractions"] = repaired
                    logger.debug(
                        "json_repair_unescaped_strings",
                        repaired_count=sum(
                            1 for i in items if isinstance(i, str)
                        ),
                    )
                    return json.dumps(parsed)

        # --- Fix 5: Tab/whitespace-prefixed JSON keys ---
        # Some models emit `"\tterm"` instead of `"term"` (the indentation
        # tab ends up inside the quoted key).  Strip leading/trailing whitespace
        # from all string keys recursively so schema validation can match them.
        def _strip_keys(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {(k.strip() if isinstance(k, str) else k): _strip_keys(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_strip_keys(i) for i in obj]
            return obj

        stripped = _strip_keys(parsed)
        if stripped != parsed:
            logger.debug("json_repair_stripped_whitespace_keys")
            return json.dumps(stripped)

        return text

    def _call_llm(self, prompt: str, attempt: int, call_max_tokens: int | None = None) -> tuple[str, Any, str]:
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
            for tag in ("deepseek-r1", "qwen3", "gpt-oss")
        )
        if is_reasoning:
            system_prompt += (
                "\n\nIMPORTANT: Keep your internal reasoning brief and focused. "
                "Do NOT exhaustively analyze every possible interpretation. "
                "Identify the key findings quickly, then produce the JSON output."
            )
        if attempt > 0:
            last_err = getattr(self, "_last_error", "unknown error")
            system_prompt += (
                "\n\nPREVIOUS ATTEMPT FAILED VALIDATION. "
                f"Error: {last_err[:200]}\n"
                "CRITICAL: Your output MUST be a single valid JSON object. "
                "Do NOT output multiple JSON objects. "
                "Do NOT wrap objects in string quotes inside arrays. "
                "Ensure all braces and brackets are properly closed. "
                "Double-check all evidence spans are verbatim quotes from the passage."
            )

        # Use lower max_tokens for local models to fit within context window.
        # call_max_tokens (from dynamic scaling) takes priority over the
        # per-agent override so short passages don't waste GPU time waiting
        # for tokens they can never produce.
        max_tokens = call_max_tokens or self.max_tokens_override or settings.extraction_max_tokens
        if settings.extraction_provider == "local":
            max_tokens = min(max_tokens, settings.local_extraction_max_tokens)

        temperature = self.temperature_override if self.temperature_override is not None else settings.extraction_temperature

        call_kwargs = dict(
            system_prompt=system_prompt,
            user_prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            model_override=self.model_override,
            reasoning_effort=self.reasoning_effort,
        )

        try:
            response = self._provider.call(**call_kwargs)
        except Exception as exc:
            # Retry the SAME model once after a brief pause.  The previous
            # fallback strategy tried a different model (local_extraction_model),
            # but LM Studio can only hold one model in VRAM — so the fallback
            # model was always unloaded, causing a cascade failure.  Retrying
            # the same model is more reliable: the timeout was likely transient
            # (GPU busy with another group's requests).
            import time

            logger.warning(
                "extraction_retry_same_model",
                agent=self.agent_name,
                model=self.model_override or self._provider.model_id,
                error=str(exc)[:300],
            )
            time.sleep(2)
            try:
                response = self._provider.call(**call_kwargs)
            except Exception as retry_exc:
                logger.error(
                    "extraction_retry_failed",
                    agent=self.agent_name,
                    model=self.model_override or self._provider.model_id,
                    original_error=str(exc)[:200],
                    retry_error=str(retry_exc)[:200],
                )
                raise exc from retry_exc

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

    @staticmethod
    def _normalize_unicode(text: str) -> str:
        """Normalize typographic Unicode variants to ASCII equivalents.

        Source PDFs and HTML use typographic characters (smart quotes, en/em
        dashes, non-breaking hyphens) that LLMs replace with ASCII equivalents
        when reproducing verbatim quotes.  Normalizing both sides before
        comparison prevents false verification failures on character-level
        differences that carry no semantic meaning.
        """
        return (
            text
            # Hyphens and dashes → ASCII hyphen
            .replace("\u2011", "-")   # non-breaking hyphen
            .replace("\u2013", "-")   # en-dash
            .replace("\u2014", "-")   # em-dash
            .replace("\u2012", "-")   # figure dash
            # Smart single quotes / apostrophes → ASCII apostrophe
            .replace("\u2018", "'")   # left single quotation mark
            .replace("\u2019", "'")   # right single quotation mark
            .replace("\u201a", "'")   # single low-9 quotation mark
            # Smart double quotes → ASCII double quote
            .replace("\u201c", '"')   # left double quotation mark
            .replace("\u201d", '"')   # right double quotation mark
            .replace("\u201e", '"')   # double low-9 quotation mark
            # Non-breaking and special spaces → regular space
            .replace("\u00a0", " ")   # non-breaking space
            .replace("\u202f", " ")   # narrow no-break space
            .replace("\u2009", " ")   # thin space
        )

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """Collapse all whitespace (spaces, newlines, tabs) to single spaces."""
        return " ".join(text.split())

    def _normalize_text(self, text: str) -> str:
        """Full normalization pipeline: Unicode variants then whitespace."""
        return self._normalize_whitespace(self._normalize_unicode(text))

    def _verify_evidence_spans(
        self, spans: list[dict], passage: str
    ) -> list[dict]:
        """Verify evidence spans via string matching (Rec #3).

        Confirms each evidence span text appears in the passage.
        Applies Unicode normalization (smart quotes, dashes, non-breaking
        spaces) followed by whitespace normalization, then falls back to
        case-insensitive matching for minor casing differences.
        """
        norm_passage = self._normalize_text(passage)
        lower_passage = norm_passage.lower()
        verified = []
        for span_data in spans:
            if not isinstance(span_data, dict) or not span_data.get("text"):
                continue  # skip empty dicts and spans with missing/null text
            try:
                span = EvidenceSpan(**span_data)
            except Exception:
                continue
            norm_span = self._normalize_text(span.text)

            # Try exact match on whitespace-normalized text
            if norm_span in norm_passage:
                start = norm_passage.index(norm_span)
                verified.append(
                    {
                        "field_name": span.field_name,
                        "text": span.text,
                        "char_start": start,
                        "char_end": start + len(norm_span),
                        "verified": True,
                    }
                )
            # Try case-insensitive match
            elif norm_span.lower() in lower_passage:
                start = lower_passage.index(norm_span.lower())
                verified.append(
                    {
                        "field_name": span.field_name,
                        "text": span.text,
                        "char_start": start,
                        "char_end": start + len(norm_span),
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
