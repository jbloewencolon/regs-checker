"""Smoke test for the NVIDIA LLM provider.

Run this before flipping REGS_EXTRACTION_PROVIDER=nvidia on any real extraction run.
It verifies: key validity, model availability, chat completion, and a realistic
extraction-shaped prompt.

Usage:
    python -m src.scripts.smoke_test_nvidia

Requires NVIDIA_API_KEY in your environment or .env file.
"""
from __future__ import annotations

import json
import sys
import time


def _check(label: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    msg = f"  [{status}] {label}"
    if detail:
        msg += f"\n         {detail}"
    print(msg)
    if not passed:
        sys.exit(1)


def main() -> None:
    print("\n=== NVIDIA Provider Smoke Test ===\n")

    # ------------------------------------------------------------------
    # 1. Settings load
    # ------------------------------------------------------------------
    print("1. Settings")
    from src.core.config import settings
    _check(
        "NVIDIA_API_KEY is set",
        bool(settings.nvidia_api_key),
        "Add NVIDIA_API_KEY=nvapi-... to your .env or shell environment",
    )
    _check(
        "nvidia_base_url configured",
        bool(settings.nvidia_base_url),
        f"Current: {settings.nvidia_base_url!r}",
    )
    print(f"         model: {settings.nvidia_extraction_model}")
    print(f"         base:  {settings.nvidia_base_url}")

    # ------------------------------------------------------------------
    # 2. Provider instantiation
    # ------------------------------------------------------------------
    print("\n2. Provider init")
    from src.core.llm_provider import NvidiaLLMProvider
    try:
        provider = NvidiaLLMProvider()
        _check("NvidiaLLMProvider instantiated", True, f"model_id={provider.model_id}")
    except Exception as e:
        _check("NvidiaLLMProvider instantiated", False, str(e))

    # ------------------------------------------------------------------
    # 3. Models endpoint — confirms key is valid and model is available
    # ------------------------------------------------------------------
    print("\n3. Model catalog check")
    import httpx
    try:
        resp = httpx.get(
            f"{settings.nvidia_base_url}/models",
            headers={"Authorization": f"Bearer {settings.nvidia_api_key}"},
            timeout=15.0,
        )
        _check(f"GET /v1/models → HTTP {resp.status_code}", resp.status_code == 200,
               resp.text[:200] if resp.status_code != 200 else "")
        model_ids = [m["id"] for m in resp.json().get("data", [])]
        target = settings.nvidia_extraction_model
        _check(
            f"{target!r} in catalog",
            target in model_ids,
            f"Available: {', '.join(sorted(model_ids)[:10])}{'...' if len(model_ids) > 10 else ''}",
        )
        print(f"         {len(model_ids)} models available")
    except Exception as e:
        _check("Models endpoint reachable", False, str(e))

    # ------------------------------------------------------------------
    # 4. Minimal chat completion
    # ------------------------------------------------------------------
    print("\n4. Chat completion (minimal)")
    try:
        t0 = time.monotonic()
        result = provider.call(
            system_prompt="You are a helpful assistant. Output only valid JSON.",
            user_prompt='Reply with exactly this JSON object and nothing else: {"ok": true}',
            max_tokens=512,
            temperature=0.0,
        )
        elapsed = time.monotonic() - t0
        _check(
            "Response received",
            bool(result.text.strip()),
            f"text={result.text!r}  tokens=in:{result.usage.input_tokens}/out:{result.usage.output_tokens}  "
            f"stop={result.stop_reason}  elapsed={elapsed:.1f}s",
        )
    except Exception as e:
        _check("Chat completion", False, str(e))

    # ------------------------------------------------------------------
    # 5. Extraction-shaped prompt (JSON output)
    # ------------------------------------------------------------------
    print("\n5. Extraction prompt (structured JSON output)")
    passage = (
        "Section 6(a). Any developer of a high-risk AI system shall conduct "
        "an impact assessment prior to deployment and maintain records for five years."
    )
    prompt = (
        "Extract the following from the legal passage and return as a JSON object:\n"
        '{"subject": "", "action": "", "modality": "", "section_reference": ""}\n\n'
        f"PASSAGE:\n{passage}"
    )
    try:
        t0 = time.monotonic()
        result = provider.call(
            system_prompt="You are a legal analyst. Return only valid JSON.",
            user_prompt=prompt,
            max_tokens=256,
            temperature=0.0,
        )
        elapsed = time.monotonic() - t0
        text = result.text.strip()
        # Strip markdown fences
        if text.startswith("```"):
            text = "\n".join(
                l for l in text.splitlines()
                if not l.strip().startswith("```")
            ).strip()
        try:
            parsed = json.loads(text)
            _check(
                "Extraction output is valid JSON",
                True,
                f"elapsed={elapsed:.1f}s  tokens=in:{result.usage.input_tokens}/out:{result.usage.output_tokens}",
            )
            print("\n         Extracted payload:")
            for k, v in parsed.items():
                print(f"           {k}: {v!r}")
        except json.JSONDecodeError:
            _check(
                "Extraction output is valid JSON",
                False,
                f"Raw output:\n{result.text[:400]}",
            )
    except Exception as e:
        _check("Extraction prompt", False, str(e))

    # ------------------------------------------------------------------
    # 6. Provider switch confirmation
    # ------------------------------------------------------------------
    print("\n6. Provider routing check")
    from src.core.llm_provider import _provider_cache
    _provider_cache.clear()

    import os
    os.environ["REGS_EXTRACTION_PROVIDER"] = "nvidia"
    # Re-import settings to pick up the env change
    import importlib
    import src.core.config
    importlib.reload(src.core.config)
    import src.core.llm_provider
    importlib.reload(src.core.llm_provider)

    from src.core.llm_provider import get_extraction_provider as gep
    ep = gep()
    _check(
        "get_extraction_provider() returns NvidiaLLMProvider when REGS_EXTRACTION_PROVIDER=nvidia",
        type(ep).__name__ == "NvidiaLLMProvider",
        f"Got: {type(ep).__name__}",
    )

    print("\n=== All checks passed. NVIDIA provider is ready. ===")
    print("\nTo activate for extraction runs, add to your .env:")
    print("  REGS_EXTRACTION_PROVIDER=nvidia")
    print("Then restart the server.\n")


if __name__ == "__main__":
    main()
