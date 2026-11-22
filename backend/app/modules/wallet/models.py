"""Wallet tables (database.md §3.3)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id
from app.core.models import CurrencyRecord
from app.modules.identity.public import User
from app.modules.ledger.public import LedgerAccount
from app.modules.wallet.domain import FreezeSource, WalletStatus, WalletType

# Tables referenced by foreign keys must be registered in this process's metadata too.
_FOREIGN_KEY_TARGETS = (CurrencyRecord, User, LedgerAccount)


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "currency", "wallet_type"),
        CheckConstraint(_in("status", [s.value for s in WalletStatus]), name="status_valid"),
        CheckConstraint(_in("wallet_type", [t.value for t in WalletType]), name="type_valid"),
        CheckConstraint(
            f"freeze_source IS NULL OR {_in('freeze_source', [s.value for s in FreezeSource])}",
            name="freeze_source_valid",
        ),
        CheckConstraint(
            "(status = 'FROZEN') = (freeze_source IS NOT NULL)", name="freeze_source_iff_frozen"
        ),
        CheckConstraint("wallet_number ~ '^[1-9][0-9]{9}$'", name="number_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"))
    wallet_number: Mapped[str] = mapped_column(String(10), unique=True)
    wallet_type: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3), ForeignKey("app.currencies.code"))
    status: Mapped[str] = mapped_column(String(20))
    status_reason: Mapped[str | None] = mapped_column(String(200))
    freeze_source: Mapped[str | None] = mapped_column(String(10))
    frozen_by: Mapped[uuid.UUID | None]
    frozen_at: Mapped[datetime | None]
    ledger_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app.ledger_accounts.id"), unique=True
    )
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
