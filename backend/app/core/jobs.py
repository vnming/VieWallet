"""Periodic jobs run by the worker.

Each job has a row in ``scheduled_jobs``. A worker runs a job only after it locks that row
(``FOR UPDATE SKIP LOCKED``) and sees ``next_run_at <= now``; it moves ``next_run_at``
forward in the same transaction. Several workers can therefore run the scheduler safely and
each due job runs once per interval.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import structlog
from sqlalchemy import CheckConstraint, String, Text, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import Clock
from app.core.db import Base, Database

logger = structlog.get_logger(__name__)

RUNNING: Final = "RUNNING"
SUCCEEDED: Final = "SUCCEEDED"
FAILED: Final = "FAILED"


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        CheckConstraint(
            f"last_status IS NULL OR last_status IN ('{RUNNING}', '{SUCCEEDED}', '{FAILED}')",
            name="last_status_valid",
        ),
    )

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    next_run_at: Mapped[datetime]
    last_started_at: Mapped[datetime | None]
    last_finished_at: Mapped[datetime | None]
    last_status: Mapped[str | None] = mapped_column(String(20))
    last_error: Mapped[str | None] = mapped_column(Text)


@dataclass(frozen=True, slots=True)
class JobSpec:
    name: str
    interval: timedelta
    run: Callable[[], Awaitable[None]]


class JobScheduler:
    def __init__(self, db: Database, clock: Clock, jobs: Sequence[JobSpec]) -> None:
        names = [j.name for j in jobs]
        if len(names) != len(set(names)):
            raise ValueError("job names must be unique")
        self._db = db
        self._clock = clock
        self._jobs = list(jobs)

    async def _try_start(self, job: JobSpec) -> bool:
        now = self._clock.now()
        async with self._db.transaction() as session:
            await session.execute(
                insert(ScheduledJob)
                .values(name=job.name, next_run_at=now)
                .on_conflict_do_nothing(index_elements=["name"])
            )
            row = await session.scalar(
                select(ScheduledJob)
                .where(ScheduledJob.name == job.name)
                .with_for_update(skip_locked=True)
            )
            if row is None or row.next_run_at > now:
                return False
            row.next_run_at = now + job.interval
            row.last_started_at = now
            row.last_status = RUNNING
            row.last_error = None
        return True

    async def _finish(self, job: JobSpec, error: BaseException | None) -> None:
        async with self._db.transaction() as session:
            await session.execute(
                update(ScheduledJob)
                .where(ScheduledJob.name == job.name)
                .values(
                    last_finished_at=self._clock.now(),
                    last_status=FAILED if error else SUCCEEDED,
                    last_error=(f"{type(error).__name__}: {error}"[:2000] if error else None),
                )
            )

    async def run_due(self) -> list[str]:
        """Run every job that is due now. Returns the names of jobs that ran."""
        ran = []
        for job in self._jobs:
            if not await self._try_start(job):
                continue
            ran.append(job.name)
            try:
                await job.run()
            except Exception as exc:
                logger.exception("scheduled_job_failed", job=job.name)
                await self._finish(job, exc)
            else:
                await self._finish(job, None)
        return ran

    async def run_forever(self, stop: asyncio.Event, *, tick: float = 5.0) -> None:
        while not stop.is_set():
            try:
                await self.run_due()
            except Exception:
                logger.exception("scheduler_error")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=tick)
