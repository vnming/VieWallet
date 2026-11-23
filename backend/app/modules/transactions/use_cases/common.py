"""Shared pieces of the money-movement use cases: context, limits usage, product rules."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import AppError, FieldError, ValidationFailedError, register_titles
from app.core.money import Money
from app.modules.identity.public import IdentityServices
from app.modules.ledger.public import balance_of
from app.modules.pricing.public import (
    LimitBreach,
    LimitCode,
    LimitDirection,
    Subject,
    Usage,
    limit_breach,
)
from app.modules.providers.public import ProviderServices
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.wallet.public import Wallet

register_titles(
    {
        LimitCode.BELOW_MINIMUM.value: {
            "vi": "Số tiền nhỏ hơn mức tối thiểu",
            "en": "Amount is below the minimum",
        },
        LimitCode.PER_TXN.value: {
            "vi": "Vượt hạn mức mỗi giao dịch",
            "en": "Per-transaction limit exceeded",
        },
        LimitCode.DAILY_AMOUNT.value: {
            "vi": "Vượt hạn mức trong ngày",
            "en": "Daily limit exceeded",
        },
        LimitCode.DAILY_COUNT.value: {
            "vi": "Vượt số giao dịch trong ngày",
            "en": "Daily transaction count exceeded",
        },
        LimitCode.MONTHLY_AMOUNT.value: {
            "vi": "Vượt hạn mức trong tháng",
            "en": "Monthly limit exceeded",
        },
        LimitCode.MONTHLY_COUNT.value: {
            "vi": "Vượt số giao dịch trong tháng",
            "en": "Monthly transaction count exceeded",
        },
        LimitCode.BALANCE_CAP.value: {
            "vi": "Số dư sẽ vượt mức tối đa của ví",
            "en": "Wallet balance cap would be exceeded",
        },
        LimitCode.NOT_ALLOWED.value: {
            "vi": "Cần xác minh danh tính (KYC) để dùng chức năng này",
            "en": "Identity verification (KYC) is required for this",
        },
        "AMOUNT_NOT_MULTIPLE": {
            "vi": "Số tiền phải là bội của 1.000 ₫",
            "en": "Amount must be a multiple of 1,000 VND",
        },
        "QUOTE_EXPIRED": {
            "vi": "Báo giá đã hết hạn, vui lòng tạo lại",
            "en": "The quote has expired, please start again",
        },
    }
)

# Statuses that count towards limits (financial-processing.md §8.2).
COUNTED_STATUSES = (
    TxnStatus.PENDING.value,
    TxnStatus.PROCESSING.value,
    TxnStatus.COMPLETED.value,
    TxnStatus.PARTIALLY_REFUNDED.value,
    TxnStatus.REFUNDED.value,
)
AMOUNT_STEP = 1_000  # deposits and withdrawals move whole thousands of dong


class LimitExceededError(AppError):
    status = 422

    def __init__(self, breach: LimitBreach) -> None:
        super().__init__(f"limit={breach.limit_minor}" if breach.limit_minor is not None else None)
        self.breach = breach

    @property
    def code(self) -> str:  # type: ignore[override]
        return self.breach.code.value


class AmountNotMultipleError(ValidationFailedError):
    code = "AMOUNT_NOT_MULTIPLE"


@dataclass(frozen=True, slots=True)
class FinanceContext:
    """Everything the money use cases need; built once per process."""

    identity: IdentityServices
    providers: ProviderServices

    @property
    def db(self) -> Database:
        return self.identity.db

    @property
    def clock(self) -> Clock:
        return self.identity.clock

    @property
    def settings(self) -> Settings:
        return self.identity.settings


def require_step(amount: Money) -> None:
    if amount.amount_minor <= 0 or amount.amount_minor % AMOUNT_STEP:
        raise AmountNotMultipleError(
            errors=[FieldError("amount", "AMOUNT_NOT_MULTIPLE", "multiple of 1000, > 0")]
        )


def _period_starts(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    local = now.astimezone(tz)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day.replace(day=1)


async def usage_for(
    session: AsyncSession,
    wallet: Wallet,
    txn_type: TxnType,
    direction: LimitDirection,
    now: datetime,
    *,
    timezone: str,
    exclude: uuid.UUID | None = None,
) -> Usage:
    day, month = _period_starts(now, ZoneInfo(timezone))
    column = (
        Transaction.source_wallet_id
        if direction is LimitDirection.OUT
        else Transaction.destination_wallet_id
    )
    base = and_(
        column == wallet.id,
        Transaction.type == txn_type.value,
        Transaction.status.in_(COUNTED_STATUSES),
        Transaction.created_at >= month,
        *([Transaction.id != exclude] if exclude else []),
    )
    is_today = Transaction.created_at >= day
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(Transaction.amount_minor).filter(is_today), 0),
                func.count().filter(is_today),
                func.coalesce(func.sum(Transaction.amount_minor), 0),
                func.count(),
            ).where(base)
        )
    ).one()
    balance = await balance_of(session, wallet.ledger_account_id)
    return Usage(
        daily_amount_minor=int(row[0]),
        daily_count=int(row[1]),
        monthly_amount_minor=int(row[2]),
        monthly_count=int(row[3]),
        balance_minor=balance.amount_minor,
    )


async def check_limits(
    session: AsyncSession,
    *,
    wallet: Wallet,
    txn_type: TxnType,
    direction: LimitDirection,
    amount: Money,
    user_id: uuid.UUID,
    kyc_tier: int,
    now: datetime,
    timezone: str,
    exclude: uuid.UUID | None = None,
) -> LimitBreach | None:
    usage = await usage_for(
        session, wallet, txn_type, direction, now, timezone=timezone, exclude=exclude
    )
    if direction is LimitDirection.OUT:
        usage = Usage(
            daily_amount_minor=usage.daily_amount_minor,
            daily_count=usage.daily_count,
            monthly_amount_minor=usage.monthly_amount_minor,
            monthly_count=usage.monthly_count,
            balance_minor=0,  # balance caps apply to money coming in only
        )
    return await limit_breach(
        session,
        txn_type=txn_type.value,
        direction=direction,
        amount=amount,
        subject=Subject(user_id, kyc_tier=kyc_tier),
        usage=usage,
    )


async def recent_activity(
    session: AsyncSession, wallet_id: uuid.UUID, now: datetime
) -> tuple[int, int, int]:
    """(outgoing count 10 min, outgoing amount 1 h, failed count 30 min) for risk rules."""
    out = Transaction.source_wallet_id == wallet_id
    counted = Transaction.status.in_(COUNTED_STATUSES)
    row = (
        await session.execute(
            select(
                func.count().filter(counted, Transaction.created_at >= now - timedelta(minutes=10)),
                func.coalesce(
                    func.sum(Transaction.amount_minor).filter(
                        counted, Transaction.created_at >= now - timedelta(hours=1)
                    ),
                    0,
                ),
                func.count().filter(
                    Transaction.status == TxnStatus.FAILED.value,
                    Transaction.created_at >= now - timedelta(minutes=30),
                ),
            ).where(out, Transaction.created_at >= now - timedelta(hours=1))
        )
    ).one()
    return int(row[0]), int(row[1]), int(row[2])
