"""Fixtures for tests that run against real PostgreSQL and Redis."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy import text

from app.core.clock import FixedClock
from app.core.config import Environment, Settings
from app.core.db import Database
from app.main import create_app
from tests.conftest import BACKEND_DIR, IntegrationSettings

# Tables cleaned between tests. Reference data (currencies) is left in place.
MUTABLE_TABLES = ("idempotency_keys", "outbox_events", "scheduled_jobs")


def alembic_config(url: str) -> Config:
    config = Config()  # no ini file: keeps Alembic from reconfiguring logging
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="session")
def migrated(test_settings: IntegrationSettings) -> IntegrationSettings:
    """Rebuild the test database schema from scratch once per session (also proves downgrade)."""
    assert test_settings.migration_database_url
    config = alembic_config(test_settings.migration_database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return test_settings


@pytest.fixture(scope="session")
async def db(migrated: IntegrationSettings) -> AsyncIterator[Database]:
    """Database as the least-privileged application role."""
    assert migrated.database_url
    database = Database(migrated.database_url, pool_size=20, max_overflow=10)
    yield database
    await database.dispose()


@pytest.fixture(scope="session")
async def migrator_db(migrated: IntegrationSettings) -> AsyncIterator[Database]:
    assert migrated.migration_database_url
    database = Database(migrated.migration_database_url, pool_size=2)
    yield database
    await database.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(migrator_db: Database) -> None:
    async with migrator_db.transaction() as session:
        tables = ", ".join(f"app.{t}" for t in MUTABLE_TABLES)
        await session.execute(text(f"TRUNCATE {tables}"))


@pytest.fixture
def app_settings(migrated: IntegrationSettings) -> Settings:
    assert migrated.database_url
    assert migrated.redis_url
    return Settings(
        environment=Environment.TEST,
        database_url=migrated.database_url,
        migration_database_url=migrated.migration_database_url,
        redis_url=migrated.redis_url,
        log_json=False,
        log_level="WARNING",
        cors_origins=["http://localhost:5173"],
    )


@pytest.fixture
async def app(app_settings: Settings, clock: FixedClock) -> AsyncIterator[FastAPI]:
    application = create_app(app_settings, clock=clock)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
