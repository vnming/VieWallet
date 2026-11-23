"""DEPOSIT (financial-processing.md §5.2).

``start`` is idempotent per (customer, Idempotency-Key) through the transactions table's
unique key: a retry continues the same transaction instead of creating a second one, and its
response is the transaction's current state. The provider call happens between two DB
transactions, never while a lock is held.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import IdempotencyKeyReusedError
from app.core.money import Money
from app.modules.identity.public import kyc_tier_of
from app.modules.pricing.public import LimitDirection
from app.modules.providers.public import (
    SIMULATOR,
    ChargeRequest,
    OperationStatus,
    Outcome,
    ProviderError,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
    create_operation,
    lock_operation,
    mark_status,
    operation_for,
)
from app.modules.transactions import service
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.transactions.service import Actor
from app.modules.transactions.use_cases.common import (
    FinanceContext,
    LimitExceededError,
    check_limits,
    require_step,
)
from app.modules.transactions.use_cases.provider_flow import PROVIDER, apply_outcome
from app.modules.wallet.public import ensure_active, ensure_wallet

METHODS = ("SIMBANK",)
SUBMIT_LEASE_SECONDS = 60


async def _existing(ctx: FinanceContext, user_id: uuid.UUID, key: str) -> Transaction | None:
    async with ctx.db.transaction() as session:
        txn: Transaction | None = await session.scalar(
            select(Transaction).where(
                Transaction.initiator_user_id == user_id, Transaction.idempotency_key == key
            )
        )
        return txn


async def _create(
    ctx: FinanceContext, *, user_id: uuid.UUID, amount: Money, method: str, key: str
) -> Transaction:
    async with ctx.db.transaction() as session:
        wallet = await ensure_wallet(session, ctx.clock, user_id, amount.currency)
        ensure_active(wallet)
        breach = await check_limits(
            session,
            wallet=wallet,
            txn_type=TxnType.DEPOSIT,
            direction=LimitDirection.IN,
            amount=amount,
            user_id=user_id,
            kyc_tier=await kyc_tier_of(session, user_id),
            now=ctx.clock.now(),
            timezone=ctx.settings.business_timezone,
        )
        if breach is not None:
            raise LimitExceededError(breach)
        txn = await service.create(
            session,
            ctx.clock,
            txn_type=TxnType.DEPOSIT,
            amount=amount,
            initiator_user_id=user_id,
            actor=Actor("USER", user_id),
            destination_wallet_id=wallet.id,
            description="Nạp tiền qua SimBank",
            idempotency_key=key,
            counterparty_snapshot={"kind": "PROVIDER", "name": "SimBank"},
            extra={"method": method},
        )
        txn.provider_code = SIMULATOR
        await create_operation(
            session,
            ctx.clock,
            transaction_id=txn.id,
            provider_code=SIMULATOR,
            operation="CHARGE",
            amount_minor=amount.amount_minor,
            currency=amount.currency,
        )
        return txn


async def start(
    ctx: FinanceContext, *, user_id: uuid.UUID, amount: Money, method: str, key: str
) -> Transaction:
    require_step(amount)
    txn = await _existing(ctx, user_id, key)
    if txn is not None and (
        txn.type != TxnType.DEPOSIT.value or txn.amount_minor != amount.amount_minor
    ):
        raise IdempotencyKeyReusedError()
    if txn is None:
        try:
            txn = await _create(ctx, user_id=user_id, amount=amount, method=method, key=key)
        except IntegrityError:  # a concurrent request with the same key won the insert
            txn = await _existing(ctx, user_id, key)
            if txn is None:
                raise
    await submit(ctx, txn.id)
    async with ctx.db.transaction() as session:
        return await service.get(session, txn.id)


async def submit(ctx: FinanceContext, transaction_id: uuid.UUID) -> None:
    """Send the charge once. Safe to call again: only a CREATED operation is sent."""
    now = ctx.clock.now()
    async with ctx.db.transaction() as session:
        txn = await service.lock(session, transaction_id)
        op = await operation_for(session, transaction_id, "CHARGE")
        assert op is not None  # noqa: S101
        op = await lock_operation(session, op.id)
        assert op is not None  # noqa: S101
        if op.status != "CREATED":
            return
        op.attempt += 1
        op.submitted_at = now
        await mark_status(
            session,
            ctx.clock,
            op,
            "SUBMITTED",
            next_inquiry_at=now + timedelta(seconds=SUBMIT_LEASE_SECONDS),
        )
        request = ChargeRequest(
            operation_key=str(op.id),
            amount_minor=txn.amount_minor,
            currency=txn.currency,
            reference=txn.reference,
            method=str(txn.extra.get("method", "SIMBANK")),
            callback_url=ctx.providers.callback_url(SIMULATOR),
            return_url=f"{ctx.settings.web_base_url.rstrip('/')}/deposits/{txn.reference}",
        )
        operation_id = op.id
    provider = ctx.providers.get(SIMULATOR)
    try:
        result: OperationStatus | ProviderError = await provider.create_charge(request)
    except ProviderError as exc:
        result = exc
    async with ctx.db.transaction() as session:
        await record_submission(session, ctx, transaction_id, operation_id, result)


async def record_submission(
    session: AsyncSession,
    ctx: FinanceContext,
    transaction_id: uuid.UUID,
    operation_id: uuid.UUID,
    result: OperationStatus | ProviderError,
) -> None:
    clock = ctx.clock
    txn = await service.lock(session, transaction_id)
    op = await lock_operation(session, operation_id)
    assert op is not None  # noqa: S101
    if op.status != "SUBMITTED":
        return  # a callback or inquiry already moved it on
    if isinstance(result, ProviderTimeout):
        await mark_status(
            session,
            clock,
            op,
            "UNKNOWN",
            error_code="TIMEOUT",
            next_inquiry_at=clock.now() + timedelta(seconds=30),
        )
        await service.transition(
            session, clock, txn, TxnStatus.PENDING, actor=PROVIDER, reason_code="PROVIDER_TIMEOUT"
        )
        return
    if isinstance(result, (ProviderUnavailable, ProviderRejected, ProviderError)):
        code = (
            "PROVIDER_UNAVAILABLE"
            if isinstance(result, ProviderUnavailable)
            else "PROVIDER_REJECTED"
        )
        await mark_status(session, clock, op, "FAILED", error_code=code, resolved=True)
        await service.transition(
            session,
            clock,
            txn,
            TxnStatus.FAILED,
            actor=PROVIDER,
            reason_code=code,
            changes={"failure_code": code},
        )
        return
    if result.outcome is Outcome.PENDING:
        await mark_status(
            session,
            clock,
            op,
            "ACCEPTED",
            provider_reference=result.provider_reference,
            response={"checkout_url": result.checkout_url},
            next_inquiry_at=clock.now()
            + timedelta(minutes=ctx.settings.deposit_inquiry_after_minutes),
        )
        await service.transition(
            session,
            clock,
            txn,
            TxnStatus.PENDING,
            actor=PROVIDER,
            reason_code="AWAITING_CUSTOMER",
            changes={"extra": {**txn.extra, "checkout_url": result.checkout_url}},
        )
        return
    # Final answer straight away (e.g. scenario 111: declined at creation).
    await mark_status(session, clock, op, "ACCEPTED", provider_reference=result.provider_reference)
    await apply_outcome(session, clock, transaction_id, operation_id, result)
