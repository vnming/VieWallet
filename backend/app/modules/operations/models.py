"""Approval requests (maker-checker) and reconciliation (database.md §3.7)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id
from app.modules.identity.public import User

# Tables referenced by foreign keys must be registered in this process's metadata too.
_FOREIGN_KEY_TARGETS = (User,)


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"  # transient inside the approving transaction
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ActionType(StrEnum):
    ADJUSTMENT = "ADJUSTMENT"
    WALLET_UNFREEZE = "WALLET_UNFREEZE"


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(_in("status", [s.value for s in ApprovalStatus]), name="status_valid"),
        CheckConstraint(_in("action_type", [a.value for a in ActionType]), name="action_valid"),
        # FT-30: the database refuses self-approval even if the service check were bypassed.
        CheckConstraint(
            "checker_id IS NULL OR checker_id <> maker_id", name="maker_is_not_checker"
        ),
        Index("ix_approval_requests_status_created", "status", "created_at"),
        Index(
            "uq_approval_requests_pending_target",
            "action_type",
            "target_type",
            "target_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    action_type: Mapped[str] = mapped_column(String(30))
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    maker_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"))
    maker_reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20))
    checker_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app.users.id"))
    checker_note: Mapped[str | None] = mapped_column(String(500))
    failure_code: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime]
    decided_at: Mapped[datetime | None]
    executed_at: Mapped[datetime | None]
    expires_at: Mapped[datetime]


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (
        CheckConstraint("kind IN ('INTERNAL', 'PROVIDER')", name="kind_valid"),
        CheckConstraint("status IN ('RUNNING', 'SUCCEEDED', 'FAILED')", name="status_valid"),
        Index("ix_reconciliation_runs_started", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10))
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    violations: Mapped[int] = mapped_column(default=0)
    triggered_by: Mapped[uuid.UUID | None]
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]


class ReconciliationItem(Base):
    __tablename__ = "reconciliation_items"
    __table_args__ = (
        CheckConstraint(
            "type IN ('MISSING_INTERNAL', 'MISSING_PROVIDER', 'AMOUNT_MISMATCH',"
            " 'STATUS_MISMATCH', 'INVARIANT_VIOLATION')",
            name="type_valid",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'INVESTIGATING', 'RESOLVED', 'WRITTEN_OFF')", name="status_valid"
        ),
        Index("ix_reconciliation_items_run", "run_id"),
        Index("ix_reconciliation_items_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.reconciliation_runs.id"))
    type: Mapped[str] = mapped_column(String(30))
    invariant: Mapped[str | None] = mapped_column(String(10))
    subject: Mapped[str] = mapped_column(String(120))
    expected: Mapped[str] = mapped_column(String(64))
    actual: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    created_at: Mapped[datetime]
