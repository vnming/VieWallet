"""Simulator settings (prefix ``SIM_``)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SimSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIM_", env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = Field(description="Async SQLAlchemy URL (simulator role, own database).")
    log_level: str = "INFO"

    # Secrets shared with VieWallet's adapter. No defaults on purpose.
    api_key: str = Field(description="Bearer key VieWallet uses to call the simulator.")
    webhook_secret: str = Field(description="HMAC key for callbacks sent to VieWallet.")
    admin_key: str = Field(description="Key for the scenario control endpoints.")
    enable_controls: bool = True

    public_base_url: str = "http://127.0.0.1:8090"
    allowed_return_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:8080", "http://localhost:8080"]
    )

    # Scenario timings (seconds). Tests and E2E shorten them.
    payout_delay_seconds: float = Field(default=3, ge=0)
    pending_delay_seconds: float = Field(default=120, ge=0)
    slow_response_seconds: float = Field(default=30, ge=0)
    return_delay_seconds: float = Field(default=300, ge=0)
    followup_callback_seconds: float = Field(default=60, ge=0)
    charge_ttl_hours: float = Field(default=24, gt=0)
    callback_timeout_seconds: float = Field(default=5, gt=0)
    worker_interval_seconds: float = Field(default=1, gt=0)

    @property
    def controls_enabled(self) -> bool:
        return self.enable_controls and self.environment in {"development", "simulation", "test"}


@lru_cache(maxsize=1)
def get_sim_settings() -> SimSettings:
    return SimSettings()  # type: ignore[call-arg]
