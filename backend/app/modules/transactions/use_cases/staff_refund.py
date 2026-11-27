"""REFUND of a PAYMENT proposed by staff (user-flows §9, owner decision Phase 8).

Operations proposes; the REFUND transaction waits in PENDING until Finance approves through
maker-checker. Approval executes the stored amount in the approving DB transaction: lock the
original payment (serialises with merchant refunds, FT-21), re-check what remains refundable,
then lock both wallets and post Dr merchant wallet / Cr customer wallet, as a merchant refund
does. Business failures (merchant wallet not active, not enough balance, nothing left to refund)
end the refund in FAILED and never raise, so the caller commits the outcome.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.money import Money
from app.modules.ledger.public import (
    InsufficientBalanceError,
    JournalDraft,
    JournalKind,
    credit,
    debit,
    post,
)
from app.modules.transactions import service
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.transactions.service import Actor, TxnInvalidStateError
from app.modules.transactions.use_cases.refund import REFUNDABLE, RefundExceedsRemainingError
from app.modules.transactions.use_cases.spend import inactive_code
from app.modules.transactions.use_cases.transfer import clean_note
from app.modules.wallet.public import WalletStatus, lock_wallets


async def pending_refunds(session: AsyncSession, original_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
            Transaction.related_transaction_id == original_id,
            Transaction.type == TxnType.REFUND.value,
            Transaction.status == TxnStatus.PENDING.value,
        )
    )
    return int(total or 0)


async def propose(
    session: AsyncSession,
    clock: Clock,
    *,
    reference: str,
    amount: Money,
    reason: str,
    maker_id: uuid.UUID,
    idempotency_key: str,
) -> tuple[Transaction, Transaction]:
    """Returns (original payment, pending refund). The caller creates the approval request."""
    found = await service.get_by_reference(session, reference)
    if found.type != TxnType.PAYMENT.value:
        raise TxnInvalidStateError("only payments can be refunded")
    original = await service.lock(session, found.id)
    if original.status not in REFUNDABLE:
        raise TxnInvalidStateError("only completed payments can be refunded")
    if amount.currency != original.currency:
        raise TxnInvalidStateError("refund currency must match the payment")
    remaining = (
        original.amount_minor
        - original.refunded_amount_minor
        - await pending_refunds(session, original.id)
    )
    if amount.amount_minor > remaining:
        raise RefundExceedsRemainingError(f"remaining={remaining}")
    actor = Actor("STAFF", maker_id)
    note = clean_note(reason)
    refund = await service.create(
        session,
        clock,
        txn_type=TxnType.REFUND,
        amount=amount,
        initiator_user_id=maker_id,
        actor=actor,
        source_wallet_id=original.destination_wallet_id,
        destination_wallet_id=original.source_wallet_id,
        description="Hoàn tiền",
        idempotency_key=idempotency_key,
        related_transaction_id=original.id,
        counterparty_snapshot=original.extra.get("source_party"),
        extra={
            "subtype": "STAFF",
            "source_party": original.counterparty_snapshot,
            "internal_note": note,
        },
    )
    refund = await service.transition(
        session, clock, refund, TxnStatus.PENDING, actor=actor, reason_code="AWAITING_APPROVAL"
    )
    return original, refund


@dataclass(frozen=True, slots=True)
class RefundOutcome:
    transaction: Transaction
    failure_code: str | None = None


async def execute(
    session: AsyncSession,
    clock: Clock,
    refund_id: uuid.UUID,
    *,
    checker_id: uuid.UUID,
    approval_request_id: uuid.UUID,
) -> RefundOutcome:
    refund = await service.lock(session, refund_id)
    if refund.type != TxnType.REFUND.value or refund.related_transaction_id is None:
        raise TxnInvalidStateError("not a refund")
    original = await service.lock(session, refund.related_transaction_id)
    actor = Actor("STAFF", checker_id)

    async def failed(code: str) -> RefundOutcome:
        txn = await service.transition(
            session,
            clock,
            refund,
            TxnStatus.FAILED,
            actor=actor,
            reason_code=code,
            changes={"failure_code": code},
        )
        return RefundOutcome(txn, code)

    if original.status not in REFUNDABLE:
        return await failed("ORIGINAL_NOT_REFUNDABLE")
    if refund.amount_minor > original.amount_minor - original.refunded_amount_minor:
        return await failed("REFUND_EXCEEDS_REMAINING")
    assert original.source_wallet_id is not None  # noqa: S101 - CHECK wallets_match_type
    assert original.destination_wallet_id is not None  # noqa: S101
    wallets = await lock_wallets(
        session, [original.source_wallet_id, original.destination_wallet_id]
    )
    merchant_wallet = wallets[original.destination_wallet_id]
    customer_wallet = wallets[original.source_wallet_id]
    if merchant_wallet.status != WalletStatus.ACTIVE.value:
        return await failed(inactive_code(merchant_wallet))
    amount = Money(refund.amount_minor, refund.currency)
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
                    description=f"refund {refund.reference} of {original.reference}",
                ),
                transaction_id=refund.id,
                created_by=f"staff:{checker_id}",
            )
    except InsufficientBalanceError:
        return await failed("INSUFFICIENT_BALANCE")
    refunded = original.refunded_amount_minor + refund.amount_minor
    await service.transition(
        session,
        clock,
        original,
        TxnStatus.REFUNDED if refunded == original.amount_minor else TxnStatus.PARTIALLY_REFUNDED,
        actor=actor,
        reason_code="REFUND",
        message=refund.reference,
        changes={"refunded_amount_minor": refunded},
    )
    txn = await service.transition(
        session,
        clock,
        refund,
        TxnStatus.COMPLETED,
        actor=actor,
        reason_code="APPROVED",
        message=str(approval_request_id),
        changes={"confirmed_at": clock.now()},
    )
    return RefundOutcome(txn)


async def cancel(
    session: AsyncSession, clock: Clock, refund_id: uuid.UUID, *, actor: Actor, reason_code: str
) -> Transaction:
    txn = await service.lock(session, refund_id)
    return await service.transition(
        session, clock, txn, TxnStatus.CANCELLED, actor=actor, reason_code=reason_code
    )
