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

    model_config = {"env_prefix": "REGS_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
