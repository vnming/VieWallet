"""Database plumbing for the simulator (deliberately independent of VieWallet's core)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import DateTime, MetaData, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

SIM_SCHEMA = "simulator"


class SimBase(DeclarativeBase):
    metadata = MetaData(
        schema=SIM_SCHEMA,
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        },
    )
    type_annotation_map = {  # noqa: RUF012
        datetime: DateTime(timezone=True),
        uuid.UUID: Uuid(as_uuid=True),
        dict[str, Any]: JSONB,
    }


class SimClock(Protocol):
    def now(self) -> datetime: ...


class SystemSimClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SimDatabase:
    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(
            url, pool_size=5, pool_pre_ping=True, connect_args={"application_name": "simulator"}
        )
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session, session.begin():
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
