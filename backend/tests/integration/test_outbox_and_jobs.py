from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import FixedClock
from app.core.context import correlation_id_var
from app.core.db import Database
from app.core.jobs import FAILED, SUCCEEDED, JobScheduler, JobSpec, ScheduledJob
from app.core.outbox import (
    DEAD,
    DONE,
    PENDING,
    HandlerRegistry,
    OutboxDispatcher,
    OutboxEvent,
    OutboxMessage,
    publish,
    requeue_dead_event,
)


async def publish_events(
    db: Database, clock: FixedClock, n: int, topic: str = "t.happened"
) -> None:
    async with db.transaction() as session:
        for i in range(n):
            await publish(
                session,
                clock,
                topic=topic,
                aggregate_type="test",
                aggregate_id=str(i),
                payload={"i": i},
            )


async def events(db: Database) -> list[OutboxEvent]:
    async with db.transaction() as session:
        return list((await session.scalars(select(OutboxEvent).order_by(OutboxEvent.id))).all())


def dispatcher(
    db: Database, registry: HandlerRegistry, clock: FixedClock, **kwargs: object
) -> OutboxDispatcher:
    return OutboxDispatcher(db, registry, clock, batch_size=10, **kwargs)  # type: ignore[arg-type]


async def test_publish_is_part_of_caller_transaction(db: Database, clock: FixedClock) -> None:
    async def publish_then_fail() -> None:
        async with db.transaction() as session:
            await publish(
                session, clock, topic="t", aggregate_type="a", aggregate_id="1", payload={}
            )
            raise RuntimeError("rollback")

    with pytest.raises(RuntimeError):
        await publish_then_fail()
    assert await events(db) == []


async def test_publish_records_correlation_id(db: Database, clock: FixedClock) -> None:
    token = correlation_id_var.set("corr-123456789")
    try:
        await publish_events(db, clock, 1)
    finally:
        correlation_id_var.reset(token)
    (event,) = await events(db)
    assert event.correlation_id == "corr-123456789"
    assert event.status == PENDING


async def test_dispatch_success_marks_done(db: Database, clock: FixedClock) -> None:
    received: list[OutboxMessage] = []

    async def handler(message: OutboxMessage) -> None:
        received.append(message)

    registry = HandlerRegistry()
    registry.register("t.happened", handler)
    await publish_events(db, clock, 3)
    assert await dispatcher(db, registry, clock).run_once() == 3
    assert [m.payload["i"] for m in received] == [0, 1, 2]
    assert all(e.status == DONE and e.attempts == 1 for e in await events(db))
    assert await dispatcher(db, registry, clock).run_once() == 0


async def test_failure_retries_with_backoff_then_dead_letters(
    db: Database, clock: FixedClock
) -> None:
    attempts: list[int] = []

    async def flaky(message: OutboxMessage) -> None:
        attempts.append(message.attempt)
        raise ValueError("downstream unavailable")

    registry = HandlerRegistry()
    registry.register("t.happened", flaky)
    await publish_events(db, clock, 1)
    worker = dispatcher(db, registry, clock, max_attempts=3, backoff_base=timedelta(seconds=10))

    assert await worker.run_once() == 1
    (event,) = await events(db)
    assert event.status == PENDING
    assert event.next_attempt_at == clock.now() + timedelta(seconds=10)
    assert event.last_error == "ValueError: downstream unavailable"

    assert await worker.run_once() == 0  # not due yet
    clock.advance(timedelta(seconds=10))
    assert await worker.run_once() == 1
    clock.advance(timedelta(seconds=20))
    assert await worker.run_once() == 1

    (event,) = await events(db)
    assert event.status == DEAD
    assert attempts == [1, 2, 3]

    # Admin replay puts it back in the queue.
    async with db.transaction() as session:
        assert await requeue_dead_event(session, clock, event.id) is True
        assert await requeue_dead_event(session, clock, event.id) is False
    (event,) = await events(db)
    assert event.status == PENDING
    assert event.attempts == 0


