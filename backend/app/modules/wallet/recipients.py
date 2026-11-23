"""Recipient lookup (masked names, rate limited) and saved recipients (api-spec.md §5).

A lookup answers "who would receive this?" with a masked name only, so the transfer screen
can show "NGUYEN V*** A" for confirmation without turning the endpoint into a directory.
"""

from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import Clock
from app.core.db import Base
from app.core.errors import AppError, NotFoundError, register_titles
from app.core.ids import new_id
from app.core.rate_limit import RateLimit
from app.modules.identity.public import User
from app.modules.wallet.domain import WalletStatus, WalletType, mask_wallet_number
from app.modules.wallet.models import Wallet
from app.modules.wallet.service import get_by_number

register_titles(
    {
        "RECIPIENT_NOT_FOUND": {
            "vi": "Không tìm thấy người nhận",
            "en": "Recipient not found",
        },
        "RECIPIENT_IS_SELF": {
            "vi": "Không thể chuyển tiền cho chính mình",
            "en": "You cannot send money to yourself",
        },
        "RECIPIENT_LIMIT_REACHED": {
            "vi": "Đã lưu tối đa 50 người nhận",
            "en": "You can save at most 50 recipients",
        },
    }
)

# Anti-enumeration (security.md §2.3): a person looks up a handful of recipients, not lists.
LOOKUP_PER_USER = RateLimit("recipient_lookup_user", 20, timedelta(minutes=10))
LOOKUP_PER_USER_DAY = RateLimit("recipient_lookup_user_day", 100, timedelta(days=1))
MAX_SAVED = 50


class RecipientNotFoundError(NotFoundError):
    code = "RECIPIENT_NOT_FOUND"


class RecipientIsSelfError(AppError):
    code = "RECIPIENT_IS_SELF"
    status = 422


class RecipientLimitError(AppError):
    code = "RECIPIENT_LIMIT_REACHED"
    status = 422


class SavedRecipient(Base):
    __tablename__ = "recipients"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "wallet_id"),
        Index("ix_recipients_owner_used", "owner_user_id", "last_used_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"))
    wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.wallets.id"))
    nickname: Mapped[str | None] = mapped_column(String(40))
    last_used_at: Mapped[datetime | None]
    created_at: Mapped[datetime]


