"""Wallet rules: numbers (financial-processing.md §12) and status changes. Pure Python."""

from __future__ import annotations

import secrets
from enum import StrEnum

WALLET_NUMBER_LENGTH = 10


class WalletStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class WalletType(StrEnum):
    MAIN = "MAIN"
    MERCHANT = "MERCHANT"


class FreezeSource(StrEnum):
    """Who froze the wallet. A customer may lift only a freeze they placed themselves."""

    SELF = "SELF"
    STAFF = "STAFF"
    SYSTEM = "SYSTEM"


def luhn_check_digit(payload: str) -> str:
    if not payload.isdigit():
        raise ValueError("payload must be digits")
    total = 0
    # Walk from the right; the digit next to the (future) check digit is doubled first.
    for index, char in enumerate(reversed(payload)):
        digit = int(char)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:  # noqa: PLR2004
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def is_valid_wallet_number(number: str) -> bool:
    if len(number) != WALLET_NUMBER_LENGTH or not number.isdigit():
        return False
    return luhn_check_digit(number[:-1]) == number[-1]


def generate_wallet_number() -> str:
    """Nine random digits (first one non-zero) plus a Luhn check digit."""
    first = str(secrets.randbelow(9) + 1)
    rest = "".join(str(secrets.randbelow(10)) for _ in range(WALLET_NUMBER_LENGTH - 2))
    payload = first + rest
    return payload + luhn_check_digit(payload)


def mask_wallet_number(number: str) -> str:
    return f"******{number[-4:]}"
