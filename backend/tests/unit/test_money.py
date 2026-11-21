from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.money import (
    BIGINT_MAX,
    MAX_AMOUNT_MINOR,
    CurrencyMismatchError,
    Money,
    MoneyError,
    basis_points_of,
    clamp,
)

amounts = st.integers(min_value=0, max_value=MAX_AMOUNT_MINOR)


def vnd(amount: int) -> Money:
    return Money(amount, "VND")


class TestConstruction:
    def test_valid(self) -> None:
        m = vnd(1_250_000)
        assert m.amount_minor == 1_250_000
        assert m.currency == "VND"

    @pytest.mark.parametrize("bad", [1.0, True, Decimal(1), "1", None])
    def test_rejects_non_int_amounts(self, bad: object) -> None:
        with pytest.raises(MoneyError):
            Money(bad, "VND")  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", ["vnd", "VN", "VNDD", "", "V1D"])
    def test_rejects_bad_currency(self, bad: str) -> None:
        with pytest.raises(MoneyError):
            Money(1, bad)

    def test_rejects_out_of_bigint(self) -> None:
        Money(BIGINT_MAX, "VND")
        with pytest.raises(MoneyError):
            Money(BIGINT_MAX + 1, "VND")

    def test_is_immutable(self) -> None:
        m = vnd(1)
        with pytest.raises(AttributeError):
            m.amount_minor = 2  # type: ignore[misc]


class TestApiConversion:
    def test_round_trip(self) -> None:
        m = Money.from_api("1500000", "VND")
        assert m == vnd(1_500_000)
        assert m.to_api() == {"amount": "1500000", "currency": "VND"}

    @pytest.mark.parametrize("bad", ["-1", "1.5", "01", " 1", "1e3", "", "abc", "１２"])  # noqa: RUF001 - full-width digits on purpose
    def test_rejects_malformed(self, bad: str) -> None:
        with pytest.raises(MoneyError):
            Money.from_api(bad, "VND")

    def test_rejects_above_cap(self) -> None:
        Money.from_api(str(MAX_AMOUNT_MINOR), "VND")
        with pytest.raises(MoneyError):
            Money.from_api(str(MAX_AMOUNT_MINOR + 1), "VND")

    def test_rejects_non_string(self) -> None:
        with pytest.raises(MoneyError):
            Money.from_api(100, "VND")  # type: ignore[arg-type]


class TestArithmetic:
    def test_add_sub_neg(self) -> None:
        assert vnd(100) + vnd(50) == vnd(150)
        assert vnd(100) - vnd(150) == vnd(-50)
        assert -vnd(5) == vnd(-5)

    def test_comparisons(self) -> None:
        assert vnd(1) < vnd(2) <= vnd(2) < vnd(3)
        assert vnd(3) > vnd(2) >= vnd(2)

    def test_flags(self) -> None:
        assert vnd(0).is_zero
        assert vnd(1).is_positive
        assert vnd(-1).is_negative
        assert Money.zero("USD") == Money(0, "USD")

    @pytest.mark.parametrize(
        "op",
        [
            lambda a, b: a + b,
            lambda a, b: a - b,
            lambda a, b: a < b,
            lambda a, b: a <= b,
            lambda a, b: a > b,
            lambda a, b: a >= b,
        ],
    )
    def test_currency_mismatch(self, op: object) -> None:
        with pytest.raises(CurrencyMismatchError):
            op(vnd(1), Money(1, "USD"))  # type: ignore[operator]

    def test_rejects_non_money_operand(self) -> None:
        with pytest.raises(TypeError):
            vnd(1) + 1  # type: ignore[operator]

    @given(a=amounts, b=amounts, c=amounts)
    def test_addition_is_associative_and_commutative(self, a: int, b: int, c: int) -> None:
        assert (vnd(a) + vnd(b)) + vnd(c) == vnd(a) + (vnd(b) + vnd(c))
        assert vnd(a) + vnd(b) == vnd(b) + vnd(a)


class TestBasisPoints:
    @pytest.mark.parametrize(
        ("amount", "bps", "expected"),
        [
            (1_000_000, 100, 10_000),  # 1 %
            (1_000_000, 0, 0),
            (149, 100, 1),  # 1.49 -> 1
            (150, 100, 2),  # 1.50 -> 2 (half up)
            (50, 100, 1),  # 0.5 -> 1
            (49, 100, 0),  # 0.49 -> 0
            (1, 10_000, 1),  # 100 %
            (0, 250, 0),
        ],
    )
    def test_examples(self, amount: int, bps: int, expected: int) -> None:
        assert basis_points_of(vnd(amount), bps) == vnd(expected)

    @given(amount=amounts, bps=st.integers(min_value=0, max_value=10_000))
    def test_matches_decimal_round_half_up(self, amount: int, bps: int) -> None:
        exact = Decimal(amount) * Decimal(bps) / Decimal(10_000)
        expected = int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        assert basis_points_of(vnd(amount), bps).amount_minor == expected

    @pytest.mark.parametrize("bps", [-1, 1.5, True])
    def test_rejects_bad_bps(self, bps: object) -> None:
        with pytest.raises(MoneyError):
            basis_points_of(vnd(100), bps)  # type: ignore[arg-type]

    def test_rejects_negative_amount(self) -> None:
        with pytest.raises(MoneyError):
            basis_points_of(vnd(-100), 100)


class TestClamp:
    def test_clamps(self) -> None:
        assert clamp(vnd(5), vnd(10), vnd(20)) == vnd(10)
        assert clamp(vnd(15), vnd(10), vnd(20)) == vnd(15)
        assert clamp(vnd(25), vnd(10), vnd(20)) == vnd(20)
        assert clamp(vnd(25), vnd(10)) == vnd(25)

    def test_rejects_inverted_bounds(self) -> None:
        with pytest.raises(MoneyError):
            clamp(vnd(1), vnd(20), vnd(10))

    def test_rejects_mixed_currency(self) -> None:
        with pytest.raises(CurrencyMismatchError):
            clamp(vnd(1), Money(0, "USD"))