def _plain(text: str) -> str:
    """Upper-case ASCII ("Nguyễn Văn An" -> "NGUYEN VAN AN"), as on Vietnamese bank screens."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def mask_name(full_name: str) -> str:
    """First word in full, middle words as initial + ***, last word as its initial."""
    words = _plain(full_name).split()
    if not words:
        return "***"
    if len(words) == 1:
        return f"{words[0][0]}***"
    middle = [f"{w[0]}***" for w in words[1:-1]]
    return " ".join([words[0], *middle, words[-1][0]])


@dataclass(frozen=True, slots=True)
class RecipientView:
    wallet_id: uuid.UUID
    wallet_number_masked: str
    display_name: str  # masked for people, the verified business name for merchants
    kind: str  # USER or MERCHANT
    active: bool


def _view(wallet: Wallet, owner: User, merchant_name: str | None) -> RecipientView:
    is_merchant = wallet.wallet_type == WalletType.MERCHANT.value
    return RecipientView(
        wallet_id=wallet.id,
        wallet_number_masked=mask_wallet_number(wallet.wallet_number),
        display_name=(merchant_name or owner.full_name)
        if is_merchant
        else mask_name(owner.full_name),
        kind="MERCHANT" if is_merchant else "USER",
        active=wallet.status == WalletStatus.ACTIVE.value and owner.status == "ACTIVE",
    )


async def describe_wallet(
    session: AsyncSession, wallet: Wallet, merchant_name: str | None = None
) -> RecipientView:
    owner = await session.get(User, wallet.owner_user_id)
    if owner is None:  # pragma: no cover - FK
        raise RecipientNotFoundError()
    return _view(wallet, owner, merchant_name)


async def find_recipient(
    session: AsyncSession,
    *,
    requester_id: uuid.UUID,
    wallet_number: str | None = None,
    email: str | None = None,
) -> Wallet:
    """The receiving wallet for a number or an e-mail (customers' main wallets only)."""
    wallet: Wallet | None
    if wallet_number is not None:
        try:
            wallet = await get_by_number(session, wallet_number)
        except NotFoundError as exc:
            raise RecipientNotFoundError() from exc
        if wallet.wallet_type != WalletType.MAIN.value:
            raise RecipientNotFoundError()  # merchants are paid through payment requests
    elif email is not None:
        wallet = await session.scalar(
            select(Wallet)
            .join(User, User.id == Wallet.owner_user_id)
            .where(
                func.lower(User.email) == email.strip().lower(),
                User.user_type == "CUSTOMER",
                Wallet.wallet_type == WalletType.MAIN.value,
                Wallet.currency == "VND",
            )
        )
        if wallet is None:
            raise RecipientNotFoundError()
    else:
        raise RecipientNotFoundError()
    if wallet.owner_user_id == requester_id:
        raise RecipientIsSelfError()
    return wallet


# ---------------------------------------------------------------- saved recipients


@dataclass(frozen=True, slots=True)
class SavedRecipientView:
    id: uuid.UUID
    nickname: str | None
    last_used_at: datetime | None
    recipient: RecipientView


async def list_saved(session: AsyncSession, owner_user_id: uuid.UUID) -> list[SavedRecipientView]:
    rows = (
        await session.execute(
            select(SavedRecipient, Wallet, User)
            .join(Wallet, Wallet.id == SavedRecipient.wallet_id)
            .join(User, User.id == Wallet.owner_user_id)
            .where(SavedRecipient.owner_user_id == owner_user_id)
            .order_by(
                SavedRecipient.last_used_at.desc().nulls_last(), SavedRecipient.created_at.desc()
            )
        )
    ).all()
    return [
        SavedRecipientView(r.id, r.nickname, r.last_used_at, _view(w, u, None)) for r, w, u in rows
    ]


async def save_recipient(
    session: AsyncSession,
    clock: Clock,
    *,
    owner_user_id: uuid.UUID,
    wallet: Wallet,
    nickname: str | None,
) -> SavedRecipient:
    existing = await session.scalar(
        select(SavedRecipient).where(
            SavedRecipient.owner_user_id == owner_user_id, SavedRecipient.wallet_id == wallet.id
        )
    )
    if existing is not None:
        if nickname is not None:
            existing.nickname = nickname.strip()[:40] or None
        return existing
    count = await session.scalar(
        select(func.count())
        .select_from(SavedRecipient)
        .where(SavedRecipient.owner_user_id == owner_user_id)
    )
    if int(count or 0) >= MAX_SAVED:
        raise RecipientLimitError()
    row = SavedRecipient(
        id=new_id(),
        owner_user_id=owner_user_id,
        wallet_id=wallet.id,
        nickname=(nickname or "").strip()[:40] or None,
        last_used_at=None,
        created_at=clock.now(),
    )
    session.add(row)
    await session.flush()
    return row


async def get_saved(
    session: AsyncSession, owner_user_id: uuid.UUID, recipient_id: uuid.UUID
) -> SavedRecipient:
    row = await session.get(SavedRecipient, recipient_id)
    if row is None or row.owner_user_id != owner_user_id:
        raise RecipientNotFoundError()  # someone else's: 404, not 403
    return row


async def rename_saved(
    session: AsyncSession, owner_user_id: uuid.UUID, recipient_id: uuid.UUID, nickname: str | None
) -> SavedRecipient:
    row = await get_saved(session, owner_user_id, recipient_id)
    row.nickname = (nickname or "").strip()[:40] or None
    await session.flush()
    return row


async def delete_saved(
    session: AsyncSession, owner_user_id: uuid.UUID, recipient_id: uuid.UUID
) -> None:
    row = await get_saved(session, owner_user_id, recipient_id)
    await session.execute(delete(SavedRecipient).where(SavedRecipient.id == row.id))


async def touch_saved(
    session: AsyncSession, clock: Clock, owner_user_id: uuid.UUID, wallet_id: uuid.UUID
) -> None:
    row = await session.scalar(
        select(SavedRecipient).where(
            SavedRecipient.owner_user_id == owner_user_id, SavedRecipient.wallet_id == wallet_id
        )
    )
    if row is not None:
        row.last_used_at = clock.now()
