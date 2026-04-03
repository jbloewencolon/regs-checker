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

    # LLM Provider routing (all local models via OpenAI-compatible API)
    llm_provider: str = "local"
    discovery_provider: str = "local"
    extraction_provider: str = "local"

    # Local LLM (OpenAI-compatible API: LM Studio, llama.cpp, vLLM, Ollama)
    local_llm_url: str = "http://localhost:1234"  # LM Studio default
    local_llm_model: str = "openai/gpt-oss-20b"  # Default model for discovery tasks
    local_extraction_model: str = "openai/gpt-oss-20b"  # Default model for extraction
    local_context_length: int = 32768  # Context window size configured in LM Studio
    local_extraction_max_tokens: int = 50000  # Max output tokens for extraction

    # Extraction settings (used by agents)
    extraction_model: str = "openai/gpt-oss-20b"  # Model ID for tracking
    extraction_max_tokens: int = 50000  # Max output tokens per extraction call
    extraction_temperature: float = 0.0  # Temperature for extraction calls

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
