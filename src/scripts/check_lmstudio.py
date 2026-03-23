"""Quick diagnostic: check LM Studio models and context length.

Usage:
    python -m src.scripts.check_lmstudio
"""

import httpx
import json
import sys


def main():
    base_url = "http://localhost:1234"

    print(f"Checking LM Studio at {base_url}...\n")

    # 1. List available models
    try:
        resp = httpx.get(f"{base_url}/v1/models", timeout=5)
        resp.raise_for_status()
        models = resp.json()
        print("=== Available Models ===")
        for m in models.get("data", []):
            print(f"  ID: {m['id']}")
        print()
    except Exception as e:
        print(f"ERROR: Cannot connect to LM Studio at {base_url}: {e}")
        print("Make sure LM Studio is running with the server enabled.")
        sys.exit(1)

    # 2. Test a minimal prompt to check context length
    model_ids = [m["id"] for m in models.get("data", [])]

    for model_id in model_ids:
        print(f"--- Testing model: {model_id} ---")
        try:
            resp = httpx.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": model_id,
                    "messages": [
                        {"role": "system", "content": "Reply with just: OK"},
                        {"role": "user", "content": "Test."},
                    ],
                    "max_tokens": 10,
                    "temperature": 0,
                },
                timeout=30,
            )
            if resp.status_code >= 400:
                print(f"  ERROR: HTTP {resp.status_code}")
                print(f"  Body: {resp.text[:300]}")
            else:
                data = resp.json()
                reply = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                print(f"  Reply: {reply}")
                print(f"  Prompt tokens: {usage.get('prompt_tokens', '?')}")
                print(f"  Completion tokens: {usage.get('completion_tokens', '?')}")
                print(f"  Status: OK")
        except Exception as e:
            print(f"  ERROR: {e}")
        print()

    # 3. Suggest the model names to use in config
    print("=== Model Name Mapping ===")
    print("Set these model_override values in your agents:")
    print()
    for model_id in model_ids:
        lower = model_id.lower()
        if "deepseek" in lower:
            print(f'  Ambiguity agent (reasoning): model_override = "{model_id}"')
        elif "qwen" in lower:
            print(f'  (Qwen available but not assigned — no thinking mode toggle)')
        elif "gpt" in lower or "oss" in lower:
            print(f'  Obligation agent:            model_override = "{model_id}"')
            print(f'  Compliance Mechanism agent:   model_override = "{model_id}"')
            print(f'  Definition/Actor agent:       model_override = "{model_id}"')
            print(f'  Rights Protection agent:      model_override = "{model_id}"')
            print(f'  Threshold/Exception agent:    model_override = "{model_id}"')
        else:
            print(f'  Unknown model type:          "{model_id}"')
    print()
    print("IMPORTANT: In LM Studio, set Context Length to at least 8192")
    print("(32768 recommended). Currently your models have n_ctx=4096")
    print("which is too small for extraction prompts.")


if __name__ == "__main__":
    main()
