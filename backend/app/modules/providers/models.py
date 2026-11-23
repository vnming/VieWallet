"""Provider tables (database.md §3.6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id
from app.core.models import CurrencyRecord

# Tables referenced by foreign keys must be registered in this process's metadata too.
_FOREIGN_KEY_TARGETS = (CurrencyRecord,)

OPERATION_KINDS = ("CHARGE", "PAYOUT")
# CREATED: recorded, not sent. SUBMITTED: a send is in flight (or the process died during it).
OPERATION_STATUSES = (
    "CREATED",
    "SUBMITTED",
    "ACCEPTED",
    "SUCCEEDED",
    "FAILED",
    "UNKNOWN",
    "EXPIRED",
    "RETURNED",
)
OPEN_STATUSES = ("SUBMITTED", "ACCEPTED", "UNKNOWN")
CALLBACK_RESULTS = (
    "APPLIED",
    "IGNORED_DUPLICATE",
    "IGNORED_STALE",
    "REJECTED",
    "ORPHAN",
    "AMOUNT_MISMATCH",
    "STATUS_MISMATCH",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Provider(Base):
    __tablename__ = "providers"
    __table_args__ = (CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="status_valid"),)

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(10))
    capabilities: Mapped[list[str]] = mapped_column(JSONB)


class ProviderOperation(Base):
    __tablename__ = "provider_operations"
    __table_args__ = (
        CheckConstraint(_in("operation", OPERATION_KINDS), name="operation_valid"),
        CheckConstraint(_in("status", OPERATION_STATUSES), name="status_valid"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        UniqueConstraint("transaction_id", "operation"),
        Index(
            "ix_provider_operations_inquiry",
            "next_inquiry_at",
            postgresql_where=text("status IN ('SUBMITTED', 'ACCEPTED', 'UNKNOWN')"),
        ),
    )

    # The id is the provider-side idempotency key (operation_key).
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    transaction_id: Mapped[uuid.UUID]
    provider_code: Mapped[str] = mapped_column(String(20), ForeignKey("app.providers.code"))
    operation: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), ForeignKey("app.currencies.code"))
    attempt: Mapped[int] = mapped_column(default=0)
    provider_reference: Mapped[str | None] = mapped_column(String(64), unique=True)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(50))
    submitted_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]
    next_inquiry_at: Mapped[datetime | None]
    inquiry_count: Mapped[int] = mapped_column(default=0)
    recovery_flagged: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ProviderCallback(Base):
    """Inbox of provider callbacks (append-only). Only verified rows dedupe by event id, so a
    forged callback cannot block the genuine event with the same id."""

    __tablename__ = "provider_callbacks"
    __table_args__ = (
        Index(
            "uq_provider_callbacks_event",
            "provider_code",
            "event_id",
            unique=True,
            postgresql_where=text("signature_valid"),
        ),
        Index("ix_provider_callbacks_received", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    provider_code: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str | None] = mapped_column(String(40))
    operation_key: Mapped[str | None] = mapped_column(String(64))
    provider_reference: Mapped[str | None] = mapped_column(String(64))
    signature_valid: Mapped[bool]
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB)
    body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    raw_excerpt: Mapped[str | None] = mapped_column(String(500))
    received_at: Mapped[datetime]


class ProviderCallbackResult(Base):
    """What happened to a callback (append-only, so the inbox itself never changes)."""

    __tablename__ = "provider_callback_results"
    __table_args__ = (
        CheckConstraint(_in("result", CALLBACK_RESULTS), name="result_valid"),
        Index("ix_provider_callback_results_callback", "callback_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    callback_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.provider_callbacks.id"))
    result: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str | None] = mapped_column(String(300))
    at: Mapped[datetime]
