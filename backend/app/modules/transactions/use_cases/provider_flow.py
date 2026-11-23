"""Applying provider results (synchronous answers, callbacks, inquiries) to transactions.

Lock order everywhere: transaction row, then provider operation row, then (inside the
posting engine) wallet balances. Every function runs inside the caller's DB transaction and
performs no I/O; HTTP calls to the provider happen between transactions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.money import Money
from app.core.outbox import publish
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
from app.modules.providers.public import (
    OperationStatus,
    Outcome,
    ProviderOperation,
    lock_operation,
    mark_status,
    next_inquiry_delay,
)
from app.modules.transactions import service
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.transactions.service import Actor
from app.modules.wallet.public import get_wallet

logger = structlog.get_logger(__name__)

PROVIDER = Actor("PROVIDER")
RECOVERY_TOPIC = "transaction.recovery_needed"
DISPATCH_TOPIC = "withdrawal.dispatch_requested"
TERMINAL_OPS = frozenset({"SUCCEEDED", "FAILED", "EXPIRED", "RETURNED"})


@dataclass(frozen=True, slots=True)
class Applied:
    result: str  # APPLIED, IGNORED_STALE, AMOUNT_MISMATCH, STATUS_MISMATCH, PENDING
    detail: str | None = None


async def request_recovery(
    session: AsyncSession,
    clock: Clock,
    *,
    transaction_id: uuid.UUID,
    operation_id: uuid.UUID | None,
    reason: str,
    detail: str,
) -> None:
    logger.error("transaction_recovery_needed", transaction_id=str(transaction_id), reason=reason)
    await publish(
        session,
        clock,
        topic=RECOVERY_TOPIC,
        aggregate_type="transaction",
        aggregate_id=str(transaction_id),
        payload={
            "transaction_id": str(transaction_id),
            "operation_id": str(operation_id) if operation_id else None,
            "reason": reason,
            "detail": detail[:300],
        },
    )


async def request_dispatch(session: AsyncSession, clock: Clock, transaction_id: uuid.UUID) -> None:
    await publish(
        session,
        clock,
        topic=DISPATCH_TOPIC,
        aggregate_type="transaction",
        aggregate_id=str(transaction_id),
        payload={"transaction_id": str(transaction_id)},
    )


# ---------------------------------------------------------------- ledger effects


async def _post(
    session: AsyncSession,
    clock: Clock,
    txn: Transaction,
    kind: JournalKind,
    lines: tuple[PostingLine, ...],
    created_by: str,
) -> None:
    await post(
        session,
        clock,
        JournalDraft(kind, lines, description=f"{kind} {txn.reference}"),
        transaction_id=txn.id,
        created_by=created_by,
    )


async def settle_deposit(session: AsyncSession, clock: Clock, txn: Transaction) -> None:
    assert txn.destination_wallet_id is not None  # noqa: S101 - CHECK wallets_match_type
    wallet = await get_wallet(session, txn.destination_wallet_id)
    settlement = await system_account(
        session, AccountCode.PROVIDER_SETTLEMENT, txn.currency, txn.provider_code
    )
    amount = Money(txn.amount_minor, txn.currency)
    await _post(
        session,
        clock,
        txn,
        JournalKind.DEPOSIT_SETTLE,
        (debit(settlement.id, amount), credit(wallet.ledger_account_id, amount)),
        "provider",
    )


async def settle_withdrawal(session: AsyncSession, clock: Clock, txn: Transaction) -> None:
    hold = await system_account(session, AccountCode.HOLD, txn.currency)
    settlement = await system_account(
        session, AccountCode.PROVIDER_SETTLEMENT, txn.currency, txn.provider_code
    )
    total = Money(txn.amount_minor + txn.fee_minor, txn.currency)
    lines = [debit(hold.id, total), credit(settlement.id, Money(txn.amount_minor, txn.currency))]
    if txn.fee_minor:
        fees = await system_account(session, AccountCode.FEE_REVENUE, txn.currency)
        lines.append(credit(fees.id, Money(txn.fee_minor, txn.currency)))
    await _post(session, clock, txn, JournalKind.WITHDRAWAL_SETTLE, tuple(lines), "provider")


async def release_withdrawal(
    session: AsyncSession, clock: Clock, txn: Transaction, created_by: str
) -> None:
    assert txn.source_wallet_id is not None  # noqa: S101
    wallet = await get_wallet(session, txn.source_wallet_id)
    hold = await system_account(session, AccountCode.HOLD, txn.currency)
    total = Money(txn.held_minor, txn.currency)
    await _post(
        session,
        clock,
        txn,
        JournalKind.WITHDRAWAL_RELEASE,
        (debit(hold.id, total), credit(wallet.ledger_account_id, total)),
        created_by,
    )


async def fail_withdrawal(
    session: AsyncSession,
    clock: Clock,
    txn: Transaction,
    *,
    failure_code: str,
    actor: Actor = PROVIDER,
) -> None:
    """Release the hold and end in FAILED (via PROCESSING when not yet sent, §5.3)."""
    if txn.status == TxnStatus.PENDING.value:
        await service.transition(session, clock, txn, TxnStatus.PROCESSING, actor=actor)
    await release_withdrawal(session, clock, txn, "provider")
    await service.transition(
        session,
        clock,
        txn,
        TxnStatus.FAILED,
        actor=actor,
        reason_code=failure_code,
        changes={"held_minor": 0, "failure_code": failure_code},
    )


async def return_withdrawal(session: AsyncSession, clock: Clock, txn: Transaction) -> Transaction:
    """Payout bounced after completion: REVERSAL transaction + WITHDRAWAL_RETURN journal (§4)."""
    assert txn.source_wallet_id is not None  # noqa: S101
    wallet = await get_wallet(session, txn.source_wallet_id)
    settlement = await system_account(
        session, AccountCode.PROVIDER_SETTLEMENT, txn.currency, txn.provider_code
    )
    refund_total = txn.amount_minor + txn.fee_minor  # the fee is refunded by default
    reversal = await service.create(
        session,
        clock,
        txn_type=TxnType.REVERSAL,
        amount=Money(refund_total, txn.currency),
        initiator_user_id=txn.initiator_user_id,
        actor=PROVIDER,
        destination_wallet_id=wallet.id,
        description="Hoàn tiền rút bị ngân hàng trả lại",
        related_transaction_id=txn.id,
        counterparty_snapshot=txn.counterparty_snapshot,
        extra={"reason": "PAYOUT_RETURNED"},
    )
    lines = [
        debit(settlement.id, Money(txn.amount_minor, txn.currency)),
        credit(wallet.ledger_account_id, Money(txn.amount_minor, txn.currency)),
    ]
    if txn.fee_minor:
        fees = await system_account(session, AccountCode.FEE_REVENUE, txn.currency)
        lines += [
            debit(fees.id, Money(txn.fee_minor, txn.currency)),
            credit(wallet.ledger_account_id, Money(txn.fee_minor, txn.currency)),
        ]
    await _post(session, clock, reversal, JournalKind.WITHDRAWAL_RETURN, tuple(lines), "provider")
    await service.transition(session, clock, reversal, TxnStatus.COMPLETED, actor=PROVIDER)
    await service.transition(
        session, clock, txn, TxnStatus.REVERSED, actor=PROVIDER, reason_code="PAYOUT_RETURNED"
    )
    return reversal


# ---------------------------------------------------------------- applying outcomes


async def _schedule_inquiry(session: AsyncSession, clock: Clock, op: ProviderOperation) -> None:
    op.inquiry_count += 1
    await mark_status(
        session,
        clock,
        op,
        op.status if op.status != "SUBMITTED" else "UNKNOWN",
        next_inquiry_at=clock.now() + next_inquiry_delay(op.inquiry_count),
    )


async def apply_outcome(
    session: AsyncSession,
    clock: Clock,
    transaction_id: uuid.UUID,
    operation_id: uuid.UUID,
    status: OperationStatus,
) -> Applied:
    """Apply a verified final (or still-pending) provider result exactly once."""
    txn = await service.lock(session, transaction_id)
    op = await lock_operation(session, operation_id)
    assert op is not None  # noqa: S101
    if status.amount_minor is not None and (
        status.amount_minor != op.amount_minor or status.currency != op.currency
    ):
        await request_recovery(
            session,
            clock,
            transaction_id=txn.id,
            operation_id=op.id,
            reason="AMOUNT_MISMATCH",
            detail=(
                f"expected {op.amount_minor} {op.currency}, "
                f"got {status.amount_minor} {status.currency}"
            ),
        )
        return Applied("AMOUNT_MISMATCH")

    if status.outcome is Outcome.PENDING:
        if op.status not in TERMINAL_OPS:
            await _schedule_inquiry(session, clock, op)
        return Applied("PENDING")
    if status.outcome is Outcome.RETURNED:
        return await _apply_return(session, clock, txn, op, status)
    if op.status in {"FAILED", "EXPIRED"} and status.outcome is Outcome.SUCCEEDED:
        # Money may exist at the provider although it told us otherwise: a human decides.
        await request_recovery(
            session,
            clock,
            transaction_id=txn.id,
            operation_id=op.id,
            reason="STATUS_MISMATCH",
            detail=f"provider SUCCEEDED after {op.status}",
        )
        return Applied("STATUS_MISMATCH")
    if op.status in TERMINAL_OPS:
        return Applied("IGNORED_STALE", f"operation already {op.status}")

    response = {"outcome": status.outcome.value, "failure_code": status.failure_code}
    reference = status.provider_reference
    if op.operation == "CHARGE":
        return await _apply_charge(session, clock, txn, op, status, response, reference)
    return await _apply_payout(session, clock, txn, op, status, response, reference)


async def _apply_charge(
    session: AsyncSession,
    clock: Clock,
    txn: Transaction,
    op: ProviderOperation,
    status: OperationStatus,
    response: dict[str, str | None],
    reference: str | None,
) -> Applied:
    open_states = {TxnStatus.CREATED.value, TxnStatus.PENDING.value}
    if txn.status not in open_states:
        # Provider and we disagree (e.g. success after we failed it): never credit silently.
        await mark_status(
            session, clock, op, status.outcome.value, response=response, resolved=True
        )
        await request_recovery(
            session,
            clock,
            transaction_id=txn.id,
            operation_id=op.id,
            reason="STATUS_MISMATCH",
            detail=f"provider {status.outcome} for transaction {txn.status}",
        )
        return Applied("STATUS_MISMATCH")
    if txn.status == TxnStatus.CREATED.value and status.outcome is not Outcome.FAILED:
        await service.transition(session, clock, txn, TxnStatus.PENDING, actor=PROVIDER)
    if status.outcome is Outcome.SUCCEEDED:
        await settle_deposit(session, clock, txn)
        await service.transition(session, clock, txn, TxnStatus.COMPLETED, actor=PROVIDER)
    elif status.outcome is Outcome.FAILED:
        code = status.failure_code or "PROVIDER_DECLINED"
        await service.transition(
            session,
            clock,
            txn,
            TxnStatus.FAILED,
            actor=PROVIDER,
            reason_code=code,
            changes={"failure_code": code},
        )
    else:  # EXPIRED
        await service.transition(session, clock, txn, TxnStatus.EXPIRED, actor=PROVIDER)
    await mark_status(
        session,
        clock,
        op,
        status.outcome.value,
        provider_reference=reference,
        error_code=status.failure_code,
        response=response,
        resolved=True,
    )
    return Applied("APPLIED")


async def _apply_payout(
    session: AsyncSession,
    clock: Clock,
    txn: Transaction,
    op: ProviderOperation,
    status: OperationStatus,
    response: dict[str, str | None],
    reference: str | None,
) -> Applied:
    if txn.status not in {TxnStatus.PENDING.value, TxnStatus.PROCESSING.value}:
        await mark_status(
            session, clock, op, status.outcome.value, response=response, resolved=True
        )
        await request_recovery(
            session,
            clock,
            transaction_id=txn.id,
            operation_id=op.id,
            reason="STATUS_MISMATCH",
            detail=f"provider {status.outcome} for transaction {txn.status}",
        )
        return Applied("STATUS_MISMATCH")
    if status.outcome is Outcome.SUCCEEDED:
        if txn.status == TxnStatus.PENDING.value:
            await service.transition(session, clock, txn, TxnStatus.PROCESSING, actor=PROVIDER)
        await settle_withdrawal(session, clock, txn)
        await service.transition(
            session, clock, txn, TxnStatus.COMPLETED, actor=PROVIDER, changes={"held_minor": 0}
        )
    else:  # FAILED (payouts do not expire)
        await fail_withdrawal(
            session, clock, txn, failure_code=status.failure_code or "PAYOUT_FAILED"
        )
    await mark_status(
        session,
        clock,
        op,
        status.outcome.value,
        provider_reference=reference,
        error_code=status.failure_code,
        response=response,
        resolved=True,
    )
    return Applied("APPLIED")


async def _apply_return(
    session: AsyncSession,
    clock: Clock,
    txn: Transaction,
    op: ProviderOperation,
    status: OperationStatus,
) -> Applied:
    if op.operation != "PAYOUT" or op.status == "RETURNED":
        return Applied("IGNORED_STALE", "already returned or not a payout")
    if txn.status in {TxnStatus.PENDING.value, TxnStatus.PROCESSING.value}:
        # The success was never seen (lost callback): book it first, then the return.
        await _apply_payout(
            session,
            clock,
            txn,
            op,
            OperationStatus(op_key(op), status.provider_reference, Outcome.SUCCEEDED),
            {"outcome": "SUCCEEDED", "failure_code": None},
            status.provider_reference,
        )
    if txn.status != TxnStatus.COMPLETED.value:
        return Applied("STATUS_MISMATCH", f"return for transaction {txn.status}")
    await return_withdrawal(session, clock, txn)
    await mark_status(session, clock, op, "RETURNED", resolved=True)
    return Applied("APPLIED")


def op_key(op: ProviderOperation) -> str:
    return str(op.id)
