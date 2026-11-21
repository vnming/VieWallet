"""Idempotency store against real PostgreSQL (FT-02, FT-03, FT-04 at kernel level)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import FixedClock
from app.core.db import Database
from app.core.errors import IdempotencyInProgressError, IdempotencyKeyReusedError
from app.core.idempotency import (
    COMPLETED,
    Claim,
    IdempotencyRecord,
    IdempotencyStore,
    OperationResult,
    StoredResponse,
    request_fingerprint,
)
from app.core.outbox import OutboxEvent, publish

PRINCIPAL = "0193f0c2-8f5e-7c3a-9d1e-2b4f6a8c0e12"
KEY = "key-0001-abcdef"


@pytest.fixture
def store(clock: FixedClock) -> IdempotencyStore:
    return IdempotencyStore(clock, ttl=timedelta(hours=24), lock_duration=timedelta(seconds=60))


def side_effect(
    clock: FixedClock, calls: list[int], status: int = 201
) -> Callable[[AsyncSession], Awaitable[OperationResult]]:
    """An operation with a visible DB side effect (an outbox row), so we can count executions."""

    async def operation(session: AsyncSession) -> OperationResult:
        calls.append(1)
        event_id = await publish(
            session,
            clock,
            topic="test.created",
            aggregate_type="test",
            aggregate_id="1",
            payload={"n": len(calls)},
        )
        return OperationResult(status, {"event_id": str(event_id)}, resource_id=str(event_id))

    return operation


async def count_outbox(db: Database) -> int:
    async with db.transaction() as session:
        return int(await session.scalar(select(func.count()).select_from(OutboxEvent)) or 0)


async def test_first_call_executes_and_stores_response(
    db: Database, store: IdempotencyStore, clock: FixedClock
) -> None:
    calls: list[int] = []
    outcome = await store.execute(
        db,
        principal_id=PRINCIPAL,
        scope="transfers.create",
        key=KEY,
        payload={"amount": "1000"},
        operation=side_effect(clock, calls),
    )
    assert outcome.replayed is False
    assert outcome.status_code == 201
    async with db.transaction() as session:
        record = await session.scalar(select(IdempotencyRecord))
    assert record is not None
    assert record.status == COMPLETED
    assert record.response_body == outcome.body
    assert record.request_hash == request_fingerprint({"amount": "1000"})


async def test_sequential_replays_return_same_response(
    db: Database, store: IdempotencyStore, clock: FixedClock
) -> None:
    calls: list[int] = []
    outcomes = [
        await store.execute(
            db,
            principal_id=PRINCIPAL,
            scope="transfers.create",
            key=KEY,
            payload={"amount": "1000"},
            operation=side_effect(clock, calls),
        )
        for _ in range(5)
    ]
    assert len(calls) == 1
    assert [o.replayed for o in outcomes] == [False, True, True, True, True]
    assert len({str(o.body) for o in outcomes}) == 1
    assert await count_outbox(db) == 1


async def test_same_key_different_payload_is_rejected(
    db: Database, store: IdempotencyStore, clock: FixedClock
) -> None:
    calls: list[int] = []
    kwargs = {"principal_id": PRINCIPAL, "scope": "transfers.create", "key": KEY}
    await store.execute(
        db, payload={"amount": "1000"}, operation=side_effect(clock, calls), **kwargs
    )
    with pytest.raises(IdempotencyKeyReusedError):
        await store.execute(
            db, payload={"amount": "1001"}, operation=side_effect(clock, calls), **kwargs
        )
    assert len(calls) == 1


async def test_keys_are_isolated_by_principal_and_scope(
    db: Database, store: IdempotencyStore, clock: FixedClock
) -> None:
    calls: list[int] = []
    for principal, scope in [(PRINCIPAL, "a"), (PRINCIPAL, "b"), ("other-principal", "a")]:
        outcome = await store.execute(
            db,
            principal_id=principal,
            scope=scope,
            key=KEY,
            payload={},
            operation=side_effect(clock, calls),
        )
        assert outcome.replayed is False
    assert len(calls) == 3


async def test_concurrent_requests_execute_once(
    db: Database, store: IdempotencyStore, clock: FixedClock
) -> None:
    calls: list[int] = []
    gate = asyncio.Event()

    async def slow(session: AsyncSession) -> OperationResult:
        await gate.wait()
        return await side_effect(clock, calls)(session)

    async def attempt() -> str:
        try:
            outcome = await store.execute(
                db,
                principal_id=PRINCIPAL,
                scope="transfers.confirm",
                key=KEY,
                payload={"pin": "redacted"},
                operation=slow,
            )
        except IdempotencyInProgressError:
            return "in_progress"
        return "replayed" if outcome.replayed else "executed"

    tasks = [asyncio.create_task(attempt()) for _ in range(10)]
    await asyncio.sleep(0.5)  # let every attempt reach the claim step
    gate.set()
    results = await asyncio.gather(*tasks)
    assert results.count("executed") == 1
    assert set(results) <= {"executed", "in_progress", "replayed"}
    assert len(calls) == 1
    assert await count_outbox(db) == 1


async def test_failed_operation_releases_key_and_rolls_back(
    db: Database, store: IdempotencyStore, clock: FixedClock
) -> None:
    calls: list[int] = []

    async def boom(session: AsyncSession) -> OperationResult:
        await side_effect(clock, calls)(session)
        raise RuntimeError("business failure")

    kwargs: dict[str, Any] = {
        "principal_id": PRINCIPAL,
        "scope": "s",
        "key": KEY,
        "payload": {"a": 1},
    }
    with pytest.raises(RuntimeError):
        await store.execute(db, operation=boom, **kwargs)
    assert await count_outbox(db) == 0  # business side effect rolled back
    # The key is free again: a retry executes.
    outcome = await store.execute(db, operation=side_effect(clock, calls), **kwargs)
    assert outcome.replayed is False
    assert await count_outbox(db) == 1


async def test_abandoned_claim_can_be_taken_over(
    db: Database, store: IdempotencyStore, clock: FixedClock
) -> None:
    fingerprint = request_fingerprint({})
    kwargs = {"principal_id": PRINCIPAL, "scope": "s", "key": KEY, "request_hash": fingerprint}
    async with db.transaction() as session:
        first = await store.claim(session, **kwargs)
    assert isinstance(first, Claim)
    async with db.transaction() as session:
        with pytest.raises(IdempotencyInProgressError):
            await store.claim(session, **kwargs)
    clock.advance(timedelta(seconds=61))  # the first worker "died"; its lock expired
    async with db.transaction() as session:
        second = await store.claim(session, **kwargs)
    assert isinstance(second, Claim)
    assert second.record_id == first.record_id


async def test_expired_key_can_be_reused_with_new_payload(
    db: Database, store: IdempotencyStore, clock: FixedClock
) -> None:
    calls: list[int] = []
    base = {"principal_id": PRINCIPAL, "scope": "s", "key": KEY}
    await store.execute(db, payload={"v": 1}, operation=side_effect(clock, calls), **base)
    clock.advance(timedelta(hours=24, seconds=1))
    outcome = await store.execute(db, payload={"v": 2}, operation=side_effect(clock, calls), **base)
    assert outcome.replayed is False
    assert len(calls) == 2


async def test_claim_returns_stored_response(
    db: Database, store: IdempotencyStore, clock: FixedClock
) -> None:
    calls: list[int] = []
    base = {"principal_id": PRINCIPAL, "scope": "s", "key": KEY}
    await store.execute(db, payload={}, operation=side_effect(clock, calls, status=202), **base)
    async with db.transaction() as session:
        stored = await store.claim(session, request_hash=request_fingerprint({}), **base)
    assert isinstance(stored, StoredResponse)
    assert stored.status_code == 202


async def test_purge_expired(db: Database, store: IdempotencyStore, clock: FixedClock) -> None:
    calls: list[int] = []
    for i in range(3):
        await store.execute(
            db,
            principal_id=PRINCIPAL,
            scope="s",
            key=f"{KEY}-{i}",
            payload={},
            operation=side_effect(clock, calls),
        )
    clock.advance(timedelta(hours=25))
    async with db.transaction() as session:
        assert await store.purge_expired(session) == 3
        remaining = await session.scalar(text("SELECT count(*) FROM app.idempotency_keys"))
    assert remaining == 0
