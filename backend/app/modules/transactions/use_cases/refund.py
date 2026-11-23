"""REFUND of a PAYMENT by its merchant (financial-processing.md §5.6, user-flows §5).

Owner decision (Phase 5): no approval threshold yet; a refund completes immediately when the
merchant wallet can cover it. Lock order: original payment row (serialises concurrent refunds,
FT-21) -> both wallet rows -> balances. ``Σ refunds ≤ amount`` is checked on the locked row and
backed by ``CHECK (refunded_amount_minor <= amount_minor)``.

Posting: Dr merchant wallet R; Cr customer wallet R (the merchant fee, 0 by default, is not
returned).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, register_titles
from app.core.money import Money
from app.modules.ledger.public import (
    InsufficientBalanceError,
    JournalDraft,
    JournalKind,
    credit,
    debit,
    post,
)
from app.modules.payments.public import merchant_of
from app.modules.transactions import service
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.transactions.service import Actor, TxnInvalidStateError
from app.modules.transactions.use_cases.common import FinanceContext
from app.modules.transactions.use_cases.spend import fail, inactive_code
from app.modules.transactions.use_cases.transfer import clean_note
from app.modules.wallet.public import WalletStatus, lock_wallets

register_titles(
    {
        "REFUND_EXCEEDS_REMAINING": {
            "vi": "Số tiền hoàn vượt quá phần còn hoàn được",
            "en": "The refund is larger than what remains refundable",
        },
    }
)

REFUNDABLE = (TxnStatus.COMPLETED.value, TxnStatus.PARTIALLY_REFUNDED.value)


class RefundExceedsRemainingError(AppError):
    code = "REFUND_EXCEEDS_REMAINING"
    status = 422


async def merchant_payment(
    session: AsyncSession, merchant_user_id: uuid.UUID, reference: str
) -> Transaction:
    merchant = await merchant_of(session, merchant_user_id)
    txn = await service.get_by_reference(session, reference)
    if txn.type != TxnType.PAYMENT.value or txn.destination_wallet_id != merchant.wallet_id:
        raise service.TransactionNotFoundError()  # another merchant's payment: 404
    return txn


async def refund(
    session: AsyncSession,
    ctx: FinanceContext,
    *,
    merchant_user_id: uuid.UUID,
    reference: str,
    amount: Money,
    reason: str,
    key: str,
) -> Transaction:
    clock = ctx.clock
    found = await merchant_payment(session, merchant_user_id, reference)
    original = await service.lock(session, found.id)
    if original.status not in REFUNDABLE:
        raise TxnInvalidStateError("only completed payments can be refunded")
    if amount.currency != original.currency:
        raise ValueError("refund currency must match the payment")
    remaining = original.amount_minor - original.refunded_amount_minor
    if amount.amount_minor > remaining:
        raise RefundExceedsRemainingError(f"remaining={remaining}")
    assert original.source_wallet_id is not None  # noqa: S101 - CHECK wallets_match_type
    assert original.destination_wallet_id is not None  # noqa: S101
    actor = Actor("USER", merchant_user_id)
    note = clean_note(reason)
    txn = await service.create(
        session,
        clock,
        txn_type=TxnType.REFUND,
        amount=amount,
        initiator_user_id=merchant_user_id,
        actor=actor,
        source_wallet_id=original.destination_wallet_id,
        destination_wallet_id=original.source_wallet_id,
        description=f"Hoàn tiền: {note}" if note else "Hoàn tiền",
        idempotency_key=key,
        related_transaction_id=original.id,
        # What the merchant sees (the customer) and what the customer sees (the merchant).
        counterparty_snapshot=original.extra.get("source_party"),
        extra={"source_party": original.counterparty_snapshot, "reason": note},
    )
    wallets = await lock_wallets(
        session, [original.source_wallet_id, original.destination_wallet_id]
    )
    merchant_wallet = wallets[original.destination_wallet_id]
    customer_wallet = wallets[original.source_wallet_id]
    if merchant_wallet.status != WalletStatus.ACTIVE.value:
        return await fail(session, ctx, txn, inactive_code(merchant_wallet), actor)
    try:
        async with session.begin_nested():
            await post(
                session,
                clock,
                JournalDraft(
                    JournalKind.REFUND,
                    (
                        debit(merchant_wallet.ledger_account_id, amount),
                        credit(customer_wallet.ledger_account_id, amount),
                    ),
                    description=f"refund {txn.reference} of {original.reference}",
                ),
                transaction_id=txn.id,
                created_by=f"user:{merchant_user_id}",
            )
    except InsufficientBalanceError:
        return await fail(session, ctx, txn, "INSUFFICIENT_BALANCE", actor)
    refunded = original.refunded_amount_minor + amount.amount_minor
    await service.transition(
        session,
        clock,
        original,
        TxnStatus.REFUNDED if refunded == original.amount_minor else TxnStatus.PARTIALLY_REFUNDED,
        actor=actor,
        reason_code="REFUND",
        message=txn.reference,
        changes={"refunded_amount_minor": refunded},
    )
    return await service.transition(
        session,
        clock,
        txn,
        TxnStatus.COMPLETED,
        actor=actor,
        reason_code="REFUNDED",
        changes={"confirmed_at": clock.now()},
    )
