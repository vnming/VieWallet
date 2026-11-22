"""ADJUSTMENT: staff-initiated balance correction, always behind maker-checker (§5.7).

``propose`` creates the transaction (CREATED -> PENDING) and is called by the operations
module together with the approval request. ``execute`` runs when a different staff member
approves; ``cancel`` when the request is rejected or expires. Posting rules (§4):

* credit wallet: Debit ADJUSTMENT_EXPENSE, Credit CUSTOMER_WALLET
* debit wallet:  Debit CUSTOMER_WALLET, Credit ADJUSTMENT_EXPENSE
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.money import Money
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
from app.modules.transactions import service
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.transactions.service import Actor
from app.modules.wallet.public import Wallet, get_wallet


class AdjustmentDirection(StrEnum):
    CREDIT = "CREDIT"  # money to the customer
    DEBIT = "DEBIT"  # money from the customer


class AdjustmentReason(StrEnum):
    GOODWILL = "GOODWILL"
    CORRECTION = "CORRECTION"
    RECONCILIATION = "RECONCILIATION"
    DEMO_FUNDING = "DEMO_FUNDING"  # simulated top-up for demos/tests (no real money exists)


DEFAULT_DESCRIPTIONS = {
    AdjustmentReason.GOODWILL: "Điều chỉnh thiện chí",
    AdjustmentReason.CORRECTION: "Điều chỉnh sửa sai",
    AdjustmentReason.RECONCILIATION: "Điều chỉnh sau đối soát",
    AdjustmentReason.DEMO_FUNDING: "Nạp tiền mô phỏng",
}


@dataclass(frozen=True, slots=True)
class AdjustmentRequest:
    wallet: Wallet
    direction: AdjustmentDirection
    amount: Money
    reason: AdjustmentReason
    note: str
    description: str = ""
    ticket_ref: str | None = None


async def propose(
    session: AsyncSession,
    clock: Clock,
    request: AdjustmentRequest,
    *,
    maker_id: uuid.UUID,
    idempotency_key: str,
) -> Transaction:
    wallet = request.wallet
    if request.amount.currency != wallet.currency:
        raise ValueError("adjustment currency must match the wallet")
    to_customer = request.direction is AdjustmentDirection.CREDIT
    actor = Actor("STAFF", maker_id)
    txn = await service.create(
        session,
        clock,
        txn_type=TxnType.ADJUSTMENT,
        amount=request.amount,
        initiator_user_id=maker_id,
        actor=actor,
        source_wallet_id=None if to_customer else wallet.id,
        destination_wallet_id=wallet.id if to_customer else None,
        description=request.description or DEFAULT_DESCRIPTIONS[request.reason],
        idempotency_key=idempotency_key,
        counterparty_snapshot={"kind": "SYSTEM", "name": "VieWallet"},
        extra={
            "direction": request.direction.value,
            "reason_code": request.reason.value,
            "internal_note": request.note,
            "ticket_ref": request.ticket_ref,
        },
    )
    return await service.transition(
        session, clock, txn, TxnStatus.PENDING, actor=actor, reason_code="AWAITING_APPROVAL"
    )


@dataclass(frozen=True, slots=True)
class AdjustmentOutcome:
    transaction: Transaction
    completed: bool
    failure_code: str | None = None


async def execute(
    session: AsyncSession,
    clock: Clock,
    transaction_id: uuid.UUID,
    *,
    checker_id: uuid.UUID,
    approval_request_id: uuid.UUID,
) -> AdjustmentOutcome:
    """Post the journal and complete; cancel instead when the wallet cannot cover a debit.

    Never raises for a business failure, so the caller can commit the failed outcome.
    """
    txn = await service.lock(session, transaction_id)
    if txn.type != TxnType.ADJUSTMENT.value:
        raise service.TxnInvalidStateError("not an adjustment")
    actor = Actor("STAFF", checker_id)
    wallet_id = txn.destination_wallet_id or txn.source_wallet_id
    assert wallet_id is not None  # noqa: S101 - CHECK wallets_match_type
    wallet = await get_wallet(session, wallet_id)
    expense = await system_account(session, AccountCode.ADJUSTMENT_EXPENSE, txn.currency)
    amount = Money(txn.amount_minor, txn.currency)
    if txn.destination_wallet_id is not None:
        lines = (debit(expense.id, amount), credit(wallet.ledger_account_id, amount))
    else:
        lines = (debit(wallet.ledger_account_id, amount), credit(expense.id, amount))
    draft = JournalDraft(JournalKind.ADJUSTMENT, lines, description=f"adjustment {txn.reference}")
    try:
        async with session.begin_nested():
            await post(
                session, clock, draft, transaction_id=txn.id, created_by=f"staff:{checker_id}"
            )
    except InsufficientBalanceError:
        await service.transition(
            session,
            clock,
            txn,
            TxnStatus.CANCELLED,
            actor=actor,
            reason_code="INSUFFICIENT_BALANCE",
            changes={"failure_code": "INSUFFICIENT_BALANCE"},
        )
        return AdjustmentOutcome(txn, completed=False, failure_code="INSUFFICIENT_BALANCE")
    await service.transition(
        session,
        clock,
        txn,
        TxnStatus.COMPLETED,
        actor=actor,
        reason_code="APPROVED",
        message=str(approval_request_id),
        changes={"confirmed_at": clock.now()},
    )
    return AdjustmentOutcome(txn, completed=True)


async def cancel(
    session: AsyncSession,
    clock: Clock,
    transaction_id: uuid.UUID,
    *,
    actor: Actor,
    reason_code: str,
) -> Transaction:
    txn = await service.lock(session, transaction_id)
    return await service.transition(
        session, clock, txn, TxnStatus.CANCELLED, actor=actor, reason_code=reason_code
    )
