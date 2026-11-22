"""Load active fee/limit rules and seed the defaults (financial-processing.md §8)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.ids import new_id
from app.core.money import Money
from app.modules.pricing.domain import (
    FeeQuote,
    FeeRuleSpec,
    LimitBreach,
    LimitDirection,
    LimitPeriod,
    LimitRuleSpec,
    RuleScope,
    Subject,
    Usage,
    check_limits,
    quote_fee,
)
from app.modules.pricing.models import FeeRule, LimitRule

ACTIVE = "ACTIVE"


async def active_fee_rules(session: AsyncSession, clock: Clock, currency: str) -> list[FeeRuleSpec]:
    now = clock.now()
    rows = await session.scalars(
        select(FeeRule).where(
            FeeRule.currency == currency,
            FeeRule.status == ACTIVE,
            FeeRule.effective_from <= now,
            or_(FeeRule.effective_to.is_(None), FeeRule.effective_to > now),
        )
    )
    return [
        FeeRuleSpec(
            id=r.id,
            version=r.version,
            txn_type=r.txn_type,
            currency=r.currency,
            scope=RuleScope(r.scope),
            scope_value=r.scope_value,
            fixed_minor=r.fixed_minor,
            bps=r.bps,
            min_fee_minor=r.min_fee_minor,
            max_fee_minor=r.max_fee_minor,
        )
        for r in rows.all()
    ]


async def active_limit_rules(session: AsyncSession, currency: str) -> list[LimitRuleSpec]:
    rows = await session.scalars(
        select(LimitRule).where(LimitRule.currency == currency, LimitRule.status == ACTIVE)
    )
    return [
        LimitRuleSpec(
            id=r.id,
            txn_type=r.txn_type,
            direction=LimitDirection(r.direction),
            scope=RuleScope(r.scope),
            scope_value=r.scope_value,
            period=LimitPeriod(r.period),
            currency=r.currency,
            max_amount_minor=r.max_amount_minor,
            max_count=r.max_count,
            min_amount_minor=r.min_amount_minor,
        )
        for r in rows.all()
    ]


async def fee_for(
    session: AsyncSession, clock: Clock, *, txn_type: str, amount: Money, subject: Subject
) -> FeeQuote:
    rules = await active_fee_rules(session, clock, amount.currency)
    return quote_fee(rules, txn_type, amount, subject)


async def limit_breach(
    session: AsyncSession,
    *,
    txn_type: str,
    direction: LimitDirection,
    amount: Money,
    subject: Subject,
    usage: Usage,
) -> LimitBreach | None:
    rules = await active_limit_rules(session, amount.currency)
    return check_limits(
        rules, txn_type=txn_type, direction=direction, amount=amount, subject=subject, usage=usage
    )


# ---------------------------------------------------------------- seed

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

DEFAULT_FEES: list[dict[str, Any]] = [
    {"code": "FEE_DEPOSIT_VND", "txn_type": "DEPOSIT", "fixed_minor": 0},
    {"code": "FEE_TRANSFER_VND", "txn_type": "TRANSFER", "fixed_minor": 0},
    {"code": "FEE_PAYMENT_VND", "txn_type": "PAYMENT", "fixed_minor": 0},
    {"code": "FEE_WITHDRAWAL_VND", "txn_type": "WITHDRAWAL", "fixed_minor": 1_100},
]

M = 1_000_000
# (txn_type, direction, period, tier0 max, tier1 max)
_LIMIT_TABLE: list[tuple[str, str, str, int, int]] = [
    ("ALL", "IN", "BALANCE_CAP", 10 * M, 100 * M),
    ("DEPOSIT", "IN", "PER_TXN", 5 * M, 50 * M),
    ("DEPOSIT", "IN", "DAILY", 10 * M, 100 * M),
    ("TRANSFER", "OUT", "PER_TXN", 2 * M, 50 * M),
    ("TRANSFER", "OUT", "DAILY", 5 * M, 100 * M),
    ("TRANSFER", "OUT", "MONTHLY", 20 * M, 500 * M),
    ("PAYMENT", "OUT", "PER_TXN", 2 * M, 20 * M),
    ("PAYMENT", "OUT", "DAILY", 5 * M, 100 * M),
    ("WITHDRAWAL", "OUT", "PER_TXN", 0, 50 * M),  # 0 = not allowed for tier 0
    ("WITHDRAWAL", "OUT", "DAILY", 0, 100 * M),
]


def default_limits() -> list[dict[str, Any]]:
    rows = []
    for txn_type, direction, period, tier0, tier1 in _LIMIT_TABLE:
        for tier, cap in (("0", tier0), ("1", tier1)):
            rows.append(
                {
                    "code": f"LIMIT_{txn_type}_{direction}_{period}_T{tier}_VND",
                    "txn_type": txn_type,
                    "direction": direction,
                    "scope": RuleScope.KYC_TIER.value,
                    "scope_value": tier,
                    "period": period,
                    "max_amount_minor": cap,
                }
            )
    return rows


async def seed_defaults(session: AsyncSession) -> None:
    """Insert missing default rules; never overwrites rules changed later (by approval)."""
    for fee in DEFAULT_FEES:
        await session.execute(
            insert(FeeRule)
            .values(
                id=new_id(),
                currency="VND",
                scope=RuleScope.DEFAULT.value,
                scope_value=None,
                bps=0,
                min_fee_minor=0,
                max_fee_minor=None,
                effective_from=_EPOCH,
                version=1,
                status=ACTIVE,
                **fee,
            )
            .on_conflict_do_nothing(index_elements=["code"])
        )
    for limit in default_limits():
        await session.execute(
            insert(LimitRule)
            .values(id=new_id(), currency="VND", version=1, status=ACTIVE, **limit)
            .on_conflict_do_nothing(index_elements=["code"])
        )
