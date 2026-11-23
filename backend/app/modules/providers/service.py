"""Provider operations and the callback inbox."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.db import Database
from app.core.ids import new_id
from app.core.outbox import publish
from app.modules.providers.models import (
    OPEN_STATUSES,
    Provider,
    ProviderCallback,
    ProviderCallbackResult,
    ProviderOperation,
)
from app.modules.providers.port import InvalidCallback, PaymentProvider, ProviderEvent

logger = structlog.get_logger(__name__)

CALLBACK_TOPIC = "provider.callback_received"
# Status inquiry back-off for unknown results (financial-processing.md §5.3), then hourly.
INQUIRY_SCHEDULE = (
    timedelta(seconds=30),
    timedelta(minutes=1),
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=15),
)
INQUIRY_HOURLY = timedelta(hours=1)
RECOVERY_AFTER = timedelta(hours=72)
_SAFE_HEADERS = ("content-type", "user-agent", "x-sim-timestamp", "x-sim-event-id")


@dataclass
class ProviderServices:
    db: Database
    clock: Clock
    providers: dict[str, PaymentProvider] = field(default_factory=dict)
    callback_base_url: str = "http://127.0.0.1:8000"

    def get(self, code: str) -> PaymentProvider:
        return self.providers[code]

    def callback_url(self, code: str) -> str:
        return f"{self.callback_base_url.rstrip('/')}/api/v1/webhooks/providers/{code.lower()}"


def next_inquiry_delay(inquiry_count: int) -> timedelta:
    if inquiry_count < len(INQUIRY_SCHEDULE):
        return INQUIRY_SCHEDULE[inquiry_count]
    return INQUIRY_HOURLY


async def create_operation(
    session: AsyncSession,
    clock: Clock,
    *,
    transaction_id: uuid.UUID,
    provider_code: str,
    operation: str,
    amount_minor: int,
    currency: str,
    request_payload: Mapping[str, Any] | None = None,
) -> ProviderOperation:
    now = clock.now()
    op = ProviderOperation(
        id=new_id(),
        transaction_id=transaction_id,
        provider_code=provider_code,
        operation=operation,
        status="CREATED",
        amount_minor=amount_minor,
        currency=currency,
        attempt=0,
        request_payload=dict(request_payload) if request_payload else None,
        created_at=now,
        updated_at=now,
    )
    session.add(op)
    await session.flush()
    return op


async def lock_operation(
    session: AsyncSession, operation_id: uuid.UUID
) -> ProviderOperation | None:
    op: ProviderOperation | None = await session.scalar(
        select(ProviderOperation)
        .where(ProviderOperation.id == operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return op


async def operation_for(
    session: AsyncSession, transaction_id: uuid.UUID, operation: str
) -> ProviderOperation | None:
    op: ProviderOperation | None = await session.scalar(
        select(ProviderOperation).where(
            ProviderOperation.transaction_id == transaction_id,
            ProviderOperation.operation == operation,
        )
    )
    return op


async def claim_due_inquiries(
    db: Database, clock: Clock, *, batch: int = 20, lease: timedelta = timedelta(minutes=2)
) -> list[uuid.UUID]:
    """Pick operations due for a status inquiry and push their next time out by a lease, so
    the HTTP call happens without holding a lock and two workers never ask twice."""
    now = clock.now()
    async with db.transaction() as session:
        rows = (
            await session.scalars(
                select(ProviderOperation)
                .where(
                    ProviderOperation.status.in_(OPEN_STATUSES),
                    ProviderOperation.next_inquiry_at <= now,
                )
                .order_by(ProviderOperation.next_inquiry_at)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            row.next_inquiry_at = now + lease
        return [row.id for row in rows]


# ---------------------------------------------------------------- callback inbox


@dataclass(frozen=True, slots=True)
class InboxResult:
    status: str  # ACCEPTED, DUPLICATE, REJECTED
    callback_id: uuid.UUID
    event: ProviderEvent | None = None


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {k.lower(): v[:200] for k, v in headers.items() if k.lower() in _SAFE_HEADERS}


async def add_result(
    session: AsyncSession,
    clock: Clock,
    callback_id: uuid.UUID,
    result: str,
    detail: str | None = None,
) -> None:
    session.add(
        ProviderCallbackResult(
            id=new_id(),
            callback_id=callback_id,
            result=result,
            detail=detail[:300] if detail else None,
            at=clock.now(),
        )
    )
    await session.flush()


async def receive_callback(
    session: AsyncSession,
    clock: Clock,
    provider: PaymentProvider,
    headers: Mapping[str, str],
    raw_body: bytes,
) -> InboxResult:
    """Verify and durably store a callback; processing happens asynchronously (outbox)."""
    now = clock.now()
    try:
        event = provider.verify_callback(headers, raw_body)
    except InvalidCallback as exc:
        callback_id = new_id()
        session.add(
            ProviderCallback(
                id=callback_id,
                provider_code=provider.code,
                signature_valid=False,
                headers=_safe_headers(headers),
                raw_excerpt=raw_body[:500].decode("utf-8", "replace"),
                received_at=now,
            )
        )
        await session.flush()
        await add_result(session, clock, callback_id, "REJECTED", str(exc))
        logger.warning("provider_callback_rejected", provider=provider.code, reason=str(exc))
        return InboxResult("REJECTED", callback_id)

    inserted = await session.scalar(
        insert(ProviderCallback)
        .values(
            id=new_id(),
            provider_code=provider.code,
            event_id=event.event_id,
            event_type=event.type,
            operation_key=event.operation_key,
            provider_reference=event.provider_reference,
            signature_valid=True,
            headers=_safe_headers(headers),
            body=dict(event.body),
            received_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["provider_code", "event_id"],
            index_where=text("signature_valid"),
        )
        .returning(ProviderCallback.id)
    )
    if inserted is None:
        original = await session.scalar(
            select(ProviderCallback.id).where(
                ProviderCallback.provider_code == provider.code,
                ProviderCallback.event_id == event.event_id,
                ProviderCallback.signature_valid,
            )
        )
        assert original is not None  # noqa: S101 - the conflicting row exists
        await add_result(session, clock, original, "IGNORED_DUPLICATE", "same event id again")
        return InboxResult("DUPLICATE", original, event)
    await publish(
        session,
        clock,
        topic=CALLBACK_TOPIC,
        aggregate_type="provider_callback",
        aggregate_id=str(inserted),
        payload={"callback_id": str(inserted), "provider_code": provider.code},
    )
    return InboxResult("ACCEPTED", inserted, event)


async def load_callback(session: AsyncSession, callback_id: uuid.UUID) -> ProviderCallback | None:
    return await session.get(ProviderCallback, callback_id)


async def mark_status(
    session: AsyncSession,
    clock: Clock,
    op: ProviderOperation,
    status: str,
    *,
    provider_reference: str | None = None,
    error_code: str | None = None,
    response: Mapping[str, Any] | None = None,
    next_inquiry_at: datetime | None = None,
    resolved: bool = False,
) -> None:
    now = clock.now()
    values: dict[str, Any] = {
        "status": status,
        "updated_at": now,
        "next_inquiry_at": next_inquiry_at,
    }
    if provider_reference:
        values["provider_reference"] = provider_reference
    if error_code is not None:
        values["error_code"] = error_code
    if response is not None:
        values["response_payload"] = dict(response)
    if resolved:
        values["resolved_at"] = now
    await session.execute(
        update(ProviderOperation).where(ProviderOperation.id == op.id).values(**values)
    )
    await session.refresh(op)


async def seed_providers(session: AsyncSession) -> None:
    await session.execute(
        insert(Provider)
        .values(
            code="SIMULATOR",
            name="Payment Provider Simulator (SimBank)",
            status="ENABLED",
            capabilities=["CHARGE", "PAYOUT"],
        )
        .on_conflict_do_nothing(index_elements=["code"])
    )
