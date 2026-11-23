"""Asynchronous risk monitoring (security.md §8.3): patterns that need an analyst but not a
synchronous block. Runs from outbox events, so it never slows down or fails a payment.

* FAN_IN: one wallet receives completed transfers/payments from many different wallets in a
  short time (money mule pattern).
* ROUND_TRIP: money goes A -> B and comes back B -> A with the same amount within an hour.
* REPEATED_PIN_LOCK: the PIN of a user was locked more than once in the lock window.

Alerts are unique per detected pattern (dedup key), so a replayed event creates nothing new.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.db import Database
from app.core.outbox import HandlerRegistry, OutboxMessage
from app.modules.risk.public import NewAlert, raise_alert
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.wallet.public import Wallet

TRANSACTION_TOPIC = "transaction.status_changed"
PIN_LOCKED_TOPIC = "identity.pin_locked"

FAN_IN_WINDOW = timedelta(hours=1)
FAN_IN_MIN_SENDERS = 5
ROUND_TRIP_WINDOW = timedelta(hours=1)
REPEATED_LOCKS = 2
WATCHED_TYPES = (TxnType.TRANSFER.value, TxnType.PAYMENT.value)


async def _owner(session: AsyncSession, wallet_id: uuid.UUID) -> uuid.UUID | None:
    owner: uuid.UUID | None = await session.scalar(
        select(Wallet.owner_user_id).where(Wallet.id == wallet_id)
    )
    return owner


async def check_transaction(session: AsyncSession, clock: Clock, txn: Transaction) -> int:
    """Returns how many new alerts were raised for this completed transaction."""
    if txn.type not in WATCHED_TYPES or txn.status != TxnStatus.COMPLETED.value:
        return 0
    assert txn.source_wallet_id is not None  # noqa: S101 - CHECK wallets_match_type
    assert txn.destination_wallet_id is not None  # noqa: S101
    completed_at = txn.completed_at or txn.updated_at
    raised = 0

    senders = int(
        await session.scalar(
            select(func.count(func.distinct(Transaction.source_wallet_id))).where(
                Transaction.destination_wallet_id == txn.destination_wallet_id,
                Transaction.type.in_(WATCHED_TYPES),
                Transaction.status == TxnStatus.COMPLETED.value,
                Transaction.completed_at >= completed_at - FAN_IN_WINDOW,
                Transaction.completed_at <= completed_at,
            )
        )
        or 0
    )
    receiver = await _owner(session, txn.destination_wallet_id)
    if senders >= FAN_IN_MIN_SENDERS and receiver is not None:
        bucket = int(completed_at.timestamp() // FAN_IN_WINDOW.total_seconds())
        raised += await raise_alert(
            session,
            clock,
            NewAlert(
                user_id=receiver,
                type="FAN_IN",
                severity="MEDIUM",
                dedup_key=f"FAN_IN:{txn.destination_wallet_id}:{bucket}",
                detail={"distinct_senders_1h": str(senders), "last_reference": txn.reference},
                transaction_id=txn.id,
            ),
        )

    back = await session.scalar(
        select(Transaction.reference).where(
            Transaction.source_wallet_id == txn.destination_wallet_id,
            Transaction.destination_wallet_id == txn.source_wallet_id,
            Transaction.amount_minor == txn.amount_minor,
            Transaction.type.in_(WATCHED_TYPES),
            Transaction.status == TxnStatus.COMPLETED.value,
            Transaction.completed_at >= completed_at - ROUND_TRIP_WINDOW,
            # Only an earlier leg: the outgoing leg must not alert when its return is seen.
            # Same instant: UUIDv7 ids order them.
            or_(
                Transaction.completed_at < completed_at,
                and_(Transaction.completed_at == completed_at, Transaction.id < txn.id),
            ),
        )
    )
    sender = await _owner(session, txn.source_wallet_id)
    if back is not None and sender is not None:
        raised += await raise_alert(
            session,
            clock,
            NewAlert(
                user_id=sender,
                type="ROUND_TRIP",
                severity="LOW",
                dedup_key=f"ROUND_TRIP:{txn.id}",
                detail={"reference": txn.reference, "earlier_reference": str(back)},
                transaction_id=txn.id,
            ),
        )
    return raised


async def on_transaction(db: Database, clock: Clock, message: OutboxMessage) -> None:
    if message.payload.get("to") != TxnStatus.COMPLETED.value:
        return
    if message.payload.get("type") not in WATCHED_TYPES:
        return
    async with db.transaction() as session:
        txn = await session.get(Transaction, uuid.UUID(str(message.payload["transaction_id"])))
        if txn is not None:
            await check_transaction(session, clock, txn)


async def on_pin_locked(db: Database, clock: Clock, message: OutboxMessage) -> None:
    count = int(message.payload.get("lock_count", 0))
    if count < REPEATED_LOCKS:
        return
    user_id = uuid.UUID(str(message.payload["user_id"]))
    async with db.transaction() as session:
        await raise_alert(
            session,
            clock,
            NewAlert(
                user_id=user_id,
                type="REPEATED_PIN_LOCK",
                severity="HIGH" if count >= REPEATED_LOCKS + 1 else "MEDIUM",
                dedup_key=f"PIN_LOCK:{message.id}",
                detail={"locks_in_window": str(count)},
            ),
        )


def register_monitoring(registry: HandlerRegistry, db: Database, clock: Clock) -> None:
    async def transaction_handler(message: OutboxMessage) -> None:
        await on_transaction(db, clock, message)

    async def pin_handler(message: OutboxMessage) -> None:
        await on_pin_locked(db, clock, message)

    registry.register(TRANSACTION_TOPIC, transaction_handler)
    registry.register(PIN_LOCKED_TOPIC, pin_handler)
