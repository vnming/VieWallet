"""Ledger tables (database.md §3.3). Journals and postings are append-only."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id
from app.core.models import CurrencyRecord
from app.modules.ledger.domain import AccountCode, AccountType, JournalKind, Side

# Tables referenced by foreign keys must be registered in this process's metadata too.
_FOREIGN_KEY_TARGETS = (CurrencyRecord,)


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        CheckConstraint(_in("code", [c.value for c in AccountCode]), name="code_valid"),
        CheckConstraint(_in("type", [t.value for t in AccountType]), name="type_valid"),
        CheckConstraint(_in("normal_side", [s.value for s in Side]), name="normal_side_valid"),
        UniqueConstraint("code", "currency", "owner_ref", postgresql_nulls_not_distinct=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(30))
    type: Mapped[str] = mapped_column(String(20))
    normal_side: Mapped[str] = mapped_column(String(10))
    currency: Mapped[str] = mapped_column(String(3), ForeignKey("app.currencies.code"))
    owner_ref: Mapped[str | None] = mapped_column(String(64))
    allow_negative: Mapped[bool] = mapped_column(Boolean)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime]


class AccountBalance(Base):
    """Balance projection, updated in the same transaction as the postings (ADR-0002)."""

    __tablename__ = "account_balances"
    __table_args__ = (CheckConstraint("allow_negative OR balance_minor >= 0", name="non_negative"),)

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app.ledger_accounts.id"), primary_key=True
    )
    currency: Mapped[str] = mapped_column(String(3))
    balance_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    allow_negative: Mapped[bool] = mapped_column(Boolean)
    last_journal_id: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime]


class Journal(Base):
    __tablename__ = "journals"
    __table_args__ = (
        CheckConstraint(_in("kind", [k.value for k in JournalKind]), name="kind_valid"),
        Index("ix_journals_transaction_id", "transaction_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    transaction_id: Mapped[uuid.UUID | None]
    kind: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3))
    description: Mapped[str] = mapped_column(String(200), default="")
    reverses_journal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app.journals.id"), unique=True
    )
    posted_at: Mapped[datetime]
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(64))


class Posting(Base):
    __tablename__ = "postings"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint(_in("side", [s.value for s in Side]), name="side_valid"),
        UniqueConstraint("journal_id", "seq"),
        Index("ix_postings_account_journal", "account_id", "journal_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    journal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.journals.id"))
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.ledger_accounts.id"))
    side: Mapped[str] = mapped_column(String(10))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    seq: Mapped[int] = mapped_column(SmallInteger)
