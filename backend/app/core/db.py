"""Database engine, declarative base and transaction helpers (SQLAlchemy 2 async + psycopg 3)."""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import DateTime, MetaData, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = structlog.get_logger(__name__)

APP_SCHEMA = "app"

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=APP_SCHEMA, naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy configuration attribute
        datetime: DateTime(timezone=True),
        uuid.UUID: Uuid(as_uuid=True),
        dict[str, Any]: JSONB,
    }


class Database:
    """Owns the async engine and session factory for one database URL."""

    def __init__(
        self,
        url: str,
        *,
        pool_size: int = 10,
        max_overflow: int = 5,
        application_name: str = "viewallet-api",
    ) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            connect_args={"application_name": application_name},
        )
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """One unit of work: commits on success, rolls back on any exception."""
        async with self.sessionmaker() as session, session.begin():
            yield session

    async def ping(self) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()


# SQLSTATE codes that mean "the whole transaction may succeed if retried".
RETRYABLE_SQLSTATES = frozenset(
    {
        "40001",  # serialization_failure
        "40P01",  # deadlock_detected
        "55P03",  # lock_not_available (lock_timeout)
    }
)


def sqlstate_of(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None)


def is_retryable_db_error(exc: BaseException) -> bool:
    return isinstance(exc, DBAPIError) and sqlstate_of(exc) in RETRYABLE_SQLSTATES


async def run_in_transaction[T](
    db: Database,
    operation: Callable[[AsyncSession], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.02,
) -> T:
    """Run ``operation`` in a new transaction, retrying the whole unit on retryable DB errors.

    The operation must be safe to re-run from scratch (it is, when it only touches the DB
    inside the given session and is protected by idempotency for client-visible effects).
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for attempt in range(1, attempts + 1):
        try:
            async with db.transaction() as session:
                return await operation(session)
        except DBAPIError as exc:
            if not is_retryable_db_error(exc) or attempt == attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1)) * (1 + random.random())  # noqa: S311
            logger.warning(
                "db_transaction_retry", attempt=attempt, sqlstate=sqlstate_of(exc), delay=delay
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
