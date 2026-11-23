"""Shared pytest configuration.

Integration tests (``tests/integration``) need PostgreSQL and Redis. They read
``VW_TEST_DATABASE_URL``, ``VW_TEST_MIGRATION_DATABASE_URL`` and ``VW_TEST_REDIS_URL`` from the
environment or ``backend/.env``; when those are missing the tests are skipped with a clear
reason (CI always sets them).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.clock import FixedClock

BACKEND_DIR = Path(__file__).resolve().parent.parent


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    # psycopg's async mode cannot run on the Windows ProactorEventLoop.
    if os.name == "nt":
        return {"selector": asyncio.SelectorEventLoop}
    return {"asyncio": asyncio.new_event_loop}


class IntegrationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VW_TEST_", env_file=BACKEND_DIR / ".env", extra="ignore"
    )

    database_url: str | None = None
    migration_database_url: str | None = None
    redis_url: str | None = None
    sim_database_url: str | None = None  # the provider simulator's own test database

    @property
    def complete(self) -> bool:
        return bool(
            self.database_url
            and self.migration_database_url
            and self.redis_url
            and self.sim_database_url
        )


@pytest.fixture(scope="session")
def test_settings() -> IntegrationSettings:
    return IntegrationSettings()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    settings = IntegrationSettings()
    skip = pytest.mark.skip(
        reason="integration tests need VW_TEST_DATABASE_URL, VW_TEST_MIGRATION_DATABASE_URL "
        "VW_TEST_REDIS_URL and VW_TEST_SIM_DATABASE_URL (see backend/.env.example)"
    )
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(pytest.mark.integration)
            if not settings.complete:
                item.add_marker(skip)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 21, 8, 0, tzinfo=UTC))
