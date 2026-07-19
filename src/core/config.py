"""Application configuration via environment variables."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central application settings loaded from environment variables."""

    # Application
    app_name: str = "regs-checker"
    app_version: str = "0.1.0"
    debug: bool = False

    # Database (PostgreSQL) — local development
    database_url: str = "postgresql://regs:regs@127.0.0.1:5434/regs_checker"

    # Supabase — Regs Checker pipeline DB (extraction source)
    supabase_url: str = ""

    # Supabase — Policy Navigator product DB (sync target)
    policy_navigator_url: str = ""

    # Object Storage (S3 / MinIO)
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket_raw: str = "raw-artifacts"
    s3_bucket_processed: str = "processed-artifacts"

    # LLM Provider routing (all local models via OpenAI-compatible API)
    llm_provider: str = "local"
    discovery_provider: str = "local"
    extraction_provider: str = "local"

    # Local LLM (OpenAI-compatible API: LM Studio, llama.cpp, vLLM, Ollama)
    local_llm_url: str = "http://localhost:1234"  # LM Studio default
    # RR7f: single source of truth — matches CLAUDE.md (google/gemma-4-26b-a4b on R9700)
    local_llm_model: str = "google/gemma-4-26b-a4b"
    local_extraction_model: str = "google/gemma-4-26b-a4b"
    local_triage_model: str = "qwen2.5-vl-3b-instruct"  # Small non-reasoning model for section triage
    local_context_length: int = 131072  # Context window size configured in LM Studio (128k)
    local_extraction_max_tokens: int = 65536  # Max output tokens for extraction

    # Extraction settings (used by agents)
    extraction_model: str = "google/gemma-4-26b-a4b"  # RR7f: matches CLAUDE.md default
    extraction_max_tokens: int = 65536  # Max output tokens per extraction call
    extraction_temperature: float = 0.0  # Temperature for extraction calls

    # RR6b — Per-model concurrency limit for LM Studio (single-GPU: default 1).
    # Prevents VRAM thrashing when multiple agents share the same model.
    # Increase to > 1 only if LM Studio is configured for concurrent requests.
    max_concurrent_agents_per_model: int = 1

    # RR7c — Fraction of passages that bypass triage and run all agents.
    # Provides recall coverage on passages triage might mis-label.
    triage_recall_sample_rate: float = 0.05

    # TA-2 — Concurrent LLM triage calls. triage_passage() does no DB I/O, so
    # it's safe to fan out across a thread pool (mirrors max_concurrent_agents_
    # per_model's extraction pattern). Same LM Studio caveat: single-GPU local
    # setups should set this to 1 to avoid VRAM thrashing; hosted APIs
    # (NVIDIA) tolerate real concurrency.
    triage_concurrency: int = 3

    # FastAPI
    # Default to loopback — the dashboard has no authentication, so binding
    # to all interfaces by default would expose it on any network the host
    # is attached to. Override explicitly (e.g. behind a reverse proxy with
    # its own auth) via REGS_API_HOST.
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # Review UI
    review_items_per_page: int = 25

    # Safety guardrails
    # Minimum confidence tier allowed on user-facing / card-bound surfaces (A/B/C/D).
    # Concepts or extractions below this tier must be flagged or withheld.
    confidence_publish_min_tier: str = "C"

    # QA-9a (docs/qa8_qa9_phased_plan.md Phase 2): restatement-scoped relevance
    # filtering at sync. The engine (src/core/restatement_scope.py) and its
    # wiring into payload_adapter.py are built and tested, but the in-scope
    # rules are a relevance judgment over what hides from the AI-regulation
    # product surface — the plan requires RPR/product ratification (step 4)
    # before this can affect live sync output. Defaults to False; flip only
    # after ratification + a reviewed hide-report (plan Phase 2 acceptance).
    qa9a_scope_filter_enabled: bool = False

    # QA-9b (plan Phase 3): pre-extraction scoping — feed clause agents only
    # a restatement's in-scope subdivisions (from the QA-9c parse-time
    # annotation) instead of the whole restated section. Changes agent
    # INPUTS, so it is gated on the EA1-3 evaluation baseline: capture the
    # baseline on full-passage inputs first, flip this on, rerun the
    # harness, and require no F1 regression before keeping it. Defaults to
    # False; span verification always runs against the full stored passage
    # regardless of this flag.
    qa9b_prescope_enabled: bool = False

    # Orrick PDF Tracker
    orrick_pdf_path: str = "data/trackers/Orrick-US-AI-Law-Tracker.pdf"

    # IAPP PDF Tracker
    iapp_pdf_path: str = "data/trackers/IAPP_Legislation_tracker.pdf"

    # Evaluation
    gold_standard_dir: str = "tests/fixtures/gold_standard"
    # Bill-level (whole-bill) gold-standard fixtures live in their own subtree so
    # the clause-level loader's `*.json` glob never sweeps them into passage eval.
    bill_level_gold_standard_dir: str = "tests/fixtures/gold_standard/bill_level"

    # NVIDIA hosted LLM (OpenAI-compatible — https://integrate.api.nvidia.com/v1)
    # NVIDIA_API_KEY has no REGS_ prefix by convention; set it directly in .env or CI secrets.
    nvidia_api_key: str = Field("", validation_alias="NVIDIA_API_KEY")
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_extraction_model: str = "openai/gpt-oss-120b"
    nvidia_discovery_model: str = "openai/gpt-oss-120b"

    # NIM-0c — retry tuning for NvidiaLLMProvider.call(). Previously hardcoded
    # at 5 retries with a flat 2**attempt backoff; the NIM throughput review
    # flagged this as too shallow for sustained rate-limit windows and
    # vulnerable to synchronized retries when several agents get throttled
    # in the same instant (no jitter). Settings-driven so the cap can be
    # raised without a code change once real throttling behavior is measured.
    nvidia_max_retries: int = 5
    # Ceiling on any single computed backoff wait (exponential growth or a
    # server-supplied Retry-After), so a generous max_retries can't produce
    # an unreasonably long single sleep.
    nvidia_retry_backoff_cap_seconds: float = 30.0
    # Randomizes each wait by +/- this fraction so concurrent agents
    # throttled together don't all retry in the same instant.
    nvidia_retry_jitter_fraction: float = 0.25

    # NIM-1a — client-side requests-per-minute cap, enforced by
    # src/core/llm_rate_limiter.py before every NVIDIA call attempt (shared
    # across threads, per model). The 2026-07-19 live-run evidence showed
    # the pipeline using only ~2.4 calls/min of a reported ~40 RPM/model
    # cap — this isn't a defense against current throttling, it's the
    # guardrail that lets concurrency be raised into that unused headroom
    # without reproducing the throttling problem faster. Default leaves
    # headroom under the reported cap; set to 0 to disable pacing entirely
    # (e.g. for a controlled benchmark).
    nvidia_rpm_limit: float = 35.0

    model_config = {"env_prefix": "REGS_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
