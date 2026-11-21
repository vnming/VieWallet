"""Application settings loaded from environment variables (prefix ``VW_``)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    SIMULATION = "simulation"
    TEST = "test"
    PRODUCTION_LIKE = "production-like"


class Settings(BaseSettings):
    """Runtime configuration.

    Secrets (database passwords) have no defaults on purpose: they must come from the
    environment or a local, git-ignored ``.env`` file.
    """

    model_config = SettingsConfigDict(env_prefix="VW_", env_file=".env", extra="ignore")

    environment: Environment = Environment.DEVELOPMENT
    app_version: str = "0.1.0"

    database_url: str = Field(description="Async SQLAlchemy URL for the application role.")
    migration_database_url: str | None = Field(
        default=None, description="URL for the migrator role (Alembic, seed)."
    )
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)

    redis_url: str = "redis://127.0.0.1:6379/0"

    log_level: str = "INFO"
    log_json: bool = True

    cors_origins: list[str] = Field(default_factory=list)

    business_timezone: str = "Asia/Ho_Chi_Minh"

    idempotency_ttl_hours: int = Field(default=24, ge=1, le=168)
    idempotency_lock_seconds: int = Field(default=60, ge=5, le=3600)

    outbox_batch_size: int = Field(default=50, ge=1, le=1000)
    outbox_max_attempts: int = Field(default=10, ge=1, le=100)
    worker_idle_sleep_seconds: float = Field(default=1.0, gt=0, le=60)

    @property
    def expose_api_docs(self) -> bool:
        return self.environment is not Environment.PRODUCTION_LIKE


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
