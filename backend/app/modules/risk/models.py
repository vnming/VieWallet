"""Risk tables: assessments (append-only), rule versions, account flags, alerts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id

RULE_STATUSES = ("PENDING", "ACTIVE", "RETIRED", "REJECTED")
ALERT_STATUSES = ("OPEN", "ACKNOWLEDGED", "RESOLVED", "FALSE_POSITIVE")
ALERT_SEVERITIES = ("LOW", "MEDIUM", "HIGH")
FLAGS = ("WATCHLIST", "CHARGEBACK_HISTORY", "DUPLICATE_DOCUMENT")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (
        CheckConstraint("decision IN ('ALLOW', 'REVIEW', 'BLOCK')", name="decision_valid"),
        CheckConstraint("score BETWEEN 0 AND 100", name="score_range"),
        Index("ix_risk_assessments_transaction", "transaction_id"),
        Index("ix_risk_assessments_user_at", "user_id", "at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    transaction_id: Mapped[uuid.UUID | None]
    user_id: Mapped[uuid.UUID]
    score: Mapped[int] = mapped_column(SmallInteger)
    decision: Mapped[str] = mapped_column(String(10))
    matched_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    rules_version: Mapped[str] = mapped_column(String(30))
    at: Mapped[datetime]


class RiskRule(Base):
    """One version of one rule. Exactly one ACTIVE version per code (partial unique index)."""

    __tablename__ = "risk_rules"
    __table_args__ = (
        UniqueConstraint("code", "version"),
        CheckConstraint(_in("status", RULE_STATUSES), name="status_valid"),
        CheckConstraint("score BETWEEN 0 AND 100", name="score_range"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_risk_rules_active_code",
            "code",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(40))
    version: Mapped[int]
    description: Mapped[str] = mapped_column(String(200), default="")
    score: Mapped[int] = mapped_column(SmallInteger)
    block: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(10))
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime]
    activated_at: Mapped[datetime | None]


class AccountRiskFlag(Base):
    __tablename__ = "account_risk_flags"
    __table_args__ = (
        CheckConstraint(_in("flag", FLAGS), name="flag_valid"),
        Index(
            "uq_account_risk_flags_active",
            "user_id",
            "flag",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID]
    flag: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(String(300))
    set_by: Mapped[uuid.UUID | None]  # None: set by the system
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime | None]
    removed_at: Mapped[datetime | None]
    removed_by: Mapped[uuid.UUID | None]


class RiskAlert(Base):
    __tablename__ = "risk_alerts"
    __table_args__ = (
        CheckConstraint(_in("status", ALERT_STATUSES), name="status_valid"),
        CheckConstraint(_in("severity", ALERT_SEVERITIES), name="severity_valid"),
        # One alert per detected pattern (the handler may see the same event twice).
        UniqueConstraint("dedup_key"),
        Index("ix_risk_alerts_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID]
    transaction_id: Mapped[uuid.UUID | None]
    assessment_id: Mapped[uuid.UUID | None]
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    dedup_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(15))
    assigned_to: Mapped[uuid.UUID | None]
    resolution_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]
