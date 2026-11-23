"""Worker side of the money flows: callback processing, payout dispatch, status inquiries."""

from __future__ import annotations

import contextlib
import uuid
from datetime import timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.outbox import HandlerRegistry, OutboxMessage
from app.modules.providers.public import (
    CALLBACK_TOPIC,
    RECOVERY_AFTER,
    OperationNotFound,
    OperationStatus,
    ProviderError,
    ProviderEvent,
    ProviderOperation,
    add_result,
    claim_due_inquiries,
    load_callback,
    lock_operation,
    mark_status,
    next_inquiry_delay,
)
from app.modules.transactions import service
from app.modules.transactions.domain.state_machine import TxnStatus
from app.modules.transactions.use_cases import deposit, withdrawal
from app.modules.transactions.use_cases.common import FinanceContext
from app.modules.transactions.use_cases.provider_flow import (
    DISPATCH_TOPIC,
    PROVIDER,
    apply_outcome,
    request_dispatch,
    request_recovery,
)

logger = structlog.get_logger(__name__)

RECOVERY_RESUBMIT_AFTER = timedelta(minutes=1)


class RetryLater(Exception):  # noqa: N818 - control flow for the outbox dispatcher
    """Raised by a handler so the outbox retries the event with back-off."""


async def process_callback(ctx: FinanceContext, callback_id: uuid.UUID) -> str:
    async with ctx.db.transaction() as session:
        callback = await load_callback(session, callback_id)
        if callback is None or not callback.signature_valid or callback.body is None:
            return "SKIPPED"
        body = callback.body
        operation_id = None
        with contextlib.suppress(ValueError):
            operation_id = uuid.UUID(str(body.get("operation_key")))
        op = await session.get(ProviderOperation, operation_id) if operation_id else None
        if op is None or op.provider_code != callback.provider_code:
            await add_result(session, ctx.clock, callback.id, "ORPHAN", "unknown operation key")
            return "ORPHAN"
        event = ProviderEvent(
            event_id=str(body["event_id"]),
            type=str(body["type"]),
            operation_key=str(body["operation_key"]),
            provider_reference=body.get("provider_reference"),
            amount_minor=int(str(body["amount"])),
            currency=str(body["currency"]),
            failure_code=body.get("failure_code"),
            body=body,
        )
        applied = await apply_outcome(
            session,
            ctx.clock,
            op.transaction_id,
            op.id,
            OperationStatus(
                operation_key=event.operation_key,
                provider_reference=event.provider_reference,
                outcome=event.outcome,
                amount_minor=event.amount_minor,
                currency=event.currency,
                failure_code=event.failure_code,
            ),
        )
        result = "APPLIED" if applied.result == "PENDING" else applied.result
        await add_result(session, ctx.clock, callback.id, result, applied.detail)
        return result


async def run_inquiries(ctx: FinanceContext, *, batch: int = 20) -> int:
    """Ask the provider about operations without a final answer (lost callbacks, timeouts)."""
    ids = await claim_due_inquiries(ctx.db, ctx.clock, batch=batch)
    for operation_id in ids:
        await _inquire(ctx, operation_id)
    return len(ids)


async def _inquire(ctx: FinanceContext, operation_id: uuid.UUID) -> None:
    async with ctx.db.transaction() as session:
        op = await session.get(ProviderOperation, operation_id)
        if op is None:
            return
        provider = ctx.providers.get(op.provider_code)
        transaction_id = op.transaction_id
    try:
        status: OperationStatus | ProviderError = await provider.get_operation(str(operation_id))
    except ProviderError as exc:
        status = exc
    async with ctx.db.transaction() as session:
        txn = await service.lock(session, transaction_id)
        op = await lock_operation(session, operation_id)
        assert op is not None  # noqa: S101
        if op.status not in {"SUBMITTED", "ACCEPTED", "UNKNOWN"}:
            return
        if isinstance(status, OperationNotFound):
            # The provider never received it: nothing exists on their side.
            if op.operation == "PAYOUT":
                await mark_status(session, ctx.clock, op, "CREATED", error_code="NOT_RECEIVED")
                await request_dispatch(session, ctx.clock, transaction_id)  # FT-19
            else:
                await mark_status(
                    session, ctx.clock, op, "FAILED", error_code="NOT_RECEIVED", resolved=True
                )
                if txn.status in {TxnStatus.CREATED.value, TxnStatus.PENDING.value}:
                    await service.transition(
                        session,
                        ctx.clock,
                        txn,
                        TxnStatus.FAILED,
                        actor=PROVIDER,
                        reason_code="PROVIDER_UNAVAILABLE",
                        changes={"failure_code": "PROVIDER_UNAVAILABLE"},
                    )
            return
        if isinstance(status, ProviderError):
            op.inquiry_count += 1
            await _reschedule(ctx, session, op)
            return
        applied = await apply_outcome(session, ctx.clock, transaction_id, operation_id, status)
        await session.refresh(op)
        if applied.result == "PENDING":
            await _flag_if_stuck(ctx, session, op)


async def _reschedule(ctx: FinanceContext, session: AsyncSession, op: ProviderOperation) -> None:
    await mark_status(
        session,
        ctx.clock,
        op,
        op.status if op.status != "SUBMITTED" else "UNKNOWN",
        next_inquiry_at=ctx.clock.now() + next_inquiry_delay(op.inquiry_count),
    )
    await _flag_if_stuck(ctx, session, op)


async def _flag_if_stuck(ctx: FinanceContext, session: AsyncSession, op: ProviderOperation) -> None:
    started = op.submitted_at or op.created_at
    if op.recovery_flagged or ctx.clock.now() - started < RECOVERY_AFTER:
        return
    op.recovery_flagged = True
    await request_recovery(
        session,
        ctx.clock,
        transaction_id=op.transaction_id,
        operation_id=op.id,
        reason="UNRESOLVED_72H",
        detail=f"{op.operation} still {op.status} after 72 hours",
    )


async def resubmit_stalled_deposits(ctx: FinanceContext) -> int:
    """Deposits whose charge was recorded but never sent (crash between the two steps)."""
    async with ctx.db.transaction() as session:
        rows = (
            await session.scalars(
                select(ProviderOperation.transaction_id).where(
                    ProviderOperation.operation == "CHARGE",
                    ProviderOperation.status == "CREATED",
                    ProviderOperation.created_at <= ctx.clock.now() - RECOVERY_RESUBMIT_AFTER,
                )
            )
        ).all()
    for transaction_id in rows:
        await deposit.submit(ctx, transaction_id)
    return len(rows)


def register_handlers(registry: HandlerRegistry, ctx: FinanceContext) -> None:
    async def on_callback(message: OutboxMessage) -> None:
        await process_callback(ctx, uuid.UUID(str(message.payload["callback_id"])))

    async def on_dispatch(message: OutboxMessage) -> None:
        if await withdrawal.dispatch(ctx, uuid.UUID(str(message.payload["transaction_id"]))):
            raise RetryLater("provider unavailable, retrying with the same operation key")

    registry.register(CALLBACK_TOPIC, on_callback)
    registry.register(DISPATCH_TOPIC, on_dispatch)
