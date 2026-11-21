"""Transactional outbox (ADR-0006).

Business code calls :func:`publish` inside its own transaction. The worker's
:class:`OutboxDispatcher` claims due events with ``FOR UPDATE SKIP LOCKED``, runs the
registered handlers, and marks events DONE, schedules a retry with exponential backoff, or
moves them to DEAD after ``max_attempts``. Delivery is at-least-once, so handlers must be
idempotent (use ``OutboxMessage.id`` as the dedup key).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

import structlog
from sqlalchemy import CheckConstraint, Index, String, Text, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import Clock
from app.core.context import get_correlation_id
from app.core.db import Base, Database
from app.core.ids import new_id

logger = structlog.get_logger(__name__)

PENDING: Final = "PENDING"
PROCESSING: Final = "PROCESSING"
DONE: Final = "DONE"
DEAD: Final = "DEAD"

MAX_ERROR_LENGTH = 2000


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            f"status IN ('{PENDING}', '{PROCESSING}', '{DONE}', '{DEAD}')", name="status_valid"
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index(
            "ix_outbox_events_due",
            "next_attempt_at",
            postgresql_where=text(f"status IN ('{PENDING}', '{PROCESSING}')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    topic: Mapped[str] = mapped_column(String(100))
    aggregate_type: Mapped[str] = mapped_column(String(50))
    aggregate_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default=PENDING)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime]
    locked_until: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime]
    processed_at: Mapped[datetime | None]


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    id: uuid.UUID
    topic: str
    aggregate_type: str
    aggregate_id: str
    payload: Mapping[str, Any]
    attempt: int
    correlation_id: str | None


Handler = Callable[[OutboxMessage], Awaitable[None]]


async def publish(
    session: AsyncSession,
    clock: Clock,
    *,
    topic: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: Mapping[str, Any],
) -> uuid.UUID:
    """Add an event to the outbox as part of the caller's transaction."""
    now = clock.now()
    event = OutboxEvent(
        id=new_id(),
        topic=topic,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=dict(payload),
        status=PENDING,
        attempts=0,
        next_attempt_at=now,
        correlation_id=get_correlation_id(),
        created_at=now,
    )
    session.add(event)
    await session.flush()
    return event.id


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def register(self, topic: str, handler: Handler) -> None:
        self._handlers[topic].append(handler)

    def handlers_for(self, topic: str) -> list[Handler]:
        return list(self._handlers.get(topic, ()))

    @property
    def topics(self) -> list[str]:
        return sorted(self._handlers)


def retry_delay(attempt: int, *, base: timedelta, cap: timedelta) -> timedelta:
    """Exponential backoff: base * 2^(attempt-1), capped. ``attempt`` starts at 1."""
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    # Bound the exponent so the multiplication never overflows timedelta.
    exponent = min(attempt - 1, 30)
    return min(base * (1 << exponent), cap)


class OutboxDispatcher:
    def __init__(
        self,
        db: Database,
        registry: HandlerRegistry,
        clock: Clock,
        *,
        batch_size: int = 50,
        max_attempts: int = 10,
        lease: timedelta = timedelta(minutes=5),
        backoff_base: timedelta = timedelta(seconds=5),
        backoff_cap: timedelta = timedelta(hours=1),
    ) -> None:
        self._db = db
        self._registry = registry
        self._clock = clock
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._lease = lease
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap

    async def _claim_batch(self) -> list[OutboxMessage]:
        now = self._clock.now()
        async with self._db.transaction() as session:
            rows = (
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        ((OutboxEvent.status == PENDING) & (OutboxEvent.next_attempt_at <= now))
                        | (
                            (OutboxEvent.status == PROCESSING)
                            & (OutboxEvent.locked_until < now)  # lease expired: worker died
                        )
                    )
                    .order_by(OutboxEvent.next_attempt_at, OutboxEvent.id)
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            messages = []
            for row in rows:
                row.status = PROCESSING
                row.attempts += 1
                row.locked_until = now + self._lease
                messages.append(
                    OutboxMessage(
                        id=row.id,
                        topic=row.topic,
                        aggregate_type=row.aggregate_type,
                        aggregate_id=row.aggregate_id,
                        payload=dict(row.payload),
                        attempt=row.attempts,
                        correlation_id=row.correlation_id,
                    )
                )
        return messages

    async def _mark_done(self, message: OutboxMessage) -> None:
        async with self._db.transaction() as session:
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == message.id, OutboxEvent.status == PROCESSING)
                .values(status=DONE, processed_at=self._clock.now(), locked_until=None)
            )

    async def _mark_failed(self, message: OutboxMessage, error: BaseException) -> None:
        now = self._clock.now()
        error_text = f"{type(error).__name__}: {error}"[:MAX_ERROR_LENGTH]
        dead = message.attempt >= self._max_attempts
        values: dict[str, Any] = {"last_error": error_text, "locked_until": None}
        if dead:
            values |= {"status": DEAD, "processed_at": now}
        else:
            delay = retry_delay(message.attempt, base=self._backoff_base, cap=self._backoff_cap)
            values |= {"status": PENDING, "next_attempt_at": now + delay}
        async with self._db.transaction() as session:
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == message.id, OutboxEvent.status == PROCESSING)
                .values(**values)
            )
        log = logger.error if dead else logger.warning
        log(
            "outbox_handler_failed",
            event_id=str(message.id),
            topic=message.topic,
            attempt=message.attempt,
            dead=dead,
            error=error_text,
        )

    async def _process(self, message: OutboxMessage) -> None:
        handlers = self._registry.handlers_for(message.topic)
        if not handlers:
            logger.warning("outbox_no_handler", topic=message.topic, event_id=str(message.id))
        bound = structlog.contextvars.bound_contextvars(
            correlation_id=message.correlation_id, outbox_event_id=str(message.id)
        )
        with bound:
            try:
                for handler in handlers:
                    await handler(message)
            except Exception as exc:
                await self._mark_failed(message, exc)
                return
        await self._mark_done(message)

    async def run_once(self) -> int:
        """Process one batch. Returns the number of events claimed."""
        messages = await self._claim_batch()
        for message in messages:
            await self._process(message)
        return len(messages)

    async def run_forever(self, stop: asyncio.Event, *, idle_sleep: float = 1.0) -> None:
        while not stop.is_set():
            try:
                processed = await self.run_once()
            except Exception:
                logger.exception("outbox_dispatch_error")
                processed = 0
            if processed == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=idle_sleep)


async def requeue_dead_event(session: AsyncSession, clock: Clock, event_id: uuid.UUID) -> bool:
    """Move a DEAD event back to PENDING (admin replay). Returns False if it is not DEAD."""
    result = await session.execute(
        update(OutboxEvent)
        .where(OutboxEvent.id == event_id, OutboxEvent.status == DEAD)
        .values(status=PENDING, attempts=0, next_attempt_at=clock.now(), processed_at=None)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]
