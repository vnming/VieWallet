"""Fee and limit rule tables (database.md §3.7). Read-only for the application role."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id
from app.modules.pricing.domain import LimitDirection, LimitPeriod, RuleScope

RULE_STATUSES = ("DRAFT", "ACTIVE", "RETIRED")


def _in(column: str, values: list[str] | tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class FeeRule(Base):
    __tablename__ = "fee_rules"
    __table_args__ = (
        CheckConstraint(_in("scope", [s.value for s in RuleScope]), name="scope_valid"),
        CheckConstraint(_in("status", RULE_STATUSES), name="status_valid"),
        CheckConstraint(
            "fixed_minor >= 0 AND bps >= 0 AND min_fee_minor >= 0", name="non_negative"
        ),
        CheckConstraint(
            "max_fee_minor IS NULL OR max_fee_minor >= min_fee_minor", name="max_at_least_min"
        ),
        CheckConstraint(
            "(scope = 'DEFAULT') = (scope_value IS NULL)", name="scope_value_iff_scoped"
        ),
        Index("ix_fee_rules_lookup", "txn_type", "currency", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    txn_type: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    scope: Mapped[str] = mapped_column(String(10))
    scope_value: Mapped[str | None] = mapped_column(String(64))
    fixed_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    bps: Mapped[int] = mapped_column(Integer, default=0)
    min_fee_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    max_fee_minor: Mapped[int | None] = mapped_column(BigInteger)
    effective_from: Mapped[datetime]
    effective_to: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(10))


class LimitRule(Base):
    __tablename__ = "limit_rules"
    __table_args__ = (
        CheckConstraint(_in("scope", [s.value for s in RuleScope]), name="scope_valid"),
        CheckConstraint(_in("status", RULE_STATUSES), name="status_valid"),
        CheckConstraint(_in("period", [p.value for p in LimitPeriod]), name="period_valid"),
        CheckConstraint(
            _in("direction", [d.value for d in LimitDirection]), name="direction_valid"
        ),
        CheckConstraint(
            "(max_amount_minor IS NULL OR max_amount_minor >= 0)"
            " AND (max_count IS NULL OR max_count >= 0)"
            " AND (min_amount_minor IS NULL OR min_amount_minor >= 0)",
            name="non_negative",
        ),
        CheckConstraint(
            "(scope = 'DEFAULT') = (scope_value IS NULL)", name="scope_value_iff_scoped"
        ),
        Index("ix_limit_rules_lookup", "currency", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    txn_type: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(3))
    scope: Mapped[str] = mapped_column(String(10))
    scope_value: Mapped[str | None] = mapped_column(String(64))
    period: Mapped[str] = mapped_column(String(12))
    currency: Mapped[str] = mapped_column(String(3))
    max_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    max_count: Mapped[int | None]
    min_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(10))
