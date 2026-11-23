"""WITHDRAWAL (financial-processing.md §5.3, pipeline §9).

quote (CREATED, fee and total fixed for 10 minutes) -> confirm with PIN (hold: PENDING) ->
dispatch after commit (PROCESSING) -> provider result (COMPLETED or FAILED with release).
Business failures at confirm end the transaction in FAILED and are returned, not raised, so
the client always receives the full transaction.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import run_in_transaction
from app.core.errors import IdempotencyKeyReusedError
from app.core.money import Money
from app.modules.identity.public import (
    RequestMeta,
    credentials_changed_at,
    kyc_tier_of,
    verify_pin,
)
from app.modules.ledger.public import (
    AccountCode,
    InsufficientBalanceError,
    JournalDraft,
    JournalKind,
    credit,
    debit,
    post,
    system_account,
)
from app.modules.pricing.public import LimitDirection, Subject, fee_for
from app.modules.providers.public import (
    SIMULATOR,
    OperationStatus,
    Outcome,
    PayoutRequest,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    create_operation,
    lock_operation,
    mark_status,
    next_inquiry_delay,
    operation_for,
)
from app.modules.risk.public import Decision, RiskContext, assess
from app.modules.transactions import service
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.transactions.service import Actor, TxnInvalidStateError
from app.modules.transactions.use_cases.common import (
    FinanceContext,
    LimitExceededError,
    check_limits,
    recent_activity,
    require_step,
)
from app.modules.transactions.use_cases.provider_flow import (
    PROVIDER,
    apply_outcome,
    fail_withdrawal,
    release_withdrawal,
    request_dispatch,
)
from app.modules.wallet.public import (
    BANKS,
    BankDestination,
    WalletNotActiveError,
    ensure_active,
    ensure_wallet,
    get_destination,
    lock_wallets,
    reveal_account_number,
)

logger = structlog.get_logger(__name__)

MAX_SUBMIT_ATTEMPTS = 3


# ---------------------------------------------------------------- quote


async def quote(
    ctx: FinanceContext,
    *,
    user_id: uuid.UUID,
    destination_id: uuid.UUID,
    amount: Money,
    key: str,
) -> Transaction:
    require_step(amount)
    async with ctx.db.transaction() as session:
        existing = await _by_key(session, user_id, key)
    if existing is not None:
        same = (
            existing.type == TxnType.WITHDRAWAL.value
            and existing.amount_minor == amount.amount_minor
            and existing.bank_destination_id == destination_id
        )
        if not same:
            raise IdempotencyKeyReusedError()
        return existing
    try:
        async with ctx.db.transaction() as session:
            return await _create_quote(session, ctx, user_id, destination_id, amount, key)
    except IntegrityError:
        async with ctx.db.transaction() as session:
            existing = await _by_key(session, user_id, key)
        if existing is None:
            raise
        return existing


async def _by_key(session: AsyncSession, user_id: uuid.UUID, key: str) -> Transaction | None:
    txn: Transaction | None = await session.scalar(
        select(Transaction).where(
            Transaction.initiator_user_id == user_id, Transaction.idempotency_key == key
        )
    )
    return txn


async def _create_quote(
    session: AsyncSession,
    ctx: FinanceContext,
    user_id: uuid.UUID,
    destination_id: uuid.UUID,
    amount: Money,
    key: str,
) -> Transaction:
    now = ctx.clock.now()
    wallet = await ensure_wallet(session, ctx.clock, user_id, amount.currency)
    ensure_active(wallet)
    destination = await get_destination(session, user_id, destination_id)
    tier = await kyc_tier_of(session, user_id)
    breach = await check_limits(
        session,
        wallet=wallet,
        txn_type=TxnType.WITHDRAWAL,
        direction=LimitDirection.OUT,
        amount=amount,
        user_id=user_id,
        kyc_tier=tier,
        now=now,
        timezone=ctx.settings.business_timezone,
    )
    if breach is not None:
        raise LimitExceededError(breach)
    fee = await fee_for(
        session, ctx.clock, txn_type="WITHDRAWAL", amount=amount, subject=Subject(user_id, tier)
    )
    snapshot = _destination_snapshot(destination)
    txn = await service.create(
        session,
        ctx.clock,
        txn_type=TxnType.WITHDRAWAL,
        amount=amount,
        fee=fee.fee,
        initiator_user_id=user_id,
        actor=Actor("USER", user_id),
        source_wallet_id=wallet.id,
        description=f"Rút tiền về {snapshot['name']}",
        idempotency_key=key,
        counterparty_snapshot=snapshot,
        extra={"destination": snapshot, "fee_breakdown": fee.breakdown},
    )
    txn.provider_code = SIMULATOR
    txn.bank_destination_id = destination.id
    txn.fee_rule_id = fee.rule_id
    txn.fee_rule_version = fee.rule_version
    txn.quote_expires_at = now + timedelta(minutes=ctx.settings.withdrawal_quote_minutes)
    await session.flush()
    return txn


def _destination_snapshot(destination: BankDestination) -> dict[str, str]:
    return {
        "kind": "BANK",
        "name": BANKS.get(destination.bank_code, destination.bank_code),
        "bank_code": destination.bank_code,
        "account_masked": destination.account_number_masked,
        "account_name": destination.account_name,
    }


# ---------------------------------------------------------------- confirm


async def _owned(session: AsyncSession, user_id: uuid.UUID, reference: str) -> Transaction:
    txn = await service.get_by_reference(session, reference)
    if txn.initiator_user_id != user_id or txn.type != TxnType.WITHDRAWAL.value:
        raise service.TransactionNotFoundError()
    return txn


async def confirm(
    ctx: FinanceContext,
    *,
    user_id: uuid.UUID,
    reference: str,
    pin: str,
    key: str,
    meta: RequestMeta,
) -> Transaction:
    async with ctx.db.transaction() as session:
        txn = await _owned(session, user_id, reference)
    if txn.status != TxnStatus.CREATED.value:
        if txn.extra.get("confirm_key") == key:
            return txn  # replay of the confirm that already ran
        raise TxnInvalidStateError()
    # Wrong PIN keeps the quote in CREATED; the failure counter commits on its own (§5.4).
    await verify_pin(ctx.identity, user_id=user_id, pin=pin, meta=meta)

    async def money_unit(session: AsyncSession) -> Transaction:
        return await _confirm_locked(session, ctx, user_id, txn.id, key)

    return await run_in_transaction(ctx.db, money_unit)


async def _fail(
    session: AsyncSession, ctx: FinanceContext, txn: Transaction, code: str, user_id: uuid.UUID
) -> Transaction:
    return await service.transition(
        session,
        ctx.clock,
        txn,
        TxnStatus.FAILED,
        actor=Actor("USER", user_id),
        reason_code=code,
        changes={"failure_code": code, "extra": {**txn.extra}},
    )


async def _confirm_locked(
    session: AsyncSession,
    ctx: FinanceContext,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID,
    key: str,
) -> Transaction:
    clock = ctx.clock
    now = clock.now()
    txn = await service.lock(session, transaction_id)
    if txn.status != TxnStatus.CREATED.value:
        if txn.extra.get("confirm_key") == key:
            return txn
        raise TxnInvalidStateError()
    txn.extra = {**txn.extra, "confirm_key": key}
    if txn.quote_expires_at is not None and txn.quote_expires_at <= now:
        return await service.transition(
            session,
            clock,
            txn,
            TxnStatus.EXPIRED,
            actor=Actor("USER", user_id),
            reason_code="QUOTE_EXPIRED",
            changes={"extra": txn.extra},
        )
    assert txn.source_wallet_id is not None  # noqa: S101
    wallet = (await lock_wallets(session, [txn.source_wallet_id]))[txn.source_wallet_id]
    try:
        ensure_active(wallet)
    except WalletNotActiveError:
        return await _fail(session, ctx, txn, "WALLET_NOT_ACTIVE", user_id)
    destination = await session.get(BankDestination, txn.bank_destination_id)
    if destination is None or destination.status != "ACTIVE":
        return await _fail(session, ctx, txn, "DESTINATION_REMOVED", user_id)

    amount = Money(txn.amount_minor, txn.currency)
    tier = await kyc_tier_of(session, user_id)
    breach = await check_limits(
        session,
        wallet=wallet,
        txn_type=TxnType.WITHDRAWAL,
        direction=LimitDirection.OUT,
        amount=amount,
        user_id=user_id,
        kyc_tier=tier,
        now=now,
        timezone=ctx.settings.business_timezone,
        exclude=txn.id,
    )
    if breach is not None:
        return await _fail(session, ctx, txn, breach.code.value, user_id)

    count_10m, amount_1h, failed_30m = await recent_activity(session, wallet.id, now)
    risk = await assess(
        session,
        RiskContext(
            user_id=user_id,
            txn_type=TxnType.WITHDRAWAL.value,
            amount_minor=txn.amount_minor,
            currency=txn.currency,
            now=now,
            outgoing_count_10m=count_10m,
            outgoing_amount_1h_minor=amount_1h,
            failed_count_30m=failed_30m,
            credentials_changed_at=await credentials_changed_at(session, user_id),
            destination_added_at=destination.created_at,
            destination_cooldown=timedelta(minutes=ctx.settings.new_destination_cooldown_minutes),
        ),
        transaction_id=txn.id,
    )
    if risk.decision is Decision.BLOCK:
        txn.risk_decision = risk.decision.value
        return await _fail(session, ctx, txn, "RISK_BLOCKED", user_id)

    total = Money(txn.amount_minor + txn.fee_minor, txn.currency)
    hold = await system_account(session, AccountCode.HOLD, txn.currency)
    try:
        async with session.begin_nested():
            await post(
                session,
                clock,
                JournalDraft(
                    JournalKind.WITHDRAWAL_HOLD,
                    (debit(wallet.ledger_account_id, total), credit(hold.id, total)),
                    description=f"hold {txn.reference}",
                ),
                transaction_id=txn.id,
                created_by=f"user:{user_id}",
            )
    except InsufficientBalanceError:
        return await _fail(session, ctx, txn, "INSUFFICIENT_BALANCE", user_id)

    review = risk.decision is Decision.REVIEW
    txn = await service.transition(
        session,
        clock,
        txn,
        TxnStatus.PENDING,
        actor=Actor("USER", user_id),
        reason_code="RISK_REVIEW" if review else "CONFIRMED",
        changes={
            "held_minor": total.amount_minor,
            "confirmed_at": now,
            "risk_decision": risk.decision.value,
            "extra": txn.extra,
        },
    )
    if not review:
        await create_operation(
            session,
            clock,
            transaction_id=txn.id,
            provider_code=SIMULATOR,
            operation="PAYOUT",
            amount_minor=txn.amount_minor,
            currency=txn.currency,
            request_payload={"destination": txn.extra.get("destination")},
        )
        await request_dispatch(session, clock, txn.id)
    return txn


# ---------------------------------------------------------------- cancel


async def cancel(ctx: FinanceContext, *, user_id: uuid.UUID, reference: str) -> Transaction:
    async def unit(session: AsyncSession) -> Transaction:
        txn = await _owned(session, user_id, reference)
        txn = await service.lock(session, txn.id)
        actor = Actor("USER", user_id)
        if txn.status == TxnStatus.CREATED.value:
            return await service.transition(
                session,
                ctx.clock,
                txn,
                TxnStatus.CANCELLED,
                actor=actor,
                reason_code="USER_CANCELLED",
            )
        if txn.status != TxnStatus.PENDING.value:
            raise TxnInvalidStateError()
        op = await operation_for(session, txn.id, "PAYOUT")
        if op is not None:
            op = await lock_operation(session, op.id)
            assert op is not None  # noqa: S101
            if op.status != "CREATED":
                raise TxnInvalidStateError("already sent to the bank")
            await mark_status(
                session, ctx.clock, op, "FAILED", error_code="CANCELLED", resolved=True
            )
        await release_withdrawal(session, ctx.clock, txn, f"user:{user_id}")
        return await service.transition(
            session,
            ctx.clock,
            txn,
            TxnStatus.CANCELLED,
            actor=actor,
            reason_code="USER_CANCELLED",
            changes={"held_minor": 0},
        )

    return await run_in_transaction(ctx.db, unit)


# ---------------------------------------------------------------- dispatch (worker)


async def dispatch(ctx: FinanceContext, transaction_id: uuid.UUID) -> bool:
    """Send the payout. Returns True when the caller should retry later (provider down)."""
    now = ctx.clock.now()
    async with ctx.db.transaction() as session:
        txn = await service.lock(session, transaction_id)
        op = await operation_for(session, transaction_id, "PAYOUT")
        if op is None or txn.status != TxnStatus.PENDING.value:
            return False
        op = await lock_operation(session, op.id)
        assert op is not None  # noqa: S101
        if op.status != "CREATED":
            return False  # already sent (duplicate event) or cancelled
        destination = await session.get(BankDestination, txn.bank_destination_id)
        assert destination is not None  # noqa: S101 - FK
        op.attempt += 1
        op.submitted_at = now
        # If this process dies during the call, the inquiry job finds the operation.
        await mark_status(
            session, ctx.clock, op, "SUBMITTED", next_inquiry_at=now + timedelta(minutes=1)
        )
        request = PayoutRequest(
            operation_key=str(op.id),
            amount_minor=txn.amount_minor,
            currency=txn.currency,
            reference=txn.reference,
            callback_url=ctx.providers.callback_url(SIMULATOR),
            bank_code=destination.bank_code,
            account_number=reveal_account_number(ctx.identity.cipher, destination),
            account_name=destination.account_name,
        )
        operation_id, attempt = op.id, op.attempt
    try:
        result: OperationStatus | ProviderError = await ctx.providers.get(SIMULATOR).create_payout(
            request
        )
    except ProviderError as exc:
        result = exc
    async with ctx.db.transaction() as session:
        return await record_payout_submission(
            session, ctx, transaction_id, operation_id, attempt, result
        )


async def record_payout_submission(
    session: AsyncSession,
    ctx: FinanceContext,
    transaction_id: uuid.UUID,
    operation_id: uuid.UUID,
    attempt: int,
    result: OperationStatus | ProviderError,
) -> bool:
    clock = ctx.clock
    txn = await service.lock(session, transaction_id)
    op = await lock_operation(session, operation_id)
    assert op is not None  # noqa: S101
    if op.status != "SUBMITTED":
        return False
    if isinstance(result, ProviderUnavailable):
        if attempt < MAX_SUBMIT_ATTEMPTS:
            await mark_status(session, clock, op, "CREATED", error_code="PROVIDER_UNAVAILABLE")
            return True  # retried with the same operation key (§5.3)
        await mark_status(
            session, clock, op, "FAILED", error_code="PROVIDER_UNAVAILABLE", resolved=True
        )
        await fail_withdrawal(session, clock, txn, failure_code="PROVIDER_UNAVAILABLE")
        return False
    if isinstance(result, ProviderTimeout):
        await mark_status(
            session,
            clock,
            op,
            "UNKNOWN",
            error_code="TIMEOUT",
            next_inquiry_at=clock.now() + next_inquiry_delay(0),
        )
        await service.transition(
            session,
            clock,
            txn,
            TxnStatus.PROCESSING,
            actor=PROVIDER,
            reason_code="PROVIDER_TIMEOUT",
        )
        return False
    if isinstance(result, ProviderError):  # rejected request: a bug or bad data, never money
        logger.error("payout_rejected", transaction_id=str(txn.id), error=str(result))
        await mark_status(
            session, clock, op, "FAILED", error_code="PROVIDER_REJECTED", resolved=True
        )
        await fail_withdrawal(session, clock, txn, failure_code="PROVIDER_REJECTED")
        return False
    await mark_status(
        session,
        clock,
        op,
        "ACCEPTED",
        provider_reference=result.provider_reference,
        next_inquiry_at=clock.now() + timedelta(minutes=10),  # fallback if no callback comes
    )
    if result.outcome is Outcome.PENDING:
        await service.transition(
            session, clock, txn, TxnStatus.PROCESSING, actor=PROVIDER, reason_code="SENT_TO_BANK"
        )
        return False
    await apply_outcome(session, clock, transaction_id, operation_id, result)
    return False


async def expire_quotes(ctx: FinanceContext, *, batch: int = 100) -> int:
    """Worker job: CREATED withdrawals whose quote ran out become EXPIRED."""
    async with ctx.db.transaction() as session:
        rows = (
            await session.scalars(
                select(Transaction)
                .where(
                    Transaction.type == TxnType.WITHDRAWAL.value,
                    Transaction.status == TxnStatus.CREATED.value,
                    Transaction.quote_expires_at <= ctx.clock.now(),
                )
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for txn in rows:
            await service.transition(
                session,
                ctx.clock,
                txn,
                TxnStatus.EXPIRED,
                actor=service.SYSTEM,
                reason_code="QUOTE_EXPIRED",
            )
        return len(rows)
