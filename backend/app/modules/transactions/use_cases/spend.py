"""Confirm pipeline shared by TRANSFER and PAYMENT (financial-processing.md §5.4, §5.5, §9).

Runs inside one money transaction after the PIN was verified. Lock order:
transaction row -> payment request row (if any) -> both wallet rows (id order) -> balances
(ledger, id order). Business failures end the transaction in FAILED/EXPIRED and are returned,
not raised, so the outcome commits and the client receives the full transaction.

Posting rules (§4), A = amount, F = payer fee:

* TRANSFER (ALLOW):  Dr sender A+F; Cr receiver A; Cr FEE_REVENUE F
* TRANSFER (REVIEW): Dr sender A+F; Cr HOLD A+F           -> PENDING until an analyst decides
* PAYMENT  (ALLOW):  Dr payer A+F;  Cr merchant A; Cr FEE_REVENUE F (merchant fee is 0)
* PAYMENT  (REVIEW): Dr payer A+F;  Cr HOLD A+F
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.identity.public import User, credentials_changed_at, kyc_tier_of
from app.modules.ledger.public import (
    AccountCode,
    InsufficientBalanceError,
    JournalDraft,
    JournalKind,
    PostingLine,
    credit,
    debit,
    post,
    system_account,
)
from app.modules.payments.public import (
    PaymentRequestAmountError,
    PaymentRequestClosedError,
    PaymentRequestNotForYouError,
    ensure_payable,
    lock_request,
    mark_paid,
)
from app.modules.pricing.public import LimitCode, LimitDirection
from app.modules.risk.public import Decision, RiskContext, assess
from app.modules.transactions import service
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.transactions.service import Actor, TxnInvalidStateError
from app.modules.transactions.use_cases.common import (
    COUNTED_STATUSES,
    FinanceContext,
    check_limits,
    recent_activity,
)
from app.modules.wallet.public import Wallet, WalletStatus, WalletType, lock_wallets, touch_saved

SPEND_TYPES = frozenset({TxnType.TRANSFER.value, TxnType.PAYMENT.value})

DIRECT_KIND = {
    TxnType.TRANSFER.value: JournalKind.TRANSFER,
    TxnType.PAYMENT.value: JournalKind.PAYMENT,
}
HOLD_KIND = {
    TxnType.TRANSFER.value: JournalKind.TRANSFER_HOLD,
    TxnType.PAYMENT.value: JournalKind.PAYMENT_HOLD,
}


def inactive_code(wallet: Wallet) -> str:
    return "WALLET_FROZEN" if wallet.status == WalletStatus.FROZEN.value else "WALLET_NOT_ACTIVE"


async def fail(
    session: AsyncSession,
    ctx: FinanceContext,
    txn: Transaction,
    code: str,
    actor: Actor,
    to: TxnStatus = TxnStatus.FAILED,
) -> Transaction:
    return await service.transition(
        session,
        ctx.clock,
        txn,
        to,
        actor=actor,
        reason_code=code,
        changes={"failure_code": code, "extra": {**txn.extra}},
    )


async def _same_pattern_count(
    session: AsyncSession, txn: Transaction, since_minutes: int = 2
) -> int:
    """Completed/held spends with the same amount to the same wallet a moment ago."""
    count = await session.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(
            Transaction.source_wallet_id == txn.source_wallet_id,
            Transaction.destination_wallet_id == txn.destination_wallet_id,
            Transaction.amount_minor == txn.amount_minor,
            Transaction.status.in_(COUNTED_STATUSES),
            Transaction.id != txn.id,
            Transaction.created_at >= txn.created_at - timedelta(minutes=since_minutes),
        )
    )
    return int(count or 0)


async def _owner_active(session: AsyncSession, wallet: Wallet) -> bool:
    status = await session.scalar(select(User.status).where(User.id == wallet.owner_user_id))
    return status == "ACTIVE"


async def _precheck(
    session: AsyncSession,
    ctx: FinanceContext,
    txn: Transaction,
    sender: Wallet,
    receiver: Wallet,
    user_id: uuid.UUID,
) -> str | None:
    """Wallet states, payer limits and the receiver's balance cap, on locked wallets."""
    if sender.status != WalletStatus.ACTIVE.value:
        return inactive_code(sender)
    if receiver.status != WalletStatus.ACTIVE.value or not await _owner_active(session, receiver):
        # Generic on purpose: the payer learns nothing about the other person's account.
        return "RECIPIENT_UNAVAILABLE"
    now = ctx.clock.now()
    txn_type = TxnType(txn.type)
    amount = Money(txn.amount_minor, txn.currency)
    breach = await check_limits(
        session,
        wallet=sender,
        txn_type=txn_type,
        direction=LimitDirection.OUT,
        amount=amount,
        user_id=user_id,
        kyc_tier=await kyc_tier_of(session, user_id),
        now=now,
        timezone=ctx.settings.business_timezone,
        exclude=txn.id,
    )
    if breach is not None:
        return breach.code.value
    if receiver.wallet_type == WalletType.MAIN.value:  # merchants have no balance cap
        cap = await check_limits(
            session,
            wallet=receiver,
            txn_type=txn_type,
            direction=LimitDirection.IN,
            amount=amount,
            user_id=receiver.owner_user_id,
            kyc_tier=await kyc_tier_of(session, receiver.owner_user_id),
            now=now,
            timezone=ctx.settings.business_timezone,
        )
        if cap is not None and cap.code is LimitCode.BALANCE_CAP:
            return "RECEIVER_LIMIT_EXCEEDED"
    return None


