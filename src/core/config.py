"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central application settings loaded from environment variables."""

    # Application
    app_name: str = "regs-checker"
    app_version: str = "0.1.0"
    debug: bool = False

    # Database (PostgreSQL)
    database_url: str = "postgresql://regs:regs@localhost:5432/regs_checker"

    # Object Storage (S3 / MinIO)
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket_raw: str = "raw-artifacts"
    s3_bucket_processed: str = "processed-artifacts"

    # Anthropic API
    anthropic_api_key: str = ""
    extraction_model: str = "claude-sonnet-4-20250514"
    extraction_temperature: float = 0.0
    extraction_max_tokens: int = 8192

    # FastAPI
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Review UI
    review_items_per_page: int = 25

    # Evaluation
    gold_standard_dir: str = "tests/fixtures/gold_standard"

    model_config = {"env_prefix": "REGS_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
