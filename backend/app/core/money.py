"""Money value object (ADR-0003).

Amounts are integers in the currency's minor unit. VND has exponent 0, so one minor unit is
one dong. Floats are rejected everywhere in the money path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Self

BIGINT_MAX = 2**63 - 1
# Sanity cap for any single amount, well below BIGINT so sums of many amounts still fit.
MAX_AMOUNT_MINOR = 9_000_000_000_000
BPS_DENOMINATOR = 10_000

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_API_AMOUNT_RE = re.compile(r"^(0|[1-9][0-9]{0,18})$")


class MoneyError(ValueError):
    """Invalid money value or operation."""


class CurrencyMismatchError(MoneyError):
    """Arithmetic or comparison between different currencies."""


@dataclass(frozen=True, slots=True)
class Currency:
    code: str
    exponent: int


# Static registry for formatting/validation in the shared kernel. The `currencies` table is
# the runtime source for which currencies are enabled.
KNOWN_CURRENCIES: dict[str, Currency] = {
    "VND": Currency("VND", 0),
    "USD": Currency("USD", 2),
}


@dataclass(frozen=True, slots=True, order=False)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        # bool is a subclass of int; reject it explicitly, together with floats and Decimals.
        if type(self.amount_minor) is not int:
            raise MoneyError("amount_minor must be an int")
        if abs(self.amount_minor) > BIGINT_MAX:
            raise MoneyError("amount_minor does not fit in BIGINT")
        if not isinstance(self.currency, str) or not _CURRENCY_RE.match(self.currency):
            raise MoneyError("currency must be a 3-letter upper-case ISO 4217 code")

    # ------------------------------------------------------------------ constructors
    @classmethod
    def zero(cls, currency: str) -> Self:
        return cls(0, currency)

    @classmethod
    def from_api(cls, amount: str, currency: str) -> Self:
        """Parse an API amount: a string of non-negative integer minor units."""
        if not isinstance(amount, str) or not _API_AMOUNT_RE.match(amount):
            raise MoneyError("amount must be a string of non-negative integer minor units")
        value = int(amount)
        if value > MAX_AMOUNT_MINOR:
            raise MoneyError("amount exceeds the maximum allowed value")
        return cls(value, currency)

    # ------------------------------------------------------------------ helpers
    def _check(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError("operand must be Money")
        if other.currency != self.currency:
            raise CurrencyMismatchError(f"{self.currency} != {other.currency}")

    def to_api(self) -> dict[str, str]:
        return {"amount": str(self.amount_minor), "currency": self.currency}

    @property
    def is_zero(self) -> bool:
        return self.amount_minor == 0

    @property
    def is_positive(self) -> bool:
        return self.amount_minor > 0

    @property
    def is_negative(self) -> bool:
        return self.amount_minor < 0

    # ------------------------------------------------------------------ arithmetic
    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount_minor, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount_minor <= other.amount_minor

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount_minor > other.amount_minor

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount_minor >= other.amount_minor


def basis_points_of(amount: Money, bps: int) -> Money:
    """Return ``amount * bps / 10_000`` rounded half-up, using integer arithmetic only."""
    if type(bps) is not int or bps < 0:
        raise MoneyError("bps must be a non-negative int")
    if amount.is_negative:
        raise MoneyError("percentage fees are only defined for non-negative amounts")
    value = (amount.amount_minor * bps + BPS_DENOMINATOR // 2) // BPS_DENOMINATOR
    return Money(value, amount.currency)


def clamp(value: Money, minimum: Money, maximum: Money | None = None) -> Money:
    """Clamp ``value`` to ``[minimum, maximum]`` (``maximum=None`` means no upper bound)."""
    value._check(minimum)
    if maximum is not None:
        value._check(maximum)
        if minimum > maximum:
            raise MoneyError("minimum must not exceed maximum")
        if value > maximum:
            return maximum
    return minimum if value < minimum else value
