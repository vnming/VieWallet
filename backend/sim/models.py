"""Simulator tables (schema ``simulator`` in the simulator's own database)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sim.db import SimBase

KINDS = ("CHARGE", "PAYOUT")
# AWAITING_CUSTOMER: charge created, the customer has not finished the SimBank checkout.
STATUSES = ("AWAITING_CUSTOMER", "PROCESSING", "SUCCEEDED", "FAILED", "EXPIRED", "RETURNED")
ACTIONS = ("SUCCEED", "EXPIRE", "RETURN")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class SimOperation(SimBase):
    __tablename__ = "sim_operations"
    __table_args__ = (
        CheckConstraint(_in("kind", KINDS), name="kind_valid"),
        CheckConstraint(_in("status", STATUSES), name="status_valid"),
        CheckConstraint(
            f"next_action IS NULL OR {_in('next_action', ACTIONS)}", name="next_action_valid"
        ),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        Index(
            "ix_sim_operations_due",
            "next_action_at",
            postgresql_where=text("next_action IS NOT NULL"),
        ),
        Index("ix_sim_operations_business_date", "business_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    operation_key: Mapped[str] = mapped_column(String(64), unique=True)
    provider_reference: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20))
    scenario: Mapped[str] = mapped_column(String(30))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    reference: Mapped[str] = mapped_column(String(64))
    method: Mapped[str | None] = mapped_column(String(20))
    destination: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    callback_url: Mapped[str] = mapped_column(Text)
    return_url: Mapped[str | None] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(40))
    next_action: Mapped[str | None] = mapped_column(String(10))
    next_action_at: Mapped[datetime | None]
    business_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]


class SimCallback(SimBase):
    """One delivery attempt chain of one event. Duplicate scenarios create several rows."""

    __tablename__ = "sim_callbacks"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'DELIVERED', 'GIVEN_UP')", name="status_valid"),
        Index(
            "ix_sim_callbacks_due", "next_attempt_at", postgresql_where=text("status = 'PENDING'")
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64))
    operation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("simulator.sim_operations.id"))
    type: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    valid_signature: Mapped[bool]
    status: Mapped[str] = mapped_column(String(10))
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime]
    last_response_code: Mapped[int | None]
    delivered_at: Mapped[datetime | None]
    created_at: Mapped[datetime]


class SimSetting(SimBase):
    __tablename__ = "sim_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)


class SimLog(SimBase):
    __tablename__ = "sim_logs"
    __table_args__ = (Index("ix_sim_logs_at", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    at: Mapped[datetime]
    direction: Mapped[str] = mapped_column(String(10))  # IN (API call) / OUT (callback)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(200))
    status_code: Mapped[int | None]
    operation_key: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(String(300))
