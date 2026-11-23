"""Risk holds: money moved to HOLD because the risk engine said REVIEW (§5.3-§5.5, §11).

An analyst with ``risk_hold:decide:any`` approves (the money continues to the receiver, or the
payout is sent) or rejects (the money goes back to the payer). Holds left undecided for the
review SLA (72 hours) are cancelled automatically and released.

Posting rules (§4):

* TRANSFER approve: Dr HOLD A+F; Cr receiver A; Cr FEE_REVENUE F
* TRANSFER reject:  Dr HOLD A+F; Cr sender A+F
* PAYMENT  approve: Dr HOLD A+F; Cr merchant A; Cr FEE_REVENUE F
* PAYMENT  reject:  Dr HOLD A+F; Cr payer A+F (a single-use request becomes payable again)
* WITHDRAWAL approve: create the payout operation and dispatch it (hold stays until settled)
* WITHDRAWAL reject:  WITHDRAWAL_RELEASE
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.audit.public import AuditEntry, record
from app.modules.ledger.public import (
    AccountCode,
    JournalDraft,
    JournalKind,
    PostingLine,
    credit,
    debit,
    post,
    system_account,
)
from app.modules.payments.public import lock_request, release_reservation
from app.modules.providers.public import SIMULATOR, create_operation
from app.modules.transactions import service
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.transactions.service import Actor, TxnInvalidStateError
from app.modules.transactions.use_cases.common import FinanceContext
from app.modules.transactions.use_cases.provider_flow import release_withdrawal, request_dispatch
from app.modules.wallet.public import get_wallet

REVIEW_SLA = timedelta(hours=72)
HELD_TYPES = (TxnType.TRANSFER.value, TxnType.PAYMENT.value, TxnType.WITHDRAWAL.value)

RELEASE_TO_RECEIVER = {
    TxnType.TRANSFER.value: JournalKind.TRANSFER_RELEASE_TO_RECEIVER,
    TxnType.PAYMENT.value: JournalKind.PAYMENT_RELEASE_TO_MERCHANT,
}
RELEASE_TO_PAYER = {
    TxnType.TRANSFER.value: JournalKind.TRANSFER_RELEASE_TO_SENDER,
    TxnType.PAYMENT.value: JournalKind.PAYMENT_RELEASE_TO_PAYER,
}


@dataclass(frozen=True, slots=True)
class Reviewer:
    id: uuid.UUID
    ip: str | None = None
    user_agent: str | None = None


def is_awaiting_review(txn: Transaction) -> bool:
    return (
        txn.type in HELD_TYPES
        and txn.status == TxnStatus.PENDING.value
        and txn.risk_decision == "REVIEW"
        and "review_decision" not in txn.extra
    )


async def list_holds(session: AsyncSession, *, limit: int = 50) -> list[Transaction]:
    rows = await session.scalars(
        select(Transaction)
        .where(
            Transaction.type.in_(HELD_TYPES),
            Transaction.status == TxnStatus.PENDING.value,
            Transaction.risk_decision == "REVIEW",
            ~Transaction.extra.has_key("review_decision"),
        )
        .order_by(Transaction.created_at)
        .limit(limit)
    )
    return list(rows.all())


async def _locked_hold(session: AsyncSession, reference: str) -> Transaction:
    found = await service.get_by_reference(session, reference)
    txn = await service.lock(session, found.id)
    if not is_awaiting_review(txn):
        raise TxnInvalidStateError("not waiting for a risk decision")
    return txn


async def _release(
    session: AsyncSession,
    ctx: FinanceContext,
    txn: Transaction,
    *,
    to_receiver: bool,
    created_by: str,
) -> None:
    assert txn.source_wallet_id is not None  # noqa: S101
    hold = await system_account(session, AccountCode.HOLD, txn.currency)
    total = Money(txn.held_minor, txn.currency)
    lines: tuple[PostingLine, ...]
    if to_receiver:
        assert txn.destination_wallet_id is not None  # noqa: S101
        receiver = await get_wallet(session, txn.destination_wallet_id)
        lines = (
            debit(hold.id, total),
            credit(receiver.ledger_account_id, Money(txn.amount_minor, txn.currency)),
        )
        if txn.fee_minor > 0:
            fees = await system_account(session, AccountCode.FEE_REVENUE, txn.currency)
            lines += (credit(fees.id, Money(txn.fee_minor, txn.currency)),)
        kind = RELEASE_TO_RECEIVER[txn.type]
    else:
        sender = await get_wallet(session, txn.source_wallet_id)
        lines = (debit(hold.id, total), credit(sender.ledger_account_id, total))
        kind = RELEASE_TO_PAYER[txn.type]
    await post(
        session,
        ctx.clock,
        JournalDraft(kind, lines, description=f"release {txn.reference}"),
        transaction_id=txn.id,
        created_by=created_by,
    )


async def _audit(
    session: AsyncSession,
    ctx: FinanceContext,
    txn: Transaction,
    action: str,
    reviewer: Reviewer | None,
    note: str | None,
) -> None:
    await record(
        session,
        ctx.clock,
        AuditEntry(
            action=action,
            actor_type="STAFF" if reviewer else "SYSTEM",
            actor_id=reviewer.id if reviewer else None,
            target_type="transaction",
            target_id=str(txn.id),
            reason=note,
            details={"reference": txn.reference, "type": txn.type, "status": txn.status},
            ip=reviewer.ip if reviewer else None,
            user_agent=reviewer.user_agent if reviewer else None,
        ),
    )


async def approve(
    session: AsyncSession,
    ctx: FinanceContext,
    reference: str,
    *,
    reviewer: Reviewer,
    note: str | None,
) -> Transaction:
    txn = await _locked_hold(session, reference)
    actor = Actor("STAFF", reviewer.id)
    extra = {**txn.extra, "review_decision": "APPROVED", "reviewed_by": str(reviewer.id)}
    if txn.type == TxnType.WITHDRAWAL.value:
        txn.extra = extra
        await session.flush()
        await create_operation(
            session,
            ctx.clock,
            transaction_id=txn.id,
            provider_code=SIMULATOR,
            operation="PAYOUT",
            amount_minor=txn.amount_minor,
            currency=txn.currency,
            request_payload={"destination": txn.extra.get("destination")},
        )
        await request_dispatch(session, ctx.clock, txn.id)
    else:
        await _release(session, ctx, txn, to_receiver=True, created_by=f"staff:{reviewer.id}")
        txn = await service.transition(
            session,
            ctx.clock,
            txn,
            TxnStatus.COMPLETED,
            actor=actor,
            reason_code="REVIEW_APPROVED",
            changes={"held_minor": 0, "extra": extra},
        )
    await _audit(session, ctx, txn, "risk.hold_approved", reviewer, note)
    return txn


async def _cancel(
    session: AsyncSession,
    ctx: FinanceContext,
    txn: Transaction,
    *,
    actor: Actor,
    reason: str,
    created_by: str,
) -> Transaction:
    extra = {**txn.extra, "review_decision": reason}
    if txn.type == TxnType.WITHDRAWAL.value:
        await release_withdrawal(session, ctx.clock, txn, created_by)
    else:
        # Same lock order as confirm: transaction -> payment request -> balances.
        if txn.payment_request_id is not None:
            request = await lock_request(session, ctx.identity.signing_key, txn.payment_request_id)
            await release_reservation(session, ctx.clock, request, txn.id)
        await _release(session, ctx, txn, to_receiver=False, created_by=created_by)
    return await service.transition(
        session,
        ctx.clock,
        txn,
        TxnStatus.CANCELLED,
        actor=actor,
        reason_code=reason,
        changes={"held_minor": 0, "failure_code": reason, "extra": extra},
    )


async def reject(
    session: AsyncSession,
    ctx: FinanceContext,
    reference: str,
    *,
    reviewer: Reviewer,
    note: str,
) -> Transaction:
    txn = await _locked_hold(session, reference)
    txn = await _cancel(
        session,
        ctx,
        txn,
        actor=Actor("STAFF", reviewer.id),
        reason="REVIEW_REJECTED",
        created_by=f"staff:{reviewer.id}",
    )
    await _audit(session, ctx, txn, "risk.hold_rejected", reviewer, note)
    return txn


async def expire_holds(ctx: FinanceContext, *, batch: int = 50) -> int:
    """Worker job: holds without a decision after the review SLA are cancelled (§11)."""
    async with ctx.db.transaction() as session:
        rows = (
            await session.scalars(
                select(Transaction)
                .where(
                    Transaction.type.in_(HELD_TYPES),
                    Transaction.status == TxnStatus.PENDING.value,
                    Transaction.risk_decision == "REVIEW",
                    ~Transaction.extra.has_key("review_decision"),
                    Transaction.confirmed_at <= ctx.clock.now() - REVIEW_SLA,
                )
                .order_by(Transaction.confirmed_at)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for held in rows:
            cancelled = await _cancel(
                session,
                ctx,
                held,
                actor=service.SYSTEM,
                reason="REVIEW_EXPIRED",
                created_by="system",
            )
            await _audit(session, ctx, cancelled, "risk.hold_expired", None, None)
        return len(rows)