async def _journal(
    session: AsyncSession, txn: Transaction, sender: Wallet, receiver: Wallet, *, review: bool
) -> JournalDraft:
    total = Money(txn.amount_minor + txn.fee_minor, txn.currency)
    lines: tuple[PostingLine, ...]
    if review:
        hold = await system_account(session, AccountCode.HOLD, txn.currency)
        lines = (debit(sender.ledger_account_id, total), credit(hold.id, total))
        kind = HOLD_KIND[txn.type]
    else:
        amount = Money(txn.amount_minor, txn.currency)
        lines = (debit(sender.ledger_account_id, total), credit(receiver.ledger_account_id, amount))
        if txn.fee_minor > 0:
            fees = await system_account(session, AccountCode.FEE_REVENUE, txn.currency)
            lines += (credit(fees.id, Money(txn.fee_minor, txn.currency)),)
        kind = DIRECT_KIND[txn.type]
    return JournalDraft(kind, lines, description=f"{txn.type.lower()} {txn.reference}")


async def confirm_locked(
    session: AsyncSession,
    ctx: FinanceContext,
    *,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID,
    key: str,
) -> Transaction:
    clock = ctx.clock
    now = clock.now()
    actor = Actor("USER", user_id)
    txn = await service.lock(session, transaction_id)
    if txn.initiator_user_id != user_id or txn.type not in SPEND_TYPES:
        raise service.TransactionNotFoundError()
    if txn.status != TxnStatus.CREATED.value:
        if txn.extra.get("confirm_key") == key:
            return txn
        raise TxnInvalidStateError()
    txn.extra = {**txn.extra, "confirm_key": key}
    if txn.quote_expires_at is not None and txn.quote_expires_at <= now:
        return await fail(session, ctx, txn, "QUOTE_EXPIRED", actor, TxnStatus.EXPIRED)

    amount = Money(txn.amount_minor, txn.currency)
    request = None
    if txn.payment_request_id is not None:
        request = await lock_request(session, ctx.identity.signing_key, txn.payment_request_id)
        try:
            ensure_payable(request, payer_id=user_id, amount=amount, now=now)
        except PaymentRequestClosedError, PaymentRequestAmountError, PaymentRequestNotForYouError:
            return await fail(session, ctx, txn, "PAYMENT_REQUEST_CLOSED", actor)

    assert txn.source_wallet_id is not None  # noqa: S101 - CHECK wallets_match_type
    assert txn.destination_wallet_id is not None  # noqa: S101
    wallets = await lock_wallets(session, [txn.source_wallet_id, txn.destination_wallet_id])
    sender, receiver = wallets[txn.source_wallet_id], wallets[txn.destination_wallet_id]
    failure = await _precheck(session, ctx, txn, sender, receiver, user_id)
    if failure is not None:
        return await fail(session, ctx, txn, failure, actor)

    count_10m, amount_1h, failed_30m = await recent_activity(session, sender.id, now)
    risk = await assess(
        session,
        RiskContext(
            user_id=user_id,
            txn_type=txn.type,
            amount_minor=txn.amount_minor,
            currency=txn.currency,
            now=now,
            counterparty_wallet_id=receiver.id,
            outgoing_count_10m=count_10m,
            outgoing_amount_1h_minor=amount_1h,
            failed_count_30m=failed_30m,
            same_amount_same_counterparty_2m=await _same_pattern_count(session, txn),
            credentials_changed_at=await credentials_changed_at(session, user_id),
        ),
        transaction_id=txn.id,
    )
    txn.risk_decision = risk.decision.value
    if risk.decision is Decision.BLOCK:
        return await fail(session, ctx, txn, "RISK_BLOCKED", actor)

    review = risk.decision is Decision.REVIEW
    total = Money(txn.amount_minor + txn.fee_minor, txn.currency)
    draft = await _journal(session, txn, sender, receiver, review=review)
    try:
        async with session.begin_nested():
            await post(
                session,
                clock,
                draft,
                transaction_id=txn.id,
                created_by=f"user:{user_id}",
            )
    except InsufficientBalanceError:
        return await fail(session, ctx, txn, "INSUFFICIENT_BALANCE", actor)

    if request is not None:
        await mark_paid(session, clock, request, txn.id)
    await touch_saved(session, clock, user_id, receiver.id)
    return await service.transition(
        session,
        clock,
        txn,
        TxnStatus.PENDING if review else TxnStatus.COMPLETED,
        actor=actor,
        reason_code="RISK_REVIEW" if review else "CONFIRMED",
        changes={
            "held_minor": total.amount_minor if review else 0,
            "confirmed_at": now,
            "risk_decision": risk.decision.value,
            "extra": txn.extra,
        },
    )
