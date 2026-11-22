"""Worker process: outbox dispatcher + periodic jobs.

Run with ``python -m app.worker``.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from datetime import timedelta

import structlog

from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.crypto import DataCipher
from app.core.db import Database
from app.core.idempotency import IdempotencyStore
from app.core.jobs import JobScheduler, JobSpec
from app.core.logging import configure_logging
from app.core.outbox import HandlerRegistry, OutboxDispatcher
from app.modules.notifications.public import EmailSender, SmtpEmailSender
from app.modules.notifications.public import register_handlers as register_email_handlers

logger = structlog.get_logger("app.worker")


def build_registry(
    settings: Settings, *, email_sender: EmailSender | None = None
) -> HandlerRegistry:
    """Event handlers of all modules are registered here as modules are implemented."""
    registry = HandlerRegistry()
    cipher = DataCipher.from_setting(settings.data_encryption_key)
    sender = email_sender or SmtpEmailSender(
        settings.smtp_host, settings.smtp_port, settings.smtp_from
    )
    register_email_handlers(registry, cipher, sender)
    return registry


def build_jobs(db: Database, clock: Clock, settings: Settings) -> list[JobSpec]:
    store = IdempotencyStore(clock, ttl=timedelta(hours=settings.idempotency_ttl_hours))

    async def purge_idempotency_keys() -> None:
        async with db.transaction() as session:
            removed = await store.purge_expired(session)
        logger.info("idempotency_keys_purged", removed=removed)

    return [JobSpec("purge_expired_idempotency_keys", timedelta(hours=1), purge_idempotency_keys)]


async def run_worker(settings: Settings, stop: asyncio.Event) -> None:
    clock = SystemClock()
    db = Database(settings.database_url, pool_size=5, application_name="viewallet-worker")
    registry = build_registry(settings)
    dispatcher = OutboxDispatcher(
        db,
        registry,
        clock,
        batch_size=settings.outbox_batch_size,
        max_attempts=settings.outbox_max_attempts,
    )
    scheduler = JobScheduler(db, clock, build_jobs(db, clock, settings))
    logger.info("worker_started", topics=registry.topics)
    try:
        await asyncio.gather(
            dispatcher.run_forever(stop, idle_sleep=settings.worker_idle_sleep_seconds),
            scheduler.run_forever(stop),
        )
    finally:
        await db.dispose()
        logger.info("worker_stopped")


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    stop = asyncio.Event()
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
    await run_worker(settings, stop)


def main() -> None:
    # psycopg's async mode needs a selector event loop on Windows (dev machines).
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main(), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
