"""Liveness/readiness probes and the public system status endpoint."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import AppState, StateDep

logger = structlog.get_logger(__name__)

router = APIRouter()

CheckStatus = Literal["ok", "fail"]
CHECK_TIMEOUT_SECONDS = 2.0


class Checks(BaseModel):
    database: CheckStatus
    migrations: CheckStatus
    redis: CheckStatus


class LiveResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: CheckStatus
    checks: Checks


class SystemStatus(BaseModel):
    status: CheckStatus
    version: str
    environment: str
    server_time: datetime
    checks: Checks


async def _check_database(state: AppState) -> tuple[CheckStatus, CheckStatus]:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            async with state.db.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                version = await conn.scalar(text("SELECT version_num FROM app.alembic_version"))
    except Exception as exc:
        logger.warning("readiness_database_failed", error=type(exc).__name__)
        return "fail", "fail"
    migrations: CheckStatus = (
        "ok" if version is not None and version == state.expected_migration_head else "fail"
    )
    return "ok", migrations


async def _check_redis(state: AppState) -> CheckStatus:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await state.redis.ping()
    except Exception as exc:
        logger.warning("readiness_redis_failed", error=type(exc).__name__)
        return "fail"
    return "ok"


async def _run_checks(state: AppState) -> Checks:
    (database, migrations), redis = await asyncio.gather(
        _check_database(state), _check_redis(state)
    )
    return Checks(database=database, migrations=migrations, redis=redis)


def _overall(checks: Checks) -> CheckStatus:
    return "ok" if all(v == "ok" for v in checks.model_dump().values()) else "fail"


@router.get("/health/live", tags=["health"], response_model=LiveResponse)
async def live() -> LiveResponse:
    return LiveResponse(status="ok")


@router.get(
    "/health/ready",
    tags=["health"],
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse}},
)
async def ready(state: StateDep) -> JSONResponse:
    checks = await _run_checks(state)
    body = ReadyResponse(status=_overall(checks), checks=checks)
    return JSONResponse(body.model_dump(), status_code=200 if body.status == "ok" else 503)


@router.get(
    "/api/v1/system/status",
    tags=["system"],
    response_model=SystemStatus,
    summary="Public system status shown in the app shells",
)
async def system_status(state: StateDep) -> SystemStatus:
    checks = await _run_checks(state)
    return SystemStatus(
        status=_overall(checks),
        version=state.settings.app_version,
        environment=state.settings.environment.value,
        server_time=state.clock.now(),
        checks=checks,
    )
