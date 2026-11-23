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
from redis.asyncio import Redis

from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.crypto import DataCipher
from app.core.db import Database
from app.core.idempotency import IdempotencyStore
from app.core.jobs import JobScheduler, JobSpec
from app.core.logging import configure_logging
from app.core.outbox import HandlerRegistry, OutboxDispatcher, OutboxMessage
from app.core.rate_limit import RateLimiter
from app.main import build_providers
from app.modules.identity.public import IdentityServices
from app.modules.notifications.public import EmailSender, SmtpEmailSender, register_inapp_handlers
from app.modules.notifications.public import register_handlers as register_email_handlers
from app.modules.operations.public import expire_due, register_recovery_handlers, run_internal
from app.modules.payments.public import expire_due as expire_payment_requests
from app.modules.providers.public import PaymentProvider, ProviderServices, SimulatorProvider
from app.modules.transactions.public import (
    FinanceContext,
    resubmit_stalled_deposits,
    review,
    run_inquiries,
    transfer,
    withdrawal,
)
from app.modules.transactions.public import register_handlers as register_transaction_handlers

logger = structlog.get_logger("app.worker")


def build_finance(
    settings: Settings,
    db: Database,
    clock: Clock,
    redis: Redis,
    *,
    providers: dict[str, PaymentProvider] | None = None,
) -> FinanceContext:
    identity = IdentityServices.build(settings, db, clock, RateLimiter(redis, clock))
    provider_map = providers if providers is not None else build_providers(settings)
    return FinanceContext(
        identity,
        ProviderServices(db, clock, provider_map, settings.provider_callback_base_url),
    )


def build_registry(
    settings: Settings,
    finance: FinanceContext,
    *,
    email_sender: EmailSender | None = None,
) -> HandlerRegistry:
    """Event handlers of all modules are registered here as modules are implemented."""
    registry = HandlerRegistry()
    cipher = DataCipher.from_setting(settings.data_encryption_key)
    sender = email_sender or SmtpEmailSender(
        settings.smtp_host, settings.smtp_port, settings.smtp_from
    )
    register_email_handlers(registry, cipher, sender)
    register_inapp_handlers(registry, finance.db, finance.clock)
    register_recovery_handlers(registry, finance.db, finance.clock)
    register_transaction_handlers(registry, finance)

    async def log_event(message: OutboxMessage) -> None:
        # Further consumers (analytics, push) subscribe in later phases.
        logger.info("domain_event", topic=message.topic, aggregate_id=message.aggregate_id)

    registry.register("reconciliation.violation_found", log_event)
    return registry


def build_jobs(
    db: Database, clock: Clock, settings: Settings, finance: FinanceContext
) -> list[JobSpec]:
    store = IdempotencyStore(clock, ttl=timedelta(hours=settings.idempotency_ttl_hours))

    async def purge_idempotency_keys() -> None:
        async with db.transaction() as session:
            removed = await store.purge_expired(session)
        logger.info("idempotency_keys_purged", removed=removed)

    async def reconcile_ledger() -> None:
        async with db.transaction() as session:
            result = await run_internal(session, clock, triggered_by=None)
        logger.info("internal_reconciliation_finished", violations=result.run.violations)

    async def expire_approvals() -> None:
        async with db.transaction() as session:
            expired = await expire_due(session, clock)
        if expired:
            logger.info("approvals_expired", count=expired)

    async def provider_inquiries() -> None:
        asked = await run_inquiries(finance)
        if asked:
            logger.info("provider_inquiries", count=asked)

    async def expire_withdrawal_quotes() -> None:
        await withdrawal.expire_quotes(finance)

    async def resubmit_deposits() -> None:
        await resubmit_stalled_deposits(finance)

    async def expire_spend_quotes() -> None:
        await transfer.expire_quotes(finance)

    async def expire_requests() -> None:
        async with db.transaction() as session:
            await expire_payment_requests(session, clock)

    async def expire_review_holds() -> None:
        cancelled = await review.expire_holds(finance)
        if cancelled:
            logger.info("risk_holds_expired", count=cancelled)

    return [
        JobSpec("purge_expired_idempotency_keys", timedelta(hours=1), purge_idempotency_keys),
        JobSpec("internal_reconciliation", timedelta(hours=1), reconcile_ledger),
        JobSpec("expire_approval_requests", timedelta(minutes=5), expire_approvals),
        JobSpec("provider_status_inquiries", timedelta(seconds=10), provider_inquiries),
        JobSpec("expire_withdrawal_quotes", timedelta(minutes=1), expire_withdrawal_quotes),
        JobSpec("resubmit_stalled_deposits", timedelta(minutes=1), resubmit_deposits),
        JobSpec("expire_spend_quotes", timedelta(minutes=1), expire_spend_quotes),
        JobSpec("expire_payment_requests", timedelta(minutes=5), expire_requests),
        JobSpec("expire_risk_holds", timedelta(minutes=15), expire_review_holds),
    ]


async def run_worker(settings: Settings, stop: asyncio.Event) -> None:
    clock = SystemClock()
    db = Database(settings.database_url, pool_size=5, application_name="viewallet-worker")
    redis = Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
    finance = build_finance(settings, db, clock, redis)
    registry = build_registry(settings, finance)
    dispatcher = OutboxDispatcher(
        db,
        registry,
        clock,
        batch_size=settings.outbox_batch_size,
        max_attempts=settings.outbox_max_attempts,
    )
    scheduler = JobScheduler(db, clock, build_jobs(db, clock, settings, finance))
    logger.info("worker_started", topics=registry.topics)
    try:
        await asyncio.gather(
            dispatcher.run_forever(stop, idle_sleep=settings.worker_idle_sleep_seconds),
            scheduler.run_forever(stop),
        )
    finally:
        for provider in finance.providers.providers.values():
            if isinstance(provider, SimulatorProvider):
                await provider.aclose()
        await redis.aclose()
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
