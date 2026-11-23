"""Assemble the risk context of a money movement (security.md §8.1).

The risk module stays pure and knows nothing about transactions or users; this module, which
owns the transactions table, gathers the facts.

Two steps keep the wallet locks short (ADR-0004): ``gather_facts`` reads the slow-moving facts
(90-day median, account age, flags, limits, credential changes) **before** the wallet rows are
locked; ``build_context`` adds the velocity counts, which must be read under the lock so that
concurrent payments from one wallet are measured one after the other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.public import User, credentials_changed_at, kyc_tier_of
from app.modules.pricing.public import Subject, daily_limit
from app.modules.risk.public import RiskContext, flags_of
from app.modules.transactions.domain.state_machine import TxnStatus
from app.modules.transactions.models import Transaction
from app.modules.transactions.use_cases.common import COUNTED_STATUSES, recent_activity

SPENDING_TYPES = ("TRANSFER", "PAYMENT", "WITHDRAWAL")


@dataclass(frozen=True, slots=True)
class RiskFacts:
    median_outgoing_90d_minor: int | None
    first_time_counterparty: bool
    account_created_at: datetime | None
    flags: frozenset[str]
    credentials_changed_at: datetime | None
    daily_limit_minor: int | None


async def median_outgoing(
    session: AsyncSession, wallet_id: uuid.UUID, now: datetime, exclude: uuid.UUID
) -> int | None:
    """Median outgoing amount over 90 days (completed money only), or None without history."""
    value = await session.scalar(
        select(func.percentile_cont(0.5).within_group(Transaction.amount_minor)).where(
            Transaction.source_wallet_id == wallet_id,
            Transaction.type.in_(SPENDING_TYPES),
            Transaction.status.in_((TxnStatus.COMPLETED.value, TxnStatus.PARTIALLY_REFUNDED.value)),
            Transaction.created_at >= now - timedelta(days=90),
            Transaction.id != exclude,
        )
    )
    return None if value is None else int(value)


async def gather_facts(
    session: AsyncSession,
    txn: Transaction,
    *,
    user_id: uuid.UUID,
    wallet_id: uuid.UUID,
    now: datetime,
    counterparty_wallet_id: uuid.UUID | None = None,
) -> RiskFacts:
    """Facts that do not need the wallet lock (call before ``lock_wallets``)."""
    first_time = False
    if counterparty_wallet_id is not None:
        first_time = not await session.scalar(
            select(
                exists().where(
                    Transaction.source_wallet_id == wallet_id,
                    Transaction.destination_wallet_id == counterparty_wallet_id,
                    Transaction.status.in_(COUNTED_STATUSES),
                    Transaction.id != txn.id,
                )
            )
        )
    tier = await kyc_tier_of(session, user_id)
    return RiskFacts(
        median_outgoing_90d_minor=await median_outgoing(session, wallet_id, now, txn.id),
        first_time_counterparty=first_time,
        account_created_at=await session.scalar(select(User.created_at).where(User.id == user_id)),
        flags=await flags_of(session, user_id, now),
        credentials_changed_at=await credentials_changed_at(session, user_id),
        daily_limit_minor=await daily_limit(
            session, txn_type=txn.type, subject=Subject(user_id, tier), currency=txn.currency
        ),
    )


async def build_context(
    session: AsyncSession,
    txn: Transaction,
    facts: RiskFacts,
    *,
    user_id: uuid.UUID,
    wallet_id: uuid.UUID,
    now: datetime,
    timezone: str,
    counterparty_wallet_id: uuid.UUID | None = None,
    same_pattern_count: int = 0,
    destination_added_at: datetime | None = None,
    destination_cooldown: timedelta = timedelta(hours=1),
) -> RiskContext:
    """Call with the paying wallet locked: velocity counts are read here."""
    count_10m, amount_1h, failed_30m = await recent_activity(session, wallet_id, now)
    return RiskContext(
        user_id=user_id,
        txn_type=txn.type,
        amount_minor=txn.amount_minor,
        currency=txn.currency,
        now=now,
        counterparty_wallet_id=counterparty_wallet_id,
        outgoing_count_10m=count_10m,
        outgoing_amount_1h_minor=amount_1h,
        daily_limit_minor=facts.daily_limit_minor,
        failed_count_30m=failed_30m,
        same_amount_same_counterparty_2m=same_pattern_count,
        median_outgoing_90d_minor=facts.median_outgoing_90d_minor,
        first_time_counterparty=facts.first_time_counterparty,
        account_created_at=facts.account_created_at,
        flags=facts.flags,
        timezone=timezone,
        credentials_changed_at=facts.credentials_changed_at,
        destination_added_at=destination_added_at,
        destination_cooldown=destination_cooldown,
    )
