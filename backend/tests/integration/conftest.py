"""Fixtures for tests that run against real PostgreSQL and Redis."""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import create_engine, text

from app.core.clock import FixedClock
from app.core.config import Environment, Settings
from app.core.db import Database
from app.main import create_app
from app.modules.providers.public import SIMULATOR, SimulatorControl, SimulatorProvider
from app.scripts.seed import seed
from sim.config import SimSettings
from sim.db import SimDatabase
from sim.main import create_sim_app
from tests.conftest import BACKEND_DIR, IntegrationSettings

# Tables cleaned between tests. Reference data (currencies, RBAC catalog) is left in place.
# Append-only tables (audit_logs, login_history) cannot be truncated, by design; tests that
# read them filter by the rows they created.
MUTABLE_TABLES = (
    "idempotency_keys",
    "outbox_events",
    "scheduled_jobs",
    "reconciliation_runs",
    "provider_operations",
    "recovery_cases",
    "risk_alerts",
    "account_risk_flags",
    "risk_rules",
    "users",  # cascades to wallets, transactions, approval_requests, transaction_events
)
# Append-only children reached by the cascade. The test fixture (table owner) lifts their
# TRUNCATE guard inside its own transaction; the application role can never do this.
CASCADED_APPEND_ONLY = ("transaction_events", "kyc_reviews", "support_ticket_messages")

SIM_TABLES = ("sim_callbacks", "sim_operations", "sim_settings", "sim_logs")
SIM_API_KEY = "test-sim-api-key"  # gitleaks:allow (throwaway test value)
SIM_WEBHOOK_SECRET = "test-sim-webhook-secret"  # gitleaks:allow
SIM_ADMIN_KEY = "test-sim-admin-key"  # gitleaks:allow
SIM_BASE = "http://simulator.test"

TEST_ENCRYPTION_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
TEST_PIN_PEPPER = base64.urlsafe_b64encode(os.urandom(32)).decode()


# Seeded fee/limit rules are reference data (kept between tests); versions added by a test are
# removed so every test, and the next session's downgrade, starts from the seeded version 1.
RESET_PRICING = (
    "DELETE FROM app.fee_rules WHERE version > 1",
    "UPDATE app.fee_rules SET status = 'ACTIVE', effective_to = NULL",
    "DELETE FROM app.limit_rules WHERE version > 1",
    "UPDATE app.limit_rules SET status = 'ACTIVE'",
)


def reset_pricing_sync(url: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            exists = conn.scalar(
                text("SELECT to_regclass('app.fee_rules') IS NOT NULL")
            ) and conn.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema"
                    " = 'app' AND table_name = 'fee_rules' AND column_name = 'created_by')"
                )
            )
            if exists:
                for statement in RESET_PRICING:
                    conn.execute(text(statement))
    finally:
        engine.dispose()


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
    reset_pricing_sync(test_settings.migration_database_url)
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
        for table in CASCADED_APPEND_ONLY:
            await session.execute(
                text(f"ALTER TABLE app.{table} DISABLE TRIGGER {table}_no_truncate")
            )
        tables = ", ".join(f"app.{t}" for t in MUTABLE_TABLES)
        await session.execute(text(f"TRUNCATE {tables} CASCADE"))
        for statement in RESET_PRICING:
            await session.execute(text(statement))
        for table in CASCADED_APPEND_ONLY:
            await session.execute(
                text(f"ALTER TABLE app.{table} ENABLE TRIGGER {table}_no_truncate")
            )


@pytest.fixture(scope="session")
def sim_migrated(test_settings: IntegrationSettings) -> str:
    assert test_settings.sim_database_url
    config = Config()
    config.set_main_option("script_location", str(BACKEND_DIR / "sim" / "migrations"))
    config.set_main_option("sqlalchemy.url", test_settings.sim_database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return test_settings.sim_database_url


@pytest.fixture(scope="session")
async def sim_db(sim_migrated: str) -> AsyncIterator[SimDatabase]:
    database = SimDatabase(sim_migrated)
    yield database
    await database.dispose()


@pytest.fixture(autouse=True)
async def clean_sim_tables(sim_db: SimDatabase) -> None:
    async with sim_db.transaction() as session:
        tables = ", ".join(f"simulator.{t}" for t in SIM_TABLES)
        await session.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def sim_settings(sim_migrated: str) -> SimSettings:
    return SimSettings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        database_url=sim_migrated,
        api_key=SIM_API_KEY,
        webhook_secret=SIM_WEBHOOK_SECRET,
        admin_key=SIM_ADMIN_KEY,
        public_base_url=SIM_BASE,
        allowed_return_origins=["http://127.0.0.1:8080"],
        payout_delay_seconds=0,
        pending_delay_seconds=120,
        slow_response_seconds=0,
        return_delay_seconds=300,
        followup_callback_seconds=60,
    )


class CallbackRelay:
    """Delivers simulator callbacks to the VieWallet app in-process (set once the app exists)."""

    def __init__(self) -> None:
        self.target: FastAPI | None = None
        self.responses: list[int] = []
        self.drop = False  # simulate a network that loses every callback

    async def __call__(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        assert self.target is not None
        if self.drop:
            raise httpx.ConnectError("callback lost")
        transport = httpx.ASGITransport(app=self.target)
        async with httpx.AsyncClient(transport=transport, base_url="http://viewallet.test") as http:
            path = url.split("://", 1)[-1].split("/", 1)[-1]
            response = await http.post("/" + path, content=body, headers=headers)
        self.responses.append(response.status_code)
        return response.status_code


@pytest.fixture
def relay() -> CallbackRelay:
    return CallbackRelay()


@pytest.fixture
async def sim_app(
    sim_settings: SimSettings, clock: FixedClock, relay: CallbackRelay
) -> AsyncIterator[FastAPI]:
    application = create_sim_app(sim_settings, clock=clock, sender=relay, run_worker=False)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def provider(sim_app: FastAPI, clock: FixedClock) -> AsyncIterator[SimulatorProvider]:
    adapter = SimulatorProvider.build(
        SIM_BASE,
        api_key=SIM_API_KEY,
        webhook_secret=SIM_WEBHOOK_SECRET,
        transport=httpx.ASGITransport(app=sim_app),
        now=lambda: clock.now().timestamp(),
    )
    yield adapter
    await adapter.aclose()


@pytest.fixture
def app_settings(migrated: IntegrationSettings, tmp_path: Path) -> Settings:
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
        simulator_url=SIM_BASE,
        simulator_api_key=SIM_API_KEY,
        simulator_webhook_secret=SIM_WEBHOOK_SECRET,
        provider_callback_base_url="http://viewallet.test",
        kyc_storage_dir=str(tmp_path / "kyc"),
        cookie_secure=False,
        # Cheap Argon2 parameters keep the suite fast; production uses the defaults.
        argon2_time_cost=1,
        argon2_memory_kib=8,
        argon2_parallelism=1,
    )


@pytest.fixture
async def app(
    app_settings: Settings,
    clock: FixedClock,
    provider: SimulatorProvider,
    sim_app: FastAPI,
    relay: CallbackRelay,
) -> AsyncIterator[FastAPI]:
    control = SimulatorControl.build(
        "http://simulator.test",
        admin_key=SIM_ADMIN_KEY,
        transport=httpx.ASGITransport(app=sim_app),
    )
    application = create_app(
        app_settings, clock=clock, providers={SIMULATOR: provider}, sim_control=control
    )
    relay.target = application
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