async def test_event_without_handler_is_marked_done(db: Database, clock: FixedClock) -> None:
    await publish_events(db, clock, 1, topic="nobody.listens")
    assert await dispatcher(db, HandlerRegistry(), clock).run_once() == 1
    (event,) = await events(db)
    assert event.status == DONE


async def test_expired_lease_is_reclaimed(db: Database, clock: FixedClock) -> None:
    """A worker that crashed mid-processing leaves PROCESSING rows; they are picked up again."""
    seen: list[int] = []

    async def handler(message: OutboxMessage) -> None:
        seen.append(message.attempt)

    registry = HandlerRegistry()
    registry.register("t.happened", handler)
    await publish_events(db, clock, 1)
    worker = dispatcher(db, registry, clock, lease=timedelta(minutes=5))
    claimed = await worker._claim_batch()  # simulate crash: claim but never finish
    assert len(claimed) == 1
    assert await worker.run_once() == 0  # still leased
    clock.advance(timedelta(minutes=6))
    assert await worker.run_once() == 1
    assert seen == [2]


async def test_concurrent_dispatchers_never_process_an_event_twice(
    db: Database, clock: FixedClock
) -> None:
    processed: list[str] = []

    async def handler(message: OutboxMessage) -> None:
        await asyncio.sleep(0.01)
        processed.append(message.aggregate_id)

    registry = HandlerRegistry()
    registry.register("t.happened", handler)
    await publish_events(db, clock, 60)
    workers = [dispatcher(db, registry, clock) for _ in range(4)]

    async def drain(worker: OutboxDispatcher) -> None:
        while await worker.run_once():
            pass

    await asyncio.gather(*(drain(w) for w in workers))
    assert sorted(processed, key=int) == [str(i) for i in range(60)]
    assert all(e.status == DONE for e in await events(db))


async def test_run_forever_stops(db: Database, clock: FixedClock) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(
        dispatcher(db, HandlerRegistry(), clock).run_forever(stop, idle_sleep=0.05)
    )
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


class TestScheduler:
    async def test_runs_due_jobs_once_per_interval(self, db: Database, clock: FixedClock) -> None:
        runs: list[str] = []

        async def job() -> None:
            runs.append("purge")

        scheduler = JobScheduler(db, clock, [JobSpec("purge", timedelta(hours=1), job)])
        assert await scheduler.run_due() == ["purge"]
        assert await scheduler.run_due() == []
        clock.advance(timedelta(minutes=59))
        assert await scheduler.run_due() == []
        clock.advance(timedelta(minutes=1))
        assert await scheduler.run_due() == ["purge"]
        assert runs == ["purge", "purge"]
        async with db.transaction() as session:
            row = await session.get(ScheduledJob, "purge")
        assert row is not None
        assert row.last_status == SUCCEEDED

    async def test_failure_is_recorded(self, db: Database, clock: FixedClock) -> None:
        async def broken() -> None:
            raise RuntimeError("nope")

        scheduler = JobScheduler(db, clock, [JobSpec("broken", timedelta(minutes=5), broken)])
        assert await scheduler.run_due() == ["broken"]
        async with db.transaction() as session:
            row = await session.get(ScheduledJob, "broken")
        assert row is not None
        assert row.last_status == FAILED
        assert row.last_error == "RuntimeError: nope"

    async def test_concurrent_schedulers_run_job_once(
        self, db: Database, clock: FixedClock
    ) -> None:
        runs: list[int] = []

        async def job() -> None:
            await asyncio.sleep(0.05)
            runs.append(1)

        spec = JobSpec("shared", timedelta(hours=1), job)
        schedulers = [JobScheduler(db, clock, [spec]) for _ in range(5)]
        await asyncio.gather(*(s.run_due() for s in schedulers))
        assert runs == [1]

    async def test_rejects_duplicate_names(self, db: Database, clock: FixedClock) -> None:
        async def job() -> None:
            return None

        with pytest.raises(ValueError, match="unique"):
            JobScheduler(
                db, clock, [JobSpec("x", timedelta(1), job), JobSpec("x", timedelta(1), job)]
            )
