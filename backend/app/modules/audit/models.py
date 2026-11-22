"""Append-only audit log with a SHA-256 hash chain (database.md §3.8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Identity, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id

ACTOR_TYPES = ("USER", "STAFF", "SYSTEM", "ANONYMOUS")
RESULTS = ("SUCCESS", "DENIED", "FAILED")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('USER', 'STAFF', 'SYSTEM', 'ANONYMOUS')", name="actor_type_valid"
        ),
        CheckConstraint("result IN ('SUCCESS', 'DENIED', 'FAILED')", name="result_valid"),
        Index("ix_audit_logs_target", "target_type", "target_id", "at"),
        Index("ix_audit_logs_actor", "actor_id", "at"),
        Index("ix_audit_logs_action", "action", "at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    # Chain order. Assigned while holding the chain lock, so it matches hash order.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    at: Mapped[datetime]
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[uuid.UUID | None]
    actor_roles: Mapped[list[str]] = mapped_column(JSONB, default=list)
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
