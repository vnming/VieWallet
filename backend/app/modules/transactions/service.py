"""Transaction engine: creation, compare-and-set status changes, events, queries."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.context import get_correlation_id
from app.core.errors import ConflictError, NotFoundError, register_titles
from app.core.ids import new_id
from app.core.money import Money
from app.core.outbox import publish
from app.modules.identity.public import User
from app.modules.transactions.domain.reference import is_reference, new_reference
from app.modules.transactions.domain.state_machine import (
    InvalidStateTransition,
    TxnStatus,
    TxnType,
    check_transition,
)
from app.modules.transactions.models import Transaction, TransactionEvent
from app.modules.wallet.public import Wallet

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
            "amount": str(txn.amount_minor),
            "fee": str(txn.fee_minor),
            "currency": txn.currency,
            "failure_code": txn.failure_code,
            "subtype": txn.extra.get("subtype"),
            # Consumers (notifications) must not query other modules' tables.
            "recipients": await _recipients(session, txn),
        },
    )
    return txn


async def _recipients(session: AsyncSession, txn: Transaction) -> list[dict[str, str]]:
    sides = [(txn.source_wallet_id, "OUT"), (txn.destination_wallet_id, "IN")]
    out = []
    for wallet_id, direction in sides:
        if wallet_id is None:
            continue
        row = (
            await session.execute(
                select(Wallet.owner_user_id, User.locale)
                .join(User, User.id == Wallet.owner_user_id)
                .where(Wallet.id == wallet_id)
            )
        ).one_or_none()
        if row is not None:
            out.append({"user_id": str(row[0]), "locale": row[1], "direction": direction})
    return out


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


STAFF_PROPOSALS = (TxnType.ADJUSTMENT.value, TxnType.REVERSAL.value)


def visible_to_customer() -> Any:
    """Staff proposals (adjustments, reversals awaiting approval) stay hidden until they
    complete."""
    return or_(
        Transaction.type.not_in(STAFF_PROPOSALS),
        Transaction.status == TxnStatus.COMPLETED.value,
    )


@dataclass(frozen=True, slots=True)
class TransactionPage:
    items: list[Transaction]
    next_cursor: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class HistoryFilter:
    """Customer history filters (PRD FR-TXN-05). All optional; combined with AND."""

    txn_type: TxnType | None = None
    status: TxnStatus | None = None
    direction: str | None = None  # IN or OUT, relative to the wallet
    created_from: datetime | None = None  # inclusive
    created_to: datetime | None = None  # exclusive
    min_amount: int | None = None
    max_amount: int | None = None
    query: str | None = None  # reference (exact) or words in description / counterparty


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def history_conditions(wallet_id: uuid.UUID, flt: HistoryFilter) -> list[Any]:
    conditions: list[Any] = [
        or_(
            Transaction.source_wallet_id == wallet_id,
            Transaction.destination_wallet_id == wallet_id,
        ),
        visible_to_customer(),
    ]
    if flt.txn_type is not None:
        conditions.append(Transaction.type == flt.txn_type.value)
    if flt.status is not None:
        conditions.append(Transaction.status == flt.status.value)
    if flt.direction == "OUT":
        conditions.append(Transaction.source_wallet_id == wallet_id)
    elif flt.direction == "IN":
        conditions.append(Transaction.destination_wallet_id == wallet_id)
    if flt.created_from is not None:
        conditions.append(Transaction.created_at >= flt.created_from)
    if flt.created_to is not None:
        conditions.append(Transaction.created_at < flt.created_to)
    if flt.min_amount is not None:
        conditions.append(Transaction.amount_minor >= flt.min_amount)
    if flt.max_amount is not None:
        conditions.append(Transaction.amount_minor <= flt.max_amount)
    if flt.query:
        text = flt.query.strip()
        pattern = _like(text)
        conditions.append(
            or_(
                Transaction.reference == text.upper(),
                Transaction.description.ilike(pattern, escape="\\"),
                Transaction.counterparty_snapshot["name"].astext.ilike(pattern, escape="\\"),
                Transaction.extra["source_party"]["name"].astext.ilike(pattern, escape="\\"),
            )
        )
    return conditions


async def list_for_wallet(
    session: AsyncSession,
    wallet_id: uuid.UUID,
    *,
    limit: int = 20,
    cursor: uuid.UUID | None = None,
    txn_type: TxnType | None = None,
    status: TxnStatus | None = None,
    flt: HistoryFilter | None = None,
) -> TransactionPage:
    """Newest first. UUIDv7 ids are time-ordered, so the id alone is a stable cursor."""
    flt = flt or HistoryFilter(txn_type=txn_type, status=status)
    conditions = history_conditions(wallet_id, flt)
    if cursor is not None:
        conditions.append(Transaction.id < cursor)
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
    if not involved or (txn.type in STAFF_PROPOSALS and txn.status != TxnStatus.COMPLETED.value):
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
