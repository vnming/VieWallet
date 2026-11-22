"""Fee and limit rules (financial-processing.md §8). Pure integer arithmetic, no I/O."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.core.money import Money, basis_points_of, clamp


class RuleScope(StrEnum):
    DEFAULT = "DEFAULT"
    KYC_TIER = "KYC_TIER"
    USER = "USER"


# More specific scopes win (USER > KYC_TIER > DEFAULT).
SCOPE_PRIORITY = {RuleScope.USER: 3, RuleScope.KYC_TIER: 2, RuleScope.DEFAULT: 1}


class LimitPeriod(StrEnum):
    PER_TXN = "PER_TXN"
    DAILY = "DAILY"
    MONTHLY = "MONTHLY"
    BALANCE_CAP = "BALANCE_CAP"


class LimitDirection(StrEnum):
    OUT = "OUT"
    IN = "IN"


ALL_TYPES = "ALL"


@dataclass(frozen=True, slots=True)
class FeeRuleSpec:
    id: uuid.UUID
    version: int
    txn_type: str
    currency: str
    scope: RuleScope
    scope_value: str | None
    fixed_minor: int
    bps: int
    min_fee_minor: int
    max_fee_minor: int | None


@dataclass(frozen=True, slots=True)
class FeeQuote:
    fee: Money
    rule_id: uuid.UUID | None
    rule_version: int | None
    breakdown: dict[str, str]


@dataclass(frozen=True, slots=True)
class Subject:
    """Who is transacting, for rule matching."""

    user_id: uuid.UUID
    kyc_tier: int = 0


def _matches(scope: RuleScope, scope_value: str | None, subject: Subject) -> bool:
    if scope is RuleScope.DEFAULT:
        return True
    if scope is RuleScope.KYC_TIER:
        return scope_value == str(subject.kyc_tier)
    return scope_value == str(subject.user_id)


def compute_fee(rule: FeeRuleSpec, amount: Money) -> Money:
    """``clamp(fixed + round_half_up(amount * bps / 10_000), min, max)``."""
    if amount.currency != rule.currency:
        raise ValueError("rule currency mismatch")
    variable = basis_points_of(amount, rule.bps)
    raw = Money(rule.fixed_minor, amount.currency) + variable
    maximum = Money(rule.max_fee_minor, amount.currency) if rule.max_fee_minor is not None else None
    return clamp(raw, Money(rule.min_fee_minor, amount.currency), maximum)


def select_fee_rule(
    rules: Iterable[FeeRuleSpec], txn_type: str, subject: Subject
) -> FeeRuleSpec | None:
    candidates = [
        r for r in rules if r.txn_type == txn_type and _matches(r.scope, r.scope_value, subject)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (SCOPE_PRIORITY[r.scope], r.version))


def quote_fee(
    rules: Iterable[FeeRuleSpec], txn_type: str, amount: Money, subject: Subject
) -> FeeQuote:
    rule = select_fee_rule(rules, txn_type, subject)
    if rule is None:
        return FeeQuote(Money.zero(amount.currency), None, None, {"rule": "none"})
    fee = compute_fee(rule, amount)
    return FeeQuote(
        fee,
        rule.id,
        rule.version,
        {
            "fixed": str(rule.fixed_minor),
            "bps": str(rule.bps),
            "min": str(rule.min_fee_minor),
            "max": "" if rule.max_fee_minor is None else str(rule.max_fee_minor),
        },
    )


# ---------------------------------------------------------------- limits


@dataclass(frozen=True, slots=True)
class LimitRuleSpec:
    id: uuid.UUID
    txn_type: str  # a TxnType value or ALL
    direction: LimitDirection
    scope: RuleScope
    scope_value: str | None
    period: LimitPeriod
    currency: str
    max_amount_minor: int | None
    max_count: int | None
    min_amount_minor: int | None


@dataclass(frozen=True, slots=True)
class Usage:
    """Amount and count already used in the current day/month (from transactions)."""

    daily_amount_minor: int = 0
    daily_count: int = 0
    monthly_amount_minor: int = 0
    monthly_count: int = 0
    balance_minor: int = 0


class LimitCode(StrEnum):
    BELOW_MINIMUM = "AMOUNT_BELOW_MINIMUM"
    PER_TXN = "LIMIT_PER_TXN_EXCEEDED"
    DAILY_AMOUNT = "LIMIT_DAILY_AMOUNT_EXCEEDED"
    DAILY_COUNT = "LIMIT_DAILY_COUNT_EXCEEDED"
    MONTHLY_AMOUNT = "LIMIT_MONTHLY_AMOUNT_EXCEEDED"
    MONTHLY_COUNT = "LIMIT_MONTHLY_COUNT_EXCEEDED"
    BALANCE_CAP = "BALANCE_CAP_EXCEEDED"
    NOT_ALLOWED = "TXN_TYPE_NOT_ALLOWED"


@dataclass(frozen=True, slots=True)
class LimitBreach:
    code: LimitCode
    rule_id: uuid.UUID
    limit_minor: int | None


def effective_limit_rules(
    rules: Sequence[LimitRuleSpec],
    txn_type: str,
    direction: LimitDirection,
    subject: Subject,
) -> list[LimitRuleSpec]:
    """Per (type, period) keep only the most specific matching rule."""
    best: dict[tuple[str, LimitPeriod], LimitRuleSpec] = {}
    for rule in rules:
        if rule.direction is not direction or rule.txn_type not in (txn_type, ALL_TYPES):
            continue
        if not _matches(rule.scope, rule.scope_value, subject):
            continue
        key = (rule.txn_type, rule.period)
        current = best.get(key)
        if current is None or SCOPE_PRIORITY[rule.scope] > SCOPE_PRIORITY[current.scope]:
            best[key] = rule
    return list(best.values())


def _per_txn_breach(rule: LimitRuleSpec, amount: int) -> LimitBreach | None:
    cap = rule.max_amount_minor
    if cap == 0:
        return LimitBreach(LimitCode.NOT_ALLOWED, rule.id, 0)
    if rule.min_amount_minor is not None and amount < rule.min_amount_minor:
        return LimitBreach(LimitCode.BELOW_MINIMUM, rule.id, rule.min_amount_minor)
    if cap is not None and amount > cap:
        return LimitBreach(LimitCode.PER_TXN, rule.id, cap)
    return None


def _period_breach(
    rule: LimitRuleSpec,
    amount: int,
    used_amount: int,
    used_count: int,
    codes: tuple[LimitCode, LimitCode],
) -> LimitBreach | None:
    cap = rule.max_amount_minor
    if cap is not None and used_amount + amount > cap:
        return LimitBreach(codes[0], rule.id, cap)
    if rule.max_count is not None and used_count + 1 > rule.max_count:
        return LimitBreach(codes[1], rule.id, None)
    return None


def _breach(rule: LimitRuleSpec, amount: int, usage: Usage) -> LimitBreach | None:
    match rule.period:
        case LimitPeriod.PER_TXN:
            return _per_txn_breach(rule, amount)
        case LimitPeriod.DAILY:
            return _period_breach(
                rule,
                amount,
                usage.daily_amount_minor,
                usage.daily_count,
                (LimitCode.DAILY_AMOUNT, LimitCode.DAILY_COUNT),
            )
        case LimitPeriod.MONTHLY:
            return _period_breach(
                rule,
                amount,
                usage.monthly_amount_minor,
                usage.monthly_count,
                (LimitCode.MONTHLY_AMOUNT, LimitCode.MONTHLY_COUNT),
            )
        case LimitPeriod.BALANCE_CAP:
            cap = rule.max_amount_minor
            if cap is not None and usage.balance_minor + amount > cap:
                return LimitBreach(LimitCode.BALANCE_CAP, rule.id, cap)
            return None


def check_limits(
    rules: Sequence[LimitRuleSpec],
    *,
    txn_type: str,
    direction: LimitDirection,
    amount: Money,
    subject: Subject,
    usage: Usage,
) -> LimitBreach | None:
    """First breach in a stable order, or ``None``. ``max_amount_minor = 0`` forbids the type."""
    periods = list(LimitPeriod)
    for rule in sorted(
        effective_limit_rules(rules, txn_type, direction, subject),
        key=lambda r: (periods.index(r.period), r.txn_type),
    ):
        if rule.currency != amount.currency:
            continue
        breach = _breach(rule, amount.amount_minor, usage)
        if breach is not None:
            return breach
    return None
