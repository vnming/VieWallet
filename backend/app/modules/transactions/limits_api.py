"""Remaining limits of the caller's wallet (api-spec.md §5, `GET /wallet/limits`).

Computed from the same rules and usage the money flows check, so what the screen shows is what
a transfer would be measured against (financial-processing.md §8.2).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.modules.identity.public import CustomerPrincipal, kyc_tier_of
from app.modules.ledger.public import balance_of
from app.modules.pricing.public import LimitDirection, LimitPeriod, Subject, effective_limits
from app.modules.transactions.domain.state_machine import TxnType
from app.modules.transactions.money_api import FinanceDep
from app.modules.transactions.use_cases.common import usage_for
from app.modules.wallet.public import ensure_wallet

router = APIRouter(prefix="/api/v1/wallet", tags=["wallet"])

TYPES: tuple[tuple[TxnType, LimitDirection], ...] = (
    (TxnType.TRANSFER, LimitDirection.OUT),
    (TxnType.PAYMENT, LimitDirection.OUT),
    (TxnType.WITHDRAWAL, LimitDirection.OUT),
    (TxnType.DEPOSIT, LimitDirection.IN),
)


class LimitOut(BaseModel):
    period: Literal["PER_TXN", "DAILY", "MONTHLY"]
    max_amount: str | None = Field(description="Integer minor units; null = no amount limit")
    used_amount: str
    remaining_amount: str | None
    max_count: int | None
    used_count: int


class TypeLimitsOut(BaseModel):
    type: TxnType
    allowed: bool = Field(description="False when the tier forbids this type (e.g. withdrawals)")
    limits: list[LimitOut]


class LimitsOut(BaseModel):
    kyc_tier: int
    currency: str
    balance_cap: str | None
    balance: str
    types: list[TypeLimitsOut]


@router.get("/limits", response_model=LimitsOut)
async def my_limits(principal: CustomerPrincipal, ctx: FinanceDep) -> LimitsOut:
    async with ctx.db.transaction() as session:
        wallet = await ensure_wallet(session, ctx.clock, principal.user_id)
        tier = await kyc_tier_of(session, principal.user_id)
        subject = Subject(principal.user_id, tier)
        now = ctx.clock.now()
        cap: int | None = None
        types: list[TypeLimitsOut] = []
        for txn_type, direction in TYPES:
            rules = await effective_limits(
                session,
                txn_type=txn_type.value,
                direction=direction,
                subject=subject,
                currency=wallet.currency,
            )
            usage = await usage_for(
                session, wallet, txn_type, direction, now, timezone=ctx.settings.business_timezone
            )
            limits: list[LimitOut] = []
            allowed = True
            for rule in sorted(rules, key=lambda r: r.period.value):
                if rule.period is LimitPeriod.BALANCE_CAP:
                    cap = rule.max_amount_minor
                    continue
                if rule.period is LimitPeriod.PER_TXN and rule.max_amount_minor == 0:
                    allowed = False
                used_amount, used_count = {
                    LimitPeriod.PER_TXN: (0, 0),
                    LimitPeriod.DAILY: (usage.daily_amount_minor, usage.daily_count),
                    LimitPeriod.MONTHLY: (usage.monthly_amount_minor, usage.monthly_count),
                }[rule.period]
                maximum = rule.max_amount_minor
                limits.append(
                    LimitOut(
                        period=rule.period.value,  # type: ignore[arg-type]
                        max_amount=None if maximum is None else str(maximum),
                        used_amount=str(used_amount),
                        remaining_amount=None
                        if maximum is None
                        else str(max(maximum - used_amount, 0)),
                        max_count=rule.max_count,
                        used_count=used_count,
                    )
                )
            types.append(TypeLimitsOut(type=txn_type, allowed=allowed, limits=limits))
        balance = await balance_of(session, wallet.ledger_account_id)
    return LimitsOut(
        kyc_tier=tier,
        currency=wallet.currency,
        balance_cap=None if cap is None else str(cap),
        balance=str(balance.amount_minor),
        types=types,
    )
