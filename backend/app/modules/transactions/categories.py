"""Transaction categories (PRD FR-TXN-07): a default per side of a transaction, which the
customer may override for themselves.

One transaction has two sides (the payer and the receiver), so the category is per user:
``transaction_categories`` stores overrides keyed by (user, transaction); everything else is
derived from the type, the direction and the merchant category code (MCC).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ForeignKey, String, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.errors import FieldError, ValidationFailedError
from app.modules.identity.public import User
from app.modules.transactions.models import Transaction

# Tables referenced by foreign keys must be registered in this process's metadata too.
_FOREIGN_KEY_TARGETS = (User,)


@dataclass(frozen=True, slots=True)
class Category:
    code: str
    vi: str
    en: str


CATEGORIES: tuple[Category, ...] = (
    Category("FOOD", "Ăn uống", "Food & drink"),
    Category("SHOPPING", "Mua sắm", "Shopping"),
    Category("TRANSPORT", "Đi lại", "Transport"),
    Category("BILLS", "Hóa đơn", "Bills"),
    Category("ENTERTAINMENT", "Giải trí", "Entertainment"),
    Category("HEALTH", "Sức khỏe", "Health"),
    Category("EDUCATION", "Học tập", "Education"),
    Category("TRANSFER_OUT", "Chuyển tiền đi", "Money sent"),
    Category("CASH_OUT", "Rút tiền", "Cash out"),
    Category("TOP_UP", "Nạp tiền", "Top-up"),
    Category("INCOME", "Tiền nhận", "Money received"),
    Category("REFUND", "Hoàn tiền", "Refund"),
    Category("OTHER", "Khác", "Other"),
)
CODES = frozenset(c.code for c in CATEGORIES)

# Merchant category codes of the simulated shops (ISO 18245 subset).
MCC_CATEGORY = {
    "5812": "FOOD",
    "5813": "FOOD",
    "5814": "FOOD",
    "5411": "SHOPPING",
    "5311": "SHOPPING",
    "5651": "SHOPPING",
    "4111": "TRANSPORT",
    "4121": "TRANSPORT",
    "5541": "TRANSPORT",
    "4900": "BILLS",
    "4814": "BILLS",
    "7832": "ENTERTAINMENT",
    "7999": "ENTERTAINMENT",
    "5912": "HEALTH",
    "8062": "HEALTH",
    "8220": "EDUCATION",
    "8299": "EDUCATION",
}


class TransactionCategory(Base):
    """A customer's own category for their side of one transaction."""

    __tablename__ = "transaction_categories"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"), primary_key=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app.transactions.id"), primary_key=True
    )
    category_code: Mapped[str] = mapped_column(String(20))
    updated_at: Mapped[datetime]


# (type, outgoing) -> category; PAYMENT out is resolved from the merchant category code.
_DEFAULTS = {
    ("PAYMENT", False): "INCOME",
    ("TRANSFER", True): "TRANSFER_OUT",
    ("TRANSFER", False): "INCOME",
    ("WITHDRAWAL", True): "CASH_OUT",
    ("DEPOSIT", False): "TOP_UP",
    ("REFUND", False): "REFUND",
}


def default_category(txn: Transaction, outgoing: bool) -> str:
    if txn.type == "PAYMENT" and outgoing:
        return MCC_CATEGORY.get(str(txn.extra.get("mcc", "")), "SHOPPING")
    return _DEFAULTS.get((txn.type, outgoing), "OTHER")


async def overrides_for(
    session: AsyncSession, user_id: uuid.UUID, transaction_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not transaction_ids:
        return {}
    rows = await session.execute(
        select(TransactionCategory.transaction_id, TransactionCategory.category_code).where(
            TransactionCategory.user_id == user_id,
            TransactionCategory.transaction_id.in_(transaction_ids),
        )
    )
    return {row[0]: row[1] for row in rows.all()}


async def set_category(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID,
    code: str,
    now: datetime,
) -> None:
    if code not in CODES:
        raise ValidationFailedError(
            errors=[FieldError("category_code", "UNKNOWN_CATEGORY", "not a category")]
        )
    stmt = insert(TransactionCategory).values(
        user_id=user_id, transaction_id=transaction_id, category_code=code, updated_at=now
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", "transaction_id"],
            set_={"category_code": code, "updated_at": now},
        )
    )
