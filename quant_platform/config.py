"""Platform configuration loaded via environment variables."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PlatformSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QUANT_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = Field(
        default="sqlite:///./quant_platform.sqlite3",
        description="SQLAlchemy database URL (PostgreSQL in production, SQLite supported for self-contained runs).",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis broker/cache URL.",
    )
    master_encryption_key: str = Field(
        default="",
        description="Base64 urlsafe 32-byte Fernet master key (must be set externally in live deployment).",
    )
    max_clock_skew_ms: int = Field(
        default=1000,
        description="Maximum allowed absolute server clock offset in milliseconds before blocking orders.",
    )
    sandbox_timeout_sec: float = Field(
        default=5.0,
        description="Default sandbox execution timeout in seconds.",
    )
    sandbox_max_memory_mb: int = Field(
        default=256,
        description="Default sandbox maximum memory limit in MB.",
    )
    api_admin_token: str = Field(
        default="dev-quant-admin-token",
        description="Admin bearer token for mutating API endpoints.",
    )
    llm_api_base: str = Field(
        default="",
        description="Optional OpenAI-compatible API base URL for NL strategy generation.",
    )
    llm_api_key: str = Field(
        default="",
        description="Optional API key for NL strategy generation.",
    )
    llm_model: str = Field(
        default="gpt-4o-mini",
        description="Model name for NL strategy generation when external LLM endpoint is configured.",
    )


def get_settings() -> PlatformSettings:
    return PlatformSettings()
