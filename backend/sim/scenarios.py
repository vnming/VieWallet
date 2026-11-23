"""Scenario selection (payment-provider-simulator.md §4). Pure functions."""

from __future__ import annotations

from enum import StrEnum


class Mode(StrEnum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"  # 20 % of requests fail with 503, 1-5 s extra latency
    OUTAGE = "OUTAGE"  # every request fails with 503


class Scenario(StrEnum):
    SUCCESS = "SUCCESS"
    FAIL = "FAIL"  # 111
    DELAYED = "DELAYED"  # 222
    SLOW_RESPONSE = "SLOW_RESPONSE"  # 333
    CONNECTION_RESET = "CONNECTION_RESET"  # 444
    DUPLICATE_CALLBACKS = "DUPLICATE_CALLBACKS"  # 555
    BAD_SIGNATURE_FIRST = "BAD_SIGNATURE_FIRST"  # 666
    UNAVAILABLE = "UNAVAILABLE"  # 777
    STATEMENT_MISMATCH = "STATEMENT_MISMATCH"  # 888
    RETURNED = "RETURNED"  # 999 (payouts)


TRIGGERS: dict[int, Scenario] = {
    111: Scenario.FAIL,
    222: Scenario.DELAYED,
    333: Scenario.SLOW_RESPONSE,
    444: Scenario.CONNECTION_RESET,
    555: Scenario.DUPLICATE_CALLBACKS,
    666: Scenario.BAD_SIGNATURE_FIRST,
    777: Scenario.UNAVAILABLE,
    888: Scenario.STATEMENT_MISMATCH,
    999: Scenario.RETURNED,
}


def trigger_code(amount_minor: int) -> int:
    """``t = (amount / 1000) mod 1000`` for VND amounts (multiples of 1 000)."""
    return (amount_minor // 1000) % 1000


def scenario_for(amount_minor: int) -> Scenario:
    return TRIGGERS.get(trigger_code(amount_minor), Scenario.SUCCESS)
