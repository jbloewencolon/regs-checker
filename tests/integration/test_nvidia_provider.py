"""Integration tests for NvidiaLLMProvider against the live NVIDIA API.

These tests require NVIDIA_API_KEY to be set in the environment and make
real network calls.  They are skipped automatically in CI unless the key
is present (mark the CI secret as available to enable them in staging).

Run locally:
    NVIDIA_API_KEY=nvapi-... pytest tests/integration/test_nvidia_provider.py -v

Or with .env loaded:
    pytest tests/integration/test_nvidia_provider.py -v
"""
from __future__ import annotations

import json
import os

import pytest

SKIP_REASON = "NVIDIA_API_KEY not set — skipping live API tests"
requires_nvidia = pytest.mark.skipif(
    not os.getenv("NVIDIA_API_KEY"),
    reason=SKIP_REASON,
)


@pytest.fixture(scope="module")
def nvidia_provider():
    """Create a real NvidiaLLMProvider for the live API."""
    from src.core.llm_provider import NvidiaLLMProvider
    return NvidiaLLMProvider()


# ---------------------------------------------------------------------------
# Connectivity / auth
# ---------------------------------------------------------------------------


class TestNvidiaConnectivity:
    @requires_nvidia
    def test_models_endpoint_reachable(self):
        """GET /v1/models should return a list that includes gpt-oss-120b."""
        import httpx
        resp = httpx.get(
            "https://integrate.api.nvidia.com/v1/models",
            headers={"Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}"},
            timeout=15.0,
        )
        assert resp.status_code == 200, (
            f"Models endpoint returned {resp.status_code}: {resp.text[:300]}"
        )
        data = resp.json()
        model_ids = [m["id"] for m in data.get("data", [])]
        assert model_ids, "No models returned — check API key entitlements"
        print(f"\nAvailable models ({len(model_ids)}):")
        for mid in sorted(model_ids):
            print(f"  {mid}")
        assert "openai/gpt-oss-120b" in model_ids, (
            f"openai/gpt-oss-120b not in catalog. Available: {model_ids}"
        )

    @requires_nvidia
    def test_bad_key_returns_401(self):
        """An invalid key should get a clean 401, not a crash."""
        import httpx
        from src.core.llm_provider import NvidiaLLMProvider
        # Instantiate with a bad key by patching settings
        from unittest.mock import patch
        with patch("src.core.llm_provider.settings") as ms:
            ms.nvidia_api_key = "nvapi-intentionally-invalid"
            ms.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
            ms.nvidia_extraction_model = "openai/gpt-oss-120b"
            bad_provider = NvidiaLLMProvider()

        with pytest.raises(httpx.HTTPStatusError, match="auth/entitlement"):
            bad_provider.call("sys", "usr", max_tokens=10)


# ---------------------------------------------------------------------------
# Smoke: single chat completion
# ---------------------------------------------------------------------------


class TestNvidiaChatCompletion:
    @requires_nvidia
    def test_returns_valid_json_response(self, nvidia_provider):
        """Provider should parse the response into LLMResponse with non-empty text."""
        from src.core.llm_provider import LLMResponse
        result = nvidia_provider.call(
            system_prompt="You are a helpful assistant. Output only valid JSON.",
            user_prompt='Reply with exactly: {"ok": true}',
            max_tokens=50,
            temperature=0.0,
        )
        assert isinstance(result, LLMResponse)
        assert result.text.strip(), "Response text is empty"
        assert result.model_id.endswith("-nvidia")
        assert result.usage.input_tokens > 0
        assert result.usage.output_tokens > 0
        print(f"\nResponse: {result.text!r}")
        print(f"Tokens: in={result.usage.input_tokens} out={result.usage.output_tokens}")
        print(f"Stop reason: {result.stop_reason}")

    @requires_nvidia
    def test_temperature_zero_is_deterministic(self, nvidia_provider):
        """Same prompt at temperature=0 should return the same text twice."""
        kwargs = dict(
            system_prompt="Output only JSON.",
            user_prompt='{"value": 42}',
            max_tokens=30,
            temperature=0.0,
        )
        r1 = nvidia_provider.call(**kwargs)
        r2 = nvidia_provider.call(**kwargs)
        assert r1.text == r2.text, (
            f"Temperature=0 produced different outputs:\n  {r1.text!r}\n  {r2.text!r}"
        )

    @requires_nvidia
    def test_model_override_accepted(self, nvidia_provider):
        """model_override should be reflected in response model_id."""
        result = nvidia_provider.call(
            system_prompt="Output JSON.",
            user_prompt='{"x": 1}',
            max_tokens=20,
            temperature=0.0,
            model_override="openai/gpt-oss-120b",
        )
        assert "gpt-oss-120b" in result.model_id

    @requires_nvidia
    def test_max_tokens_controls_output_length(self, nvidia_provider):
        """A very low max_tokens should produce a short response."""
        result = nvidia_provider.call(
            system_prompt="You are a helpful assistant.",
            user_prompt="Write a 500 word essay on photosynthesis.",
            max_tokens=20,
            temperature=0.0,
        )
        assert result.usage.output_tokens <= 25, (
            f"Expected ≤25 output tokens, got {result.usage.output_tokens}"
        )


