from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_ignore_empty=True, extra="ignore")

    # Secrets and connection strings: env only, no defaults.
    database_url: str
    redis_url: str | None = None
    keycloak_url: str | None = None
    keycloak_realm: str | None = None
    keycloak_client_id: str | None = None
    keycloak_client_secret: str | None = None
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    anthropic_api_key: str | None = None
    voyage_api_key: str | None = None
    openai_api_key: str | None = None
    cohere_api_key: str | None = None
    sentry_dsn_api: str | None = None
    alert_webhook_url: str | None = None

    # Object storage
    s3_region: str = "us-east-1"
    s3_bucket_pdfs: str = "askmysyllabus-pdfs"
    s3_bucket_markdown: str = "askmysyllabus-markdown"
    s3_bucket_backups: str = "askmysyllabus-backups"

    # Models and pipeline (bake-off switches, DESIGN.md 9, 11.1, 12, 13)
    pdf_converter: str = "docling"
    embedding_model: str = "voyage-context-4"
    embedding_dim: int = 1024
    reranker_model: str = "rerank-3"
    reranker_fallback_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    llm_model: str = "claude-haiku-5-5"
    llm_model_strong: str = "claude-sonnet-5-5"
    chunking_strategy: str = "section_aware"
    chunk_size_tokens: int = 500
    chunk_overlap_tokens: int = 50
    full_context_max_tokens: int = 30000  # tuned by eval
    abstain_threshold: float = 0.3  # tuned by eval

    # Budget and limits (DESIGN.md 14)
    ai_monthly_budget_usd: float = 200.0
    daily_limit_min: int = 10
    daily_limit_max: int = 50
    burst_limit_per_minute: int = 5

    # Kill switches
    maintenance_mode: bool = False
    pause_ai: bool = False
    disable_signups: bool = False

    # Privacy (DESIGN.md 7)
    consent_policy_version: str = "1"
    retention_months: int = 12
    account_deletion_grace_days: int = 7

    @field_validator("database_url")
    @classmethod
    def use_asyncpg(cls, v: str) -> str:
        return v.replace("postgresql://", "postgresql+asyncpg://", 1)


settings = Settings()
