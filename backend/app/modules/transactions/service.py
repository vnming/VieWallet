"""Transaction engine: creation, compare-and-set status changes, events, queries."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.context import get_correlation_id
from app.core.errors import ConflictError, NotFoundError, register_titles
from app.core.ids import new_id
from app.core.money import Money
from app.core.outbox import publish
from app.modules.transactions.domain.reference import is_reference, new_reference
from app.modules.transactions.domain.state_machine import (
    InvalidStateTransition,
    TxnStatus,
    TxnType,
    check_transition,
)
from app.modules.transactions.models import Transaction, TransactionEvent

register_titles(
    {
        "TXN_NOT_FOUND": {"vi": "Không tìm thấy giao dịch", "en": "Transaction not found"},
        "TXN_INVALID_STATE": {
            "vi": "Trạng thái giao dịch không cho phép thao tác này",
            "en": "The transaction's status does not allow this action",
        },
        "TXN_CONCURRENT_UPDATE": {
            "vi": "Giao dịch vừa được cập nhật, vui lòng thử lại",
            "en": "The transaction was just updated, please retry",
        },
    }
)


class TransactionNotFoundError(NotFoundError):
    code = "TXN_NOT_FOUND"


class TxnInvalidStateError(ConflictError):
    code = "TXN_INVALID_STATE"


class TxnConcurrentUpdateError(ConflictError):
    code = "TXN_CONCURRENT_UPDATE"


@dataclass(frozen=True, slots=True)
class Actor:
    type: str  # USER, STAFF, SYSTEM, PROVIDER
    id: uuid.UUID | None = None


SYSTEM = Actor("SYSTEM")


async def create(
    session: AsyncSession,
    clock: Clock,
    *,
    txn_type: TxnType,
    amount: Money,
    initiator_user_id: uuid.UUID,
    actor: Actor,
    fee: Money | None = None,
    source_wallet_id: uuid.UUID | None = None,
    destination_wallet_id: uuid.UUID | None = None,
    description: str = "",
    idempotency_key: str | None = None,
    related_transaction_id: uuid.UUID | None = None,
    counterparty_snapshot: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Transaction:
    now = clock.now()
    fee = fee or Money.zero(amount.currency)
    txn = Transaction(
        id=new_id(),
        reference=new_reference(now),
        type=txn_type.value,
        status=TxnStatus.CREATED.value,
        amount_minor=amount.amount_minor,
        fee_minor=fee.amount_minor,
        held_minor=0,
        refunded_amount_minor=0,
        currency=amount.currency,
        initiator_user_id=initiator_user_id,
        source_wallet_id=source_wallet_id,
        destination_wallet_id=destination_wallet_id,
        counterparty_snapshot=dict(counterparty_snapshot) if counterparty_snapshot else None,
        description=description[:140],
        idempotency_key=idempotency_key,
        related_transaction_id=related_transaction_id,
        extra=dict(extra or {}),
        created_at=now,
        updated_at=now,
        version=1,
    )
    session.add(txn)
    await session.flush()
    await _event(session, clock, txn.id, None, TxnStatus.CREATED, actor=actor)
    return txn


async def _event(
    session: AsyncSession,
    clock: Clock,
    transaction_id: uuid.UUID,
    from_status: TxnStatus | None,
    to_status: TxnStatus,
    *,
    actor: Actor,
    reason_code: str | None = None,
    message: str | None = None,
) -> None:
    session.add(
        TransactionEvent(
            id=new_id(),
            transaction_id=transaction_id,
            from_status=from_status.value if from_status else None,
            to_status=to_status.value,
            reason_code=reason_code,
            message=message[:300] if message else None,
            actor_type=actor.type,
            actor_id=actor.id,
            correlation_id=get_correlation_id(),
            at=clock.now(),
        )
    )
    await session.flush()


async def transition(
    session: AsyncSession,
    clock: Clock,
    txn: Transaction,
    to_status: TxnStatus,
    *,
    actor: Actor,
    reason_code: str | None = None,
    message: str | None = None,
    changes: Mapping[str, Any] | None = None,
) -> Transaction:
    """Compare-and-set the status (and optional columns); write the event and outbox row."""
    txn_type = TxnType(txn.type)
    from_status = TxnStatus(txn.status)
    try:
        check_transition(txn_type, from_status, to_status)
    except InvalidStateTransition as exc:
        raise TxnInvalidStateError(str(exc)) from exc
    now = clock.now()
    values: dict[str, Any] = {
        "status": to_status.value,
        "version": Transaction.version + 1,
        "updated_at": now,
        **(changes or {}),
    }
    if to_status is TxnStatus.COMPLETED:
        values.setdefault("completed_at", now)
    new_version = await session.scalar(
        update(Transaction)
        .where(
            Transaction.id == txn.id,
            Transaction.status == from_status.value,
            Transaction.version == txn.version,
        )
        .values(**values)
        .returning(Transaction.version)
    )
    if new_version is None:
        raise TxnConcurrentUpdateError()
    await session.refresh(txn)
    await _event(
        session,
        clock,
        txn.id,
        from_status,
        to_status,
        actor=actor,
        reason_code=reason_code,
        message=message,
    )
    await publish(
        session,
        clock,
        topic="transaction.status_changed",
        aggregate_type="transaction",
        aggregate_id=str(txn.id),
        payload={
            "transaction_id": str(txn.id),
            "reference": txn.reference,
            "type": txn.type,
            "from": from_status.value,
            "to": to_status.value,
        },
    )
    return txn


async def lock(session: AsyncSession, transaction_id: uuid.UUID) -> Transaction:
    txn = await session.scalar(
        select(Transaction)
        .where(Transaction.id == transaction_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if txn is None:
        raise TransactionNotFoundError()
    return txn


async def get(session: AsyncSession, transaction_id: uuid.UUID) -> Transaction:
    txn = await session.get(Transaction, transaction_id)
    if txn is None:
        raise TransactionNotFoundError()
    return txn


async def get_by_reference(session: AsyncSession, reference: str) -> Transaction:
    if not is_reference(reference):
        raise TransactionNotFoundError()
    txn = await session.scalar(select(Transaction).where(Transaction.reference == reference))
    if txn is None:
        raise TransactionNotFoundError()
    return txn


async def events_of(session: AsyncSession, transaction_id: uuid.UUID) -> list[TransactionEvent]:
    rows = await session.scalars(
        select(TransactionEvent)
        .where(TransactionEvent.transaction_id == transaction_id)
        .order_by(TransactionEvent.at, TransactionEvent.id)
    )
    return list(rows.all())


# ---------------------------------------------------------------- customer views


def visible_to_customer() -> Any:
    """Staff proposals (adjustments awaiting approval) are not shown until they complete."""
    return or_(
        Transaction.type != TxnType.ADJUSTMENT.value,
        Transaction.status == TxnStatus.COMPLETED.value,
    )


@dataclass(frozen=True, slots=True)
class TransactionPage:
    items: list[Transaction]
    next_cursor: uuid.UUID | None


async def list_for_wallet(
    session: AsyncSession,
    wallet_id: uuid.UUID,
    *,
    limit: int = 20,
    cursor: uuid.UUID | None = None,
    txn_type: TxnType | None = None,
    status: TxnStatus | None = None,
) -> TransactionPage:
    """Newest first. UUIDv7 ids are time-ordered, so the id alone is a stable cursor."""
    conditions = [
        or_(
            Transaction.source_wallet_id == wallet_id,
            Transaction.destination_wallet_id == wallet_id,
        ),
        visible_to_customer(),
    ]
    if cursor is not None:
        conditions.append(Transaction.id < cursor)
    if txn_type is not None:
        conditions.append(Transaction.type == txn_type.value)
    if status is not None:
        conditions.append(Transaction.status == status.value)
    rows = list(
        (
            await session.scalars(
                select(Transaction)
                .where(and_(*conditions))
                .order_by(Transaction.id.desc())
                .limit(limit + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    return TransactionPage(items, items[-1].id if has_more and items else None)


async def get_for_wallet(
    session: AsyncSession, wallet_id: uuid.UUID, reference: str
) -> Transaction:
    """404 (not 403) for other people's transactions, so references cannot be probed."""
    txn = await get_by_reference(session, reference)
    involved = wallet_id in (txn.source_wallet_id, txn.destination_wallet_id)
    if not involved or (
        txn.type == TxnType.ADJUSTMENT.value and txn.status != TxnStatus.COMPLETED.value
    ):
        raise TransactionNotFoundError()
    return txn


# ---------------------------------------------------------------- invariants


async def held_totals(session: AsyncSession) -> dict[str, int]:
    """Σ held_minor per currency (I5: must equal the HOLD account balance)."""
    rows = await session.execute(
        select(Transaction.currency, func.sum(Transaction.held_minor))
        .where(Transaction.held_minor > 0)
        .group_by(Transaction.currency)
    )
    return {currency: int(total) for currency, total in rows.all()}
