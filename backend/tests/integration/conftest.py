"""Fixtures for tests that run against real PostgreSQL and Redis."""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator

import httpx
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.clock import FixedClock
from app.core.config import Environment, Settings
from app.core.db import Database
from app.main import create_app
from app.scripts.seed import seed
from tests.conftest import BACKEND_DIR, IntegrationSettings

# Tables cleaned between tests. Reference data (currencies, RBAC catalog) is left in place.
# Append-only tables (audit_logs, login_history) cannot be truncated, by design; tests that
# read them filter by the rows they created.
MUTABLE_TABLES = ("idempotency_keys", "outbox_events", "scheduled_jobs", "users")

TEST_ENCRYPTION_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
TEST_PIN_PEPPER = base64.urlsafe_b64encode(os.urandom(32)).decode()


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
    await seed(database)  # currencies + RBAC catalog, as in every real environment
    yield database
    await database.dispose()


@pytest.fixture(autouse=True)
async def clean_redis(migrated: IntegrationSettings) -> None:
    """Rate-limit counters must not leak between tests (the test Redis DB is dedicated)."""
    assert migrated.redis_url
    client = Redis.from_url(migrated.redis_url)
    try:
        await client.flushdb()
    finally:
        await client.aclose()


@pytest.fixture(autouse=True)
async def clean_tables(migrator_db: Database) -> None:
    async with migrator_db.transaction() as session:
        tables = ", ".join(f"app.{t}" for t in MUTABLE_TABLES)
        await session.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def app_settings(migrated: IntegrationSettings) -> Settings:
    assert migrated.database_url
    assert migrated.redis_url
    return Settings(
        _env_file=None,  # type: ignore[call-arg]  # hermetic: ignore the developer's backend/.env
        environment=Environment.TEST,
        web_base_url="http://127.0.0.1:8080",
        admin_base_url="http://127.0.0.1:8081",
        database_url=migrated.database_url,
        migration_database_url=migrated.migration_database_url,
        redis_url=migrated.redis_url,
        log_json=False,
        log_level="WARNING",
        cors_origins=["http://localhost:5173"],
        data_encryption_key=TEST_ENCRYPTION_KEY,
        pin_pepper=TEST_PIN_PEPPER,
        cookie_secure=False,
        # Cheap Argon2 parameters keep the suite fast; production uses the defaults.
        argon2_time_cost=1,
        argon2_memory_kib=8,
        argon2_parallelism=1,
    )


@pytest.fixture
async def app(app_settings: Settings, clock: FixedClock) -> AsyncIterator[FastAPI]:
    application = create_app(app_settings, clock=clock)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    # Behaves like the SPA: always sends the CSRF header (ADR-0012).
    async with httpx.AsyncClient(
        transport=transport, base_url="http://viewallet.test", headers={"X-VW-CSRF": "1"}
    ) as http:
        yield http


@pytest.fixture
async def other_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """A second browser (separate cookie jar) against the same app."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://viewallet.test", headers={"X-VW-CSRF": "1"}
    ) as http:
        yield http
