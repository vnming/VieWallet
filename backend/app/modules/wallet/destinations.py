"""Bank accounts that receive withdrawals (simulated banks only).

Account numbers are stored encrypted (AES-256-GCM, associated data binds the row id) and
shown masked. At most five active accounts per user.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import Clock
from app.core.crypto import DataCipher
from app.core.db import Base
from app.core.errors import (
    ConflictError,
    FieldError,
    NotFoundError,
    ValidationFailedError,
    register_titles,
)
from app.core.ids import new_id
from app.modules.audit.public import AuditEntry, record
from app.modules.identity.public import User

_FOREIGN_KEY_TARGETS = (User,)

register_titles(
    {
        "DESTINATION_NOT_FOUND": {
            "vi": "Không tìm thấy tài khoản nhận tiền",
            "en": "Bank account not found",
        },
        "DESTINATION_LIMIT": {
            "vi": "Bạn chỉ lưu được tối đa 5 tài khoản nhận tiền",
            "en": "You can save at most 5 bank accounts",
        },
        "DESTINATION_DUPLICATE": {
            "vi": "Tài khoản này đã được lưu",
            "en": "This bank account is already saved",
        },
    }
)

MAX_ACTIVE = 5
_NUMBER_RE = re.compile(r"^[0-9]{6,20}$")

# Fictional banks of the simulation (no real bank names or codes).
BANKS: dict[str, str] = {
    "SIMBANK": "SimBank (mô phỏng)",
    "SENVANG": "Ngân hàng Mô phỏng Sen Vàng",
    "PHUONGNAM": "Ngân hàng Mô phỏng Phương Nam",
    "TRUONGSON": "Ngân hàng Mô phỏng Trường Sơn",
}


class DestinationNotFoundError(NotFoundError):
    code = "DESTINATION_NOT_FOUND"


class DestinationLimitError(ConflictError):
    code = "DESTINATION_LIMIT"


class DestinationDuplicateError(ConflictError):
    code = "DESTINATION_DUPLICATE"


class BankDestination(Base):
    __tablename__ = "bank_destinations"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'REMOVED')", name="status_valid"),
        Index("ix_bank_destinations_user", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"))
    bank_code: Mapped[str] = mapped_column(String(20))
    account_number_masked: Mapped[str] = mapped_column(String(24))
    account_number_encrypted: Mapped[str] = mapped_column(Text)
    # Keyed fingerprint so duplicates can be detected without decrypting every row.
    account_fingerprint: Mapped[str] = mapped_column(String(64))
    account_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime]
    removed_at: Mapped[datetime | None]


def mask_account(number: str) -> str:
    return "•" * 4 + number[-4:]


def _aad(destination_id: uuid.UUID) -> str:
    return f"wallet.bank_destination:{destination_id}"


async def list_destinations(session: AsyncSession, user_id: uuid.UUID) -> list[BankDestination]:
    rows = await session.scalars(
        select(BankDestination)
        .where(BankDestination.user_id == user_id, BankDestination.status == "ACTIVE")
        .order_by(BankDestination.created_at)
    )
    return list(rows.all())


async def add_destination(
    session: AsyncSession,
    clock: Clock,
    cipher: DataCipher,
    fingerprint_key: bytes,
    *,
    user_id: uuid.UUID,
    bank_code: str,
    account_number: str,
    account_name: str,
) -> BankDestination:
    errors = []
    if bank_code not in BANKS:
        errors.append(FieldError("bank_code", "UNKNOWN_BANK", "unknown bank"))
    if not _NUMBER_RE.match(account_number):
        errors.append(FieldError("account_number", "INVALID", "6-20 digits"))
    name = " ".join(account_name.split()).upper()
    if not 2 <= len(name) <= 120:  # noqa: PLR2004
        errors.append(FieldError("account_name", "INVALID", "2-120 characters"))
    if errors:
        raise ValidationFailedError(errors=errors)
    # Serialize additions per user so the five-account cap cannot be raced.
    await session.scalar(select(User.id).where(User.id == user_id).with_for_update())
    active = await list_destinations(session, user_id)
    if len(active) >= MAX_ACTIVE:
        raise DestinationLimitError()
    fingerprint = hmac.new(
        fingerprint_key, f"{bank_code}:{account_number}".encode(), hashlib.sha256
    ).hexdigest()
    if any(d.account_fingerprint == fingerprint for d in active):
        raise DestinationDuplicateError()
    destination_id = new_id()
    row = BankDestination(
        id=destination_id,
        user_id=user_id,
        bank_code=bank_code,
        account_number_masked=mask_account(account_number),
        account_number_encrypted=cipher.encrypt(
            account_number, associated_data=_aad(destination_id)
        ),
        account_fingerprint=fingerprint,
        account_name=name,
        status="ACTIVE",
        created_at=clock.now(),
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="wallet.destination_added",
            actor_type="USER",
            actor_id=user_id,
            target_type="bank_destination",
            target_id=str(row.id),
            details={"bank_code": bank_code, "account": row.account_number_masked},
        ),
    )
    return row


async def get_destination(
    session: AsyncSession, user_id: uuid.UUID, destination_id: uuid.UUID
) -> BankDestination:
    row = await session.get(BankDestination, destination_id)
    if row is None or row.user_id != user_id or row.status != "ACTIVE":
        raise DestinationNotFoundError()
    return row


def reveal_account_number(cipher: DataCipher, row: BankDestination) -> str:
    """Only for sending a payout to the provider; never returned by the API."""
    return cipher.decrypt(row.account_number_encrypted, associated_data=_aad(row.id))


async def remove_destination(
    session: AsyncSession, clock: Clock, *, user_id: uuid.UUID, destination_id: uuid.UUID
) -> None:
    row = await get_destination(session, user_id, destination_id)
    row.status = "REMOVED"
    row.removed_at = clock.now()
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="wallet.destination_removed",
            actor_type="USER",
            actor_id=user_id,
            target_type="bank_destination",
            target_id=str(row.id),
        ),
    )


async def count_active(session: AsyncSession, user_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(BankDestination)
            .where(BankDestination.user_id == user_id, BankDestination.status == "ACTIVE")
        )
        or 0
    )