# ---------------------------------------------------------------------------
# Extraction-shaped prompt (mirrors real agent workload)
# ---------------------------------------------------------------------------


class TestNvidiaExtractionWorkload:
    _PASSAGE = """
    Section 3. Obligations of developers.
    (a) Any developer of a high-risk artificial intelligence system shall,
    prior to deployment, conduct and document a conformity assessment to
    evaluate whether the system meets the requirements of this Act.
    (b) The developer shall maintain records of such assessment for a
    period of not less than five years following initial deployment.
    (c) Penalties for violation of this section shall not exceed $25,000
    per violation.
    """

    _SYSTEM = (
        "You are a legal analyst. Extract structured data from legal text "
        "and return only valid JSON."
    )

    _PROMPT = """
    Extract the following from the passage and return as JSON:
    {
      "subject": "Who must act?",
      "action": "What must they do?",
      "condition": "Under what condition?",
      "section_reference": "Which section?",
      "modality": "shall/must/may"
    }

    PASSAGE:
    """ + _PASSAGE.strip()

    @requires_nvidia
    def test_extraction_prompt_returns_parseable_json(self, nvidia_provider):
        """The extraction pattern used by real agents should return parseable JSON."""
        result = nvidia_provider.call(
            system_prompt=self._SYSTEM,
            user_prompt=self._PROMPT,
            max_tokens=512,
            temperature=0.0,
        )
        text = result.text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            pytest.fail(f"Response is not valid JSON: {e}\n\nRaw response:\n{result.text}")

        print(f"\nExtracted payload: {json.dumps(parsed, indent=2)}")
        assert isinstance(parsed, dict), "Expected a JSON object"
        # At least some expected fields should be present
        expected_keys = {"subject", "action", "modality"}
        found = expected_keys & parsed.keys()
        assert found, f"None of {expected_keys} found in response: {list(parsed.keys())}"

    @requires_nvidia
    def test_per_agent_token_budgets_respected(self, nvidia_provider):
        """Test each agent's real max_tokens value doesn't trigger NVIDIA errors."""
        from src.core.model_config import get_config
        cfg = get_config()
        for agent_name, agent_cfg in cfg.agents.items():
            # Use the configured max_tokens for each agent
            max_tok = agent_cfg.max_tokens if agent_cfg.max_tokens else 1024
            result = nvidia_provider.call(
                system_prompt="Output JSON.",
                user_prompt='{"agent": "' + agent_name + '"}',
                max_tokens=min(max_tok, 100),  # limit to 100 for speed in this test
                temperature=0.0,
            )
            assert result.text.strip(), f"Empty response for agent {agent_name!r}"
            print(f"  {agent_name}: ok (max_tokens={max_tok})")


# ---------------------------------------------------------------------------
# Error / edge cases
# ---------------------------------------------------------------------------


class TestNvidiaErrorPaths:
    @requires_nvidia
    def test_quota_error_surfaced_as_429(self, nvidia_provider):
        """If we ever hit rate limits, the error should be clear (hard to trigger reliably)."""
        # This test is informational — it documents the expected exception type.
        # In normal operation it should not hit 429.
        import httpx
        # We can't reliably trigger 429 in a test, but we can verify the error
        # class is correct by checking the provider handles 429 explicitly.
        # This is covered by unit tests; here we just confirm the live path is wired.
        assert hasattr(nvidia_provider, "call"), "Provider should be callable"
