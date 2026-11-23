"""Business transactions and their append-only event log (database.md §3.4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id
from app.core.models import CurrencyRecord
from app.modules.identity.public import User
from app.modules.payments.public import PaymentRequest
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.wallet.public import BankDestination, Wallet

ACTOR_TYPES = ("USER", "STAFF", "SYSTEM", "PROVIDER")

# Tables referenced by foreign keys must be registered in this process's metadata too.
_FOREIGN_KEY_TARGETS = (CurrencyRecord, User, Wallet, BankDestination, PaymentRequest)


def _in(column: str, values: list[str] | tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("initiator_user_id", "idempotency_key"),
        CheckConstraint(_in("type", [t.value for t in TxnType]), name="type_valid"),
        CheckConstraint(_in("status", [s.value for s in TxnStatus]), name="status_valid"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("fee_minor >= 0", name="fee_non_negative"),
        CheckConstraint("held_minor >= 0", name="held_non_negative"),
        CheckConstraint(
            "refunded_amount_minor >= 0 AND refunded_amount_minor <= amount_minor",
            name="refunded_within_amount",
        ),
        CheckConstraint(
            "source_wallet_id IS NULL OR destination_wallet_id IS NULL"
            " OR source_wallet_id <> destination_wallet_id",
            name="distinct_wallets",
        ),
        CheckConstraint(
            "CASE type"
            " WHEN 'TRANSFER' THEN source_wallet_id IS NOT NULL"
            " AND destination_wallet_id IS NOT NULL"
            " WHEN 'PAYMENT' THEN source_wallet_id IS NOT NULL"
            " AND destination_wallet_id IS NOT NULL"
            " WHEN 'DEPOSIT' THEN destination_wallet_id IS NOT NULL AND source_wallet_id IS NULL"
            " WHEN 'WITHDRAWAL' THEN source_wallet_id IS NOT NULL AND destination_wallet_id IS NULL"
            " WHEN 'ADJUSTMENT' THEN (source_wallet_id IS NULL) <> (destination_wallet_id IS NULL)"
            " WHEN 'REFUND' THEN source_wallet_id IS NOT NULL AND destination_wallet_id IS NOT NULL"
            " ELSE TRUE END",
            name="wallets_match_type",
        ),
        Index("ix_transactions_source_wallet", "source_wallet_id", "id"),
        Index("ix_transactions_destination_wallet", "destination_wallet_id", "id"),
        Index("ix_transactions_ops_queue", "status", "type", "created_at"),
        Index("ix_transactions_limits", "source_wallet_id", "type", "created_at"),
        Index("ix_transactions_related", "related_transaction_id"),
        Index("ix_transactions_payment_request", "payment_request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    reference: Mapped[str] = mapped_column(String(18), unique=True)
    type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    fee_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    held_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    refunded_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), ForeignKey("app.currencies.code"))
    initiator_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"))
    source_wallet_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app.wallets.id"))
    destination_wallet_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app.wallets.id"))
    counterparty_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    description: Mapped[str] = mapped_column(String(140), default="")
    category_code: Mapped[str | None] = mapped_column(String(40))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    related_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app.transactions.id")
    )
    provider_code: Mapped[str | None] = mapped_column(String(20))
    bank_destination_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app.bank_destinations.id")
    )
    payment_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app.payment_requests.id")
    )
    fee_rule_id: Mapped[uuid.UUID | None]
    fee_rule_version: Mapped[int | None]
    quote_expires_at: Mapped[datetime | None]
    risk_decision: Mapped[str | None] = mapped_column(String(10))
    failure_code: Mapped[str | None] = mapped_column(String(50))
    failure_message: Mapped[str | None] = mapped_column(String(300))
    # "metadata" is reserved on declarative classes, hence the attribute name.
    extra: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    confirmed_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    version: Mapped[int] = mapped_column(default=1)


class TransactionEvent(Base):
    """One row per state change (append-only: trigger + grants)."""

    __tablename__ = "transaction_events"
    __table_args__ = (
        CheckConstraint(_in("actor_type", ACTOR_TYPES), name="actor_type_valid"),
        Index("ix_transaction_events_transaction_at", "transaction_id", "at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.transactions.id"))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(String(50))
    message: Mapped[str | None] = mapped_column(String(300))
    actor_type: Mapped[str] = mapped_column(String(10))
    actor_id: Mapped[uuid.UUID | None]
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime]
