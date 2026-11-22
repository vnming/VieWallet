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

    # --- Secrets (no defaults). Base64-encoded 32-byte keys. -------------------------
    data_encryption_key: str = Field(
        description="AES-256-GCM key for encrypted columns and secret outbox payloads."
    )
    pin_pepper: str = Field(description="HMAC key applied to transaction PINs before Argon2id.")

    # --- Sessions (ADR-0012) -------------------------------------------------------------
    cookie_secure: bool = True
    web_session_idle_hours: int = Field(default=24 * 7, ge=1)
    web_session_absolute_hours: int = Field(default=24 * 30, ge=1)
    admin_session_idle_minutes: int = Field(default=30, ge=5)
    admin_session_absolute_hours: int = Field(default=8, ge=1)

    # --- Password/PIN hashing (Argon2id). Tests lower the cost for speed. -----------------
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_kib: int = Field(default=64 * 1024, ge=8)
    argon2_parallelism: int = Field(default=4, ge=1)

    # --- Email (Mailpit in development) ----------------------------------------------------
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_from: str = "VieWallet <no-reply@viewallet.local>"
    web_base_url: str = "http://127.0.0.1:8080"
    admin_base_url: str = "http://127.0.0.1:8081"

    @property
    def expose_api_docs(self) -> bool:
        return self.environment is not Environment.PRODUCTION_LIKE

    @property
    def trusted_origins(self) -> frozenset[str]:
        """Origins allowed to send state-changing requests (CSRF Origin check)."""
        return frozenset(o.rstrip("/") for o in self.cors_origins) | {
            self.web_base_url.rstrip("/"),
            self.admin_base_url.rstrip("/"),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
