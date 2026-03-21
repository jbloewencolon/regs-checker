"""Application configuration via environment variables."""

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

    # LLM Provider routing
    llm_provider: str = "anthropic"  # Default provider: "anthropic" or "local"
    discovery_provider: str = "local"  # Provider for discovery tasks
    extraction_provider: str = "anthropic"  # Provider for extraction tasks

    # Anthropic API
    anthropic_api_key: str = ""
    extraction_model: str = "claude-haiku-4-5-20251001"
    extraction_temperature: float = 0.0
    extraction_max_tokens: int = 8192

    # Local LLM (OpenAI-compatible API: LM Studio, llama.cpp, vLLM, Ollama)
    local_llm_url: str = "http://localhost:1234"  # LM Studio default
    local_llm_model: str = "Qwen3.5"  # Default model for discovery tasks
    local_extraction_model: str = "DeepSeek-R1-0528"  # Default model for local extraction

    # Web search (for fallback URL verification)
    search_provider: str = ""  # "tavily", "serper", or "google_cse"
    tavily_api_key: str = ""
    serper_api_key: str = ""
    google_cse_api_key: str = ""
    google_cse_cx: str = ""  # Custom Search Engine ID

    # FastAPI
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Review UI
    review_items_per_page: int = 25

    # Orrick PDF Tracker
    orrick_pdf_path: str = "static/Orrick-US-AI-Law-Tracker.pdf"

    # IAPP PDF Tracker
    iapp_pdf_path: str = "static/IAPP_Legislation_tracker.pdf"

    # Evaluation
    gold_standard_dir: str = "tests/fixtures/gold_standard"

    model_config = {"env_prefix": "REGS_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
