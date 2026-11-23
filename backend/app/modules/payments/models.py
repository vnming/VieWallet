"""Merchants and payment requests (database.md §3.5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id
from app.core.models import CurrencyRecord
from app.modules.identity.public import User
from app.modules.wallet.public import Wallet

# Tables referenced by foreign keys must be registered in this process's metadata too.
_FOREIGN_KEY_TARGETS = (CurrencyRecord, User, Wallet)


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class MerchantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class IssuerType(StrEnum):
    MERCHANT = "MERCHANT"
    USER = "USER"


class RequestStatus(StrEnum):
    OPEN = "OPEN"
    PAID = "PAID"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    DECLINED = "DECLINED"


class Merchant(Base):
    __tablename__ = "merchants"
    __table_args__ = (
        CheckConstraint(_in("status", [s.value for s in MerchantStatus]), name="status_valid"),
        CheckConstraint("mcc ~ '^[0-9]{4}$'", name="mcc_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"), unique=True)
    wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.wallets.id"), unique=True)
    legal_name: Mapped[str] = mapped_column(String(120))
    display_name: Mapped[str] = mapped_column(String(60))
    mcc: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(15))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class PaymentRequest(Base):
    __tablename__ = "payment_requests"
    __table_args__ = (
        CheckConstraint(_in("issuer_type", [t.value for t in IssuerType]), name="issuer_valid"),
        CheckConstraint(_in("status", [s.value for s in RequestStatus]), name="status_valid"),
        CheckConstraint("amount_minor IS NULL OR amount_minor > 0", name="amount_positive"),
        CheckConstraint("paid_count >= 0", name="paid_count_non_negative"),
        # Single-use requests are paid at most once (FT-32 backstop).
        CheckConstraint("NOT single_use OR paid_count <= 1", name="single_use_paid_once"),
        CheckConstraint(
            "(status = 'PAID') = (paid_transaction_id IS NOT NULL)", name="paid_has_transaction"
        ),
        CheckConstraint(
            "issuer_type = 'MERCHANT' OR amount_minor IS NOT NULL", name="user_request_has_amount"
        ),
        Index("ix_payment_requests_issuer", "issuer_user_id", "created_at"),
        Index("ix_payment_requests_payer", "payer_user_id", "status"),
        Index("ix_payment_requests_open_expiry", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    issuer_type: Mapped[str] = mapped_column(String(10))
    issuer_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"))
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app.merchants.id"))
    destination_wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.wallets.id"))
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), ForeignKey("app.currencies.code"))
    description: Mapped[str] = mapped_column(String(140), default="")
    single_use: Mapped[bool]
    status: Mapped[str] = mapped_column(String(15))
    payer_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app.users.id"))
    paid_count: Mapped[int] = mapped_column(default=0)
    # Set while a single-use request is paid (or reserved by a payment under risk review).
    # No FK: transactions sit in a higher module; the link is checked by the use case.
    paid_transaction_id: Mapped[uuid.UUID | None]
    signature: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
