"""FastAPI application factory (HTTP composition root)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api import health
from app.api.deps import AppState
from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import register_exception_handlers
from app.core.idempotency import IdempotencyStore
from app.core.logging import configure_logging
from app.core.middleware import (
    CorrelationIdMiddleware,
    CsrfProtectionMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.rate_limit import RateLimiter
from app.modules.audit import api as audit_api
from app.modules.identity import api as identity_api
from app.modules.identity.context import IdentityServices

BACKEND_DIR = Path(__file__).resolve().parent.parent


def migration_head() -> str | None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


def create_app(settings: Settings | None = None, *, clock: Clock | None = None) -> FastAPI:
    settings = settings or get_settings()
    clock = clock or SystemClock()
    configure_logging(settings.log_level, json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )
        redis = Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
        app.state.vw = AppState(
            settings=settings,
            db=db,
            redis=redis,
            clock=clock,
            idempotency=IdempotencyStore(
                clock,
                ttl=timedelta(hours=settings.idempotency_ttl_hours),
                lock_duration=timedelta(seconds=settings.idempotency_lock_seconds),
            ),
            expected_migration_head=migration_head(),
        )
        app.state.identity = IdentityServices.build(settings, db, clock, RateLimiter(redis, clock))
        try:
            yield
        finally:
            await redis.aclose()
            await db.dispose()

    docs = settings.expose_api_docs
    app = FastAPI(
        title="VieWallet API",
        version=settings.app_version,
        description="Simulated fintech e-wallet. No real money, banks or personal data.",
        openapi_url="/api/v1/openapi.json" if docs else None,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    register_exception_handlers(app)

    # Order: the last added middleware runs first (CSRF runs after CORS handled preflight).
    app.add_middleware(CsrfProtectionMiddleware, trusted_origins=settings.trusted_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Request-ID", "X-VW-CSRF"],
        expose_headers=["X-Request-ID", "Idempotent-Replayed", "Retry-After"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CorrelationIdMiddleware)

    app.include_router(health.router)
    app.include_router(identity_api.auth_router)
    app.include_router(identity_api.me_router)
    app.include_router(identity_api.admin_router)
    app.include_router(audit_api.router)
    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn --factory app.main:app_factory``."""
    return create_app()
