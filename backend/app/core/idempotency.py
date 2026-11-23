"""Idempotency store for client requests (financial-processing.md §7.1).

Flow for one request:

1. ``claim`` in its own short transaction inserts an ``IN_PROGRESS`` row
   (``INSERT ... ON CONFLICT DO NOTHING``). A conflicting row means a retry:
   same hash + completed -> replay the stored response; same hash + in progress -> 409;
   different hash -> 422. An in-progress row whose lock expired (crashed worker) can be
   re-claimed.
2. The business operation runs in a second transaction and calls ``complete`` in that same
   transaction, so the stored response exists if and only if the business change committed.
3. If the business transaction raises, ``release`` deletes the claim so the client may retry.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Final

from fastapi import Depends, Header, Request
from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint, delete, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import Clock
from app.core.db import Base, Database, is_retryable_db_error
from app.core.errors import (
    IdempotencyInProgressError,
    IdempotencyKeyInvalidError,
    IdempotencyKeyRequiredError,
    IdempotencyKeyReusedError,
)
from app.core.ids import new_id

IN_PROGRESS: Final = "IN_PROGRESS"
RETRY_ATTEMPTS: Final = 3  # deadlock/serialization retries of one unit of work
COMPLETED: Final = "COMPLETED"

_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{8,255}$")


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("principal_id", "scope", "key"),
        CheckConstraint(f"status IN ('{IN_PROGRESS}', '{COMPLETED}')", name="status_valid"),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    principal_id: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    response_status: Mapped[int | None]
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    locked_until: Mapped[datetime]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    expires_at: Mapped[datetime]


def request_fingerprint(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON form of the request payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Claim:
    record_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status_code: int
    body: dict[str, Any]
    resource_id: str | None


@dataclass(frozen=True, slots=True)
class OperationResult:
    status_code: int
    body: dict[str, Any]
    resource_id: str | None = None


@dataclass(frozen=True, slots=True)
class IdempotentOutcome:
    status_code: int
    body: dict[str, Any]
    resource_id: str | None
    replayed: bool


class IdempotencyStore:
    def __init__(
        self,
        clock: Clock,
        *,
        ttl: timedelta = timedelta(hours=24),
        lock_duration: timedelta = timedelta(seconds=60),
    ) -> None:
        self._clock = clock
        self._ttl = ttl
        self._lock = lock_duration

    async def claim(
        self,
        session: AsyncSession,
        *,
        principal_id: str,
        scope: str,
        key: str,
        request_hash: str,
    ) -> Claim | StoredResponse:
        now = self._clock.now()
        inserted = await session.scalar(
            insert(IdempotencyRecord)
            .values(
                id=new_id(),
                principal_id=principal_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
                status=IN_PROGRESS,
                locked_until=now + self._lock,
                created_at=now,
                updated_at=now,
                expires_at=now + self._ttl,
            )
            .on_conflict_do_nothing(index_elements=["principal_id", "scope", "key"])
            .returning(IdempotencyRecord.id)
        )
        if inserted is not None:
            return Claim(inserted)

        existing = await session.scalar(
            select(IdempotencyRecord)
            .where(
                IdempotencyRecord.principal_id == principal_id,
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.key == key,
            )
            .with_for_update()
        )
        if existing is None:  # deleted between our insert and select; caller may retry
            raise IdempotencyInProgressError()

        if existing.expires_at <= now:
            # Expired keys may be reused for a new request.
            await session.execute(
                update(IdempotencyRecord)
                .where(IdempotencyRecord.id == existing.id)
                .values(
                    request_hash=request_hash,
                    status=IN_PROGRESS,
                    response_status=None,
                    response_body=None,
                    resource_id=None,
                    locked_until=now + self._lock,
                    created_at=now,
                    updated_at=now,
                    expires_at=now + self._ttl,
                )
            )
            return Claim(existing.id)

        if existing.request_hash != request_hash:
            raise IdempotencyKeyReusedError()

        if existing.status == COMPLETED:
            assert existing.response_status is not None  # noqa: S101 - set together with status
            return StoredResponse(
                existing.response_status, existing.response_body or {}, existing.resource_id
            )

        if existing.locked_until > now:
            raise IdempotencyInProgressError()

        # Abandoned claim (the process handling it died): take it over.
        await session.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.id == existing.id)
            .values(locked_until=now + self._lock, updated_at=now)
        )
        return Claim(existing.id)

    async def complete(self, session: AsyncSession, claim: Claim, result: OperationResult) -> None:
        now = self._clock.now()
        updated = await session.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.id == claim.record_id, IdempotencyRecord.status == IN_PROGRESS)
            .values(
                status=COMPLETED,
                response_status=result.status_code,
                response_body=result.body,
                resource_id=result.resource_id,
                updated_at=now,
            )
        )
        if updated.rowcount != 1:  # type: ignore[attr-defined]
            # Someone else completed or released this claim; abort the business transaction.
            raise IdempotencyInProgressError()

    async def release(self, session: AsyncSession, claim: Claim) -> None:
        await session.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.id == claim.record_id, IdempotencyRecord.status == IN_PROGRESS
            )
        )

    async def purge_expired(self, session: AsyncSession) -> int:
        result = await session.execute(
            delete(IdempotencyRecord).where(IdempotencyRecord.expires_at <= self._clock.now())
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def execute(
        self,
        db: Database,
        *,
        principal_id: str,
        scope: str,
        key: str,
        payload: Mapping[str, Any],
        operation: Callable[[AsyncSession], Awaitable[OperationResult]],
    ) -> IdempotentOutcome:
        """Run ``operation`` at most once per (principal, scope, key)."""
        return await self.execute_checked(
            db,
            principal_id=principal_id,
            scope=scope,
            key=key,
            payload=payload,
            before=None,
            operation=operation,
        )

    async def execute_checked(
        self,
        db: Database,
        *,
        principal_id: str,
        scope: str,
        key: str,
        payload: Mapping[str, Any],
        before: Callable[[], Awaitable[None]] | None,
        operation: Callable[[AsyncSession], Awaitable[OperationResult]],
    ) -> IdempotentOutcome:
        """Like ``execute``; ``before`` runs after the claim and before the business
        transaction opens (for example a PIN check that commits its own failure counter), so
        one request never holds two pooled connections at once."""
        request_hash = request_fingerprint(payload)
        async with db.transaction() as session:
            claimed = await self.claim(
                session, principal_id=principal_id, scope=scope, key=key, request_hash=request_hash
            )
        if isinstance(claimed, StoredResponse):
            return IdempotentOutcome(
                claimed.status_code, claimed.body, claimed.resource_id, replayed=True
            )
        try:
            if before is not None:
                await before()
            result = await self._run(db, claimed, operation, RETRY_ATTEMPTS)
        except BaseException:
            async with db.transaction() as session:
                await self.release(session, claimed)
            raise
        return IdempotentOutcome(
            result.status_code, result.body, result.resource_id, replayed=False
        )

    async def _run(
        self,
        db: Database,
        claimed: Claim,
        operation: Callable[[AsyncSession], Awaitable[OperationResult]],
        attempts: int,
    ) -> OperationResult:
        """Run the unit of work; a deadlock/serialization failure re-runs it from scratch
        (ADR-0004 §6 step 6) while this request keeps its claim."""
        for attempt in range(1, attempts + 1):
            try:
                async with db.transaction() as session:
                    result = await operation(session)
                    await self.complete(session, claimed, result)
                    return result
            except DBAPIError as exc:
                if not is_retryable_db_error(exc) or attempt == attempts:
                    raise
                await asyncio.sleep(0.02 * (2 ** (attempt - 1)) * (1 + random.random()))  # noqa: S311
        raise AssertionError("unreachable")  # pragma: no cover


def get_idempotency_store(request: Request) -> IdempotencyStore:
    """The process-wide store, set on ``app.state.idempotency`` by the composition root."""
    store: IdempotencyStore = request.app.state.idempotency
    return store


IdempotencyDep = Annotated[IdempotencyStore, Depends(get_idempotency_store)]


def idempotency_key_header(
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="Client-generated unique key per user intent (8-255 chars: A-Z a-z 0-9 _ -).",
    ),
) -> str:
    """FastAPI dependency that requires a well-formed ``Idempotency-Key`` header."""
    if idempotency_key is None:
        raise IdempotencyKeyRequiredError()
    if not _KEY_RE.match(idempotency_key):
        raise IdempotencyKeyInvalidError()
    return idempotency_key


IdempotencyKey = Annotated[str, Depends(idempotency_key_header)]
