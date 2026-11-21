"""Shared FastAPI dependencies for the HTTP composition layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis

from app.core.clock import Clock
from app.core.config import Settings
from app.core.db import Database
from app.core.idempotency import IdempotencyStore


@dataclass(slots=True)
class AppState:
    settings: Settings
    db: Database
    redis: Redis
    clock: Clock
    idempotency: IdempotencyStore
    expected_migration_head: str | None


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.vw
    return state


StateDep = Annotated[AppState, Depends(get_state)]
