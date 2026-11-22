"""FT-29 fee rounding at the edges, limit rules, and the first risk rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.money import Money
from app.modules.pricing.domain import (
    FeeRuleSpec,
    LimitCode,
    LimitDirection,
    LimitPeriod,
    LimitRuleSpec,
    RuleScope,
    Subject,
    Usage,
    check_limits,
    compute_fee,
    quote_fee,
)
from app.modules.pricing.service import default_limits
from app.modules.risk.domain import Decision, RiskContext, evaluate

USER = uuid.uuid4()
SUBJECT = Subject(USER, kyc_tier=0)


def fee_rule(**overrides: object) -> FeeRuleSpec:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "version": 1,
        "txn_type": "TRANSFER",
        "currency": "VND",
        "scope": RuleScope.DEFAULT,
        "scope_value": None,
        "fixed_minor": 0,
        "bps": 0,
        "min_fee_minor": 0,
        "max_fee_minor": None,
    }
    values.update(overrides)
    return FeeRuleSpec(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("amount", "bps", "expected"),
    [
        (49, 100, 0),  # 0.49 -> 0
        (50, 100, 1),  # 0.5 -> 1 (half up)
        (149, 100, 1),
        (150, 100, 2),
        (1_000_000, 33, 3_300),
        (1, 5_000, 1),  # 0.5 -> 1
        (9_000_000_000_000, 10_000, 9_000_000_000_000),
        (0, 250, 0),
    ],
)
def test_bps_rounds_half_up_with_integers(amount: int, bps: int, expected: int) -> None:
    assert compute_fee(fee_rule(bps=bps), Money(amount, "VND")) == Money(expected, "VND")


def test_fixed_plus_bps_clamped_to_min_and_max() -> None:
    rule = fee_rule(fixed_minor=1_000, bps=50, min_fee_minor=2_000, max_fee_minor=10_000)
    assert compute_fee(rule, Money(100_000, "VND")) == Money(2_000, "VND")  # 1500 -> min
    assert compute_fee(rule, Money(1_000_000, "VND")) == Money(6_000, "VND")
    assert compute_fee(rule, Money(100_000_000, "VND")) == Money(10_000, "VND")  # 501000 -> max
    with pytest.raises(ValueError, match="currency"):
        compute_fee(rule, Money(1, "USD"))


def test_most_specific_fee_rule_wins() -> None:
    default = fee_rule(fixed_minor=1_100)
    tier = fee_rule(scope=RuleScope.KYC_TIER, scope_value="0", fixed_minor=500)
    personal = fee_rule(scope=RuleScope.USER, scope_value=str(USER), fixed_minor=0, version=2)
    other_tier = fee_rule(scope=RuleScope.KYC_TIER, scope_value="1", fixed_minor=1)
    amount = Money(100_000, "VND")
    assert quote_fee([default, other_tier], "TRANSFER", amount, SUBJECT).fee.amount_minor == 1_100
    assert quote_fee([default, tier], "TRANSFER", amount, SUBJECT).fee.amount_minor == 500
    quote = quote_fee([default, tier, personal], "TRANSFER", amount, SUBJECT)
    assert (quote.fee.amount_minor, quote.rule_id, quote.rule_version) == (0, personal.id, 2)
    none = quote_fee([default], "PAYMENT", amount, SUBJECT)
    assert none.fee.is_zero
    assert none.rule_id is None


def limit_rules() -> list[LimitRuleSpec]:
    return [
        LimitRuleSpec(
            id=uuid.uuid4(),
            txn_type=row["txn_type"],
            direction=LimitDirection(row["direction"]),
            scope=RuleScope(row["scope"]),
            scope_value=row["scope_value"],
            period=LimitPeriod(row["period"]),
            currency="VND",
            max_amount_minor=row["max_amount_minor"],
            max_count=None,
            min_amount_minor=None,
        )
        for row in default_limits()
    ]


def check(txn_type: str, amount: int, usage: Usage, tier: int = 0, direction: str = "OUT") -> str:
    breach = check_limits(
        limit_rules(),
        txn_type=txn_type,
        direction=LimitDirection(direction),
        amount=Money(amount, "VND"),
        subject=Subject(USER, kyc_tier=tier),
        usage=usage,
    )
    return breach.code.value if breach else "OK"


def test_default_limits_per_tier() -> None:
    empty = Usage()
    assert check("TRANSFER", 2_000_000, empty) == "OK"
    assert check("TRANSFER", 2_000_001, empty) == LimitCode.PER_TXN
    assert check("TRANSFER", 2_000_001, empty, tier=1) == "OK"
    assert (
        check("TRANSFER", 1_000_000, Usage(daily_amount_minor=4_500_000)) == LimitCode.DAILY_AMOUNT
    )
    assert (
        check("TRANSFER", 1_000_000, Usage(monthly_amount_minor=19_500_000))
        == LimitCode.MONTHLY_AMOUNT
    )
    assert check("WITHDRAWAL", 100_000, empty) == LimitCode.NOT_ALLOWED
    assert check("WITHDRAWAL", 100_000, empty, tier=1) == "OK"
    assert check("DEPOSIT", 1_000_000, Usage(balance_minor=9_500_000), direction="IN") == (
        LimitCode.BALANCE_CAP
    )
    assert check("DEPOSIT", 1_000_000, Usage(balance_minor=9_000_000), direction="IN") == "OK"


def test_count_and_minimum_limits() -> None:
    rule = LimitRuleSpec(
        id=uuid.uuid4(),
        txn_type="ALL",
        direction=LimitDirection.OUT,
        scope=RuleScope.DEFAULT,
        scope_value=None,
        period=LimitPeriod.DAILY,
        currency="VND",
        max_amount_minor=None,
        max_count=3,
        min_amount_minor=None,
    )
    minimum = LimitRuleSpec(
        id=uuid.uuid4(),
        txn_type="PAYMENT",
        direction=LimitDirection.OUT,
        scope=RuleScope.DEFAULT,
        scope_value=None,
        period=LimitPeriod.PER_TXN,
        currency="VND",
        max_amount_minor=None,
        max_count=None,
        min_amount_minor=1_000,
    )
    monthly = LimitRuleSpec(
        id=uuid.uuid4(),
        txn_type="PAYMENT",
        direction=LimitDirection.OUT,
        scope=RuleScope.DEFAULT,
        scope_value=None,
        period=LimitPeriod.MONTHLY,
        currency="VND",
        max_amount_minor=None,
        max_count=10,
        min_amount_minor=None,
    )

    def run(amount: int, usage: Usage) -> str:
        breach = check_limits(
            [rule, minimum, monthly],
            txn_type="PAYMENT",
            direction=LimitDirection.OUT,
            amount=Money(amount, "VND"),
            subject=SUBJECT,
            usage=usage,
        )
        return breach.code.value if breach else "OK"

    assert run(999, Usage()) == LimitCode.BELOW_MINIMUM
    assert run(5_000, Usage(daily_count=2)) == "OK"
    assert run(5_000, Usage(daily_count=3)) == LimitCode.DAILY_COUNT
    assert run(5_000, Usage(monthly_count=10)) == LimitCode.MONTHLY_COUNT
    # Rules in another currency are ignored.
    assert (
        check_limits(
            [rule],
            txn_type="PAYMENT",
            direction=LimitDirection.OUT,
            amount=Money(5, "USD"),
            subject=SUBJECT,
            usage=Usage(daily_count=99),
        )
        is None
    )


NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


def ctx(**overrides: object) -> RiskContext:
    values: dict[str, object] = {
        "user_id": USER,
        "txn_type": "TRANSFER",
        "amount_minor": 100_000,
        "currency": "VND",
        "now": NOW,
    }
    values.update(overrides)
    return RiskContext(**values)  # type: ignore[arg-type]


def codes(context: RiskContext) -> list[str]:
    return [h.code for h in evaluate(context).hits]


def test_quiet_activity_is_allowed() -> None:
    result = evaluate(ctx())
    assert (result.decision, result.score, result.hits) == (Decision.ALLOW, 0, ())


def test_individual_rules() -> None:
    assert codes(ctx(outgoing_count_10m=10)) == ["VELOCITY_COUNT"]
    assert codes(ctx(outgoing_count_10m=9)) == []
    assert codes(ctx(outgoing_amount_1h_minor=2_400_001, daily_limit_minor=5_000_000)) == [
        "VELOCITY_AMOUNT"
    ]
    # Exactly 50 % is not "more than half".
    assert codes(ctx(outgoing_amount_1h_minor=2_400_000, daily_limit_minor=5_000_000)) == []
    assert codes(ctx(outgoing_amount_1h_minor=2_400_000, daily_limit_minor=None)) == []
    assert codes(ctx(failed_count_30m=3)) == ["REPEATED_FAILURES"]
    assert codes(ctx(same_amount_same_counterparty_2m=1)) == []  # no counterparty
    assert codes(ctx(same_amount_same_counterparty_2m=1, counterparty_wallet_id=uuid.uuid4())) == [
        "DUPLICATE_PATTERN"
    ]


def test_credential_change_cooldown() -> None:
    recent = NOW - timedelta(hours=2)
    assert codes(ctx(credentials_changed_at=recent, amount_minor=2_000_000)) == []
    hit = evaluate(ctx(credentials_changed_at=recent, amount_minor=2_000_001))
    assert hit.decision is Decision.ALLOW  # +50 alone stays under the review threshold
    assert hit.hits[0].evidence == {"changed_at": recent.isoformat()}
    blocked = evaluate(
        ctx(credentials_changed_at=recent, amount_minor=3_000_000, txn_type="WITHDRAWAL")
    )
    assert blocked.decision is Decision.BLOCK
    old = NOW - timedelta(hours=24)
    assert codes(ctx(credentials_changed_at=old, amount_minor=3_000_000)) == []


def test_scores_add_up_to_review_and_cap_at_100() -> None:
    result = evaluate(
        ctx(
            outgoing_count_10m=20,
            failed_count_30m=5,
            counterparty_wallet_id=uuid.uuid4(),
            same_amount_same_counterparty_2m=2,
        )
    )
    assert result.score == 100  # 40 + 30 + 40 capped
    assert result.decision is Decision.REVIEW
    assert result.rules_version
