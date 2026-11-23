"""REVERSAL by staff, always behind maker-checker (financial-processing.md §5.7).

``propose`` creates the REVERSAL transaction (CREATED -> PENDING) together with the approval
request (operations module). ``execute`` runs when a second staff member approves: it posts one
journal that undoes the net effect of every journal of the original transaction and marks the
original REVERSED. If the receiver no longer has the money, nothing is posted, the reversal
FAILS and a recovery case is opened (FT-22); balances never go negative.

Reversible: completed TRANSFER, PAYMENT (only before any refund) and DEPOSIT (simulated
chargeback). Withdrawals are reversed only by the provider returning the payout (Phase 4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.money import Money
from app.modules.ledger.public import (
    AlreadyReversedError,
    InsufficientBalanceError,
    journals_of,
    reverse_transaction,
)
from app.modules.transactions import service
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.transactions.service import Actor, TxnInvalidStateError
from app.modules.transactions.use_cases.provider_flow import request_recovery

REVERSIBLE_TYPES = (TxnType.TRANSFER.value, TxnType.PAYMENT.value, TxnType.DEPOSIT.value)
RECOVERY_REASON = "REVERSAL_INSUFFICIENT_FUNDS"


async def propose(
    session: AsyncSession,
    clock: Clock,
    *,
    reference: str,
    maker_id: uuid.UUID,
    reason: str,
    idempotency_key: str,
) -> tuple[Transaction, Transaction]:
    """Returns (original, reversal). The caller creates the approval request."""
    found = await service.get_by_reference(session, reference)
    original = await service.lock(session, found.id)
    if original.type not in REVERSIBLE_TYPES or original.status != TxnStatus.COMPLETED.value:
        raise TxnInvalidStateError("only completed transfers, payments and deposits")
    open_reversal = await session.scalar(
        select(Transaction.id).where(
            Transaction.related_transaction_id == original.id,
            Transaction.type == TxnType.REVERSAL.value,
            Transaction.status.in_((TxnStatus.CREATED.value, TxnStatus.PENDING.value)),
        )
    )
    if open_reversal is not None:
        raise TxnInvalidStateError("a reversal is already waiting for approval")
    actor = Actor("STAFF", maker_id)
    returned_fee = original.fee_minor
    reversal = await service.create(
        session,
        clock,
        txn_type=TxnType.REVERSAL,
        amount=Money(original.amount_minor, original.currency),
        initiator_user_id=maker_id,
        actor=actor,
        # Money leaves the original receiver and goes back to the original payer.
        source_wallet_id=original.destination_wallet_id,
        destination_wallet_id=original.source_wallet_id,
        description=f"Đảo giao dịch {original.reference}",
        idempotency_key=idempotency_key,
        related_transaction_id=original.id,
        counterparty_snapshot=original.extra.get("source_party")
        or {"kind": "SYSTEM", "name": "VieWallet"},
        extra={
            "subtype": "STAFF",
            "reversal_of": original.reference,
            "reversal_of_type": original.type,
            "returned_fee": str(returned_fee),
            "internal_note": reason,
            "source_party": original.counterparty_snapshot,
        },
    )
    reversal = await service.transition(
        session, clock, reversal, TxnStatus.PENDING, actor=actor, reason_code="AWAITING_APPROVAL"
    )
    return original, reversal


@dataclass(frozen=True, slots=True)
class ReversalOutcome:
    transaction: Transaction
    failure_code: str | None = None


async def execute(
    session: AsyncSession,
    clock: Clock,
    reversal_id: uuid.UUID,
    *,
    checker_id: uuid.UUID,
    approval_request_id: uuid.UUID,
) -> ReversalOutcome:
    """Never raises for a business failure, so the caller commits the failed outcome."""
    reversal = await service.lock(session, reversal_id)
    if reversal.type != TxnType.REVERSAL.value or reversal.related_transaction_id is None:
        raise TxnInvalidStateError("not a reversal")
    original = await service.lock(session, reversal.related_transaction_id)
    actor = Actor("STAFF", checker_id)

    async def failed(code: str) -> ReversalOutcome:
        txn = await service.transition(
            session,
            clock,
            reversal,
            TxnStatus.FAILED,
            actor=actor,
            reason_code=code,
            changes={"failure_code": code},
        )
        return ReversalOutcome(txn, code)

    if original.status != TxnStatus.COMPLETED.value:
        return await failed("ORIGINAL_NOT_REVERSIBLE")
    journal_ids = await journals_of(session, original.id)
    try:
        async with session.begin_nested():
            await reverse_transaction(
                session,
                clock,
                journal_ids,
                transaction_id=reversal.id,
                created_by=f"staff:{checker_id}",
            )
    except InsufficientBalanceError:
        await request_recovery(
            session,
            clock,
            transaction_id=original.id,
            operation_id=None,
            reason=RECOVERY_REASON,
            detail=f"reversal {reversal.reference}: the receiver no longer holds the amount",
        )
        return await failed("INSUFFICIENT_BALANCE")
    except AlreadyReversedError:
        return await failed("ALREADY_REVERSED")
    await service.transition(
        session,
        clock,
        original,
        TxnStatus.REVERSED,
        actor=actor,
        reason_code="REVERSED",
        message=reversal.reference,
    )
    txn = await service.transition(
        session,
        clock,
        reversal,
        TxnStatus.COMPLETED,
        actor=actor,
        reason_code="APPROVED",
        message=str(approval_request_id),
        changes={"confirmed_at": clock.now()},
    )
    return ReversalOutcome(txn)


async def cancel(
    session: AsyncSession, clock: Clock, reversal_id: uuid.UUID, *, actor: Actor, reason_code: str
) -> Transaction:
    txn = await service.lock(session, reversal_id)
    return await service.transition(
        session, clock, txn, TxnStatus.CANCELLED, actor=actor, reason_code=reason_code
    )
