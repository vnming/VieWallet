"""Transaction PIN rules (FR-PIN-01, FR-PIN-05) and lock policy (security.md §3)."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

PIN_LENGTH = 6
_PIN_RE = re.compile(r"^[0-9]{6}$")

MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION = timedelta(minutes=30)
LOCK_WINDOW = timedelta(hours=24)
LOCKS_BEFORE_RESET_REQUIRED = 3

COMMON_PINS = frozenset(
    {
        "123123",
        "112233",
        "121212",
        "131313",
        "000123",
        "123321",
        "147258",
        "159753",
        "520520",
        "686868",
        "888888",
    }
)


def is_well_formed(pin: str) -> bool:
    return bool(_PIN_RE.match(pin))


def is_weak(pin: str) -> bool:
    """All-same digits, straight sequences, repeated short patterns or well-known PINs."""
    if not is_well_formed(pin):
        return True
    digits = [int(c) for c in pin]
    steps = {b - a for a, b in itertools.pairwise(digits)}
    if len(set(pin)) == 1 or steps in ({1}, {-1}):
        return True
    if pin[:2] * 3 == pin or pin[:3] * 2 == pin:
        return True
    return pin in COMMON_PINS


@dataclass(frozen=True, slots=True)
class PinState:
    failed_attempts: int
    locked_until: datetime | None
    lock_count: int
    lock_window_started_at: datetime | None
    requires_reset: bool


@dataclass(frozen=True, slots=True)
class PinFailureOutcome:
    state: PinState
    locked: bool
    remaining_attempts: int


def is_locked(state: PinState, now: datetime) -> bool:
    return state.requires_reset or (state.locked_until is not None and state.locked_until > now)


def register_failure(state: PinState, now: datetime) -> PinFailureOutcome:
    """Apply one wrong PIN. Five in a row lock for 30 min; the third lock in 24 h needs a reset."""
    failed = state.failed_attempts + 1
    if failed < MAX_FAILED_ATTEMPTS:
        new_state = PinState(
            failed, state.locked_until, state.lock_count, state.lock_window_started_at, False
        )
        return PinFailureOutcome(
            new_state, locked=False, remaining_attempts=MAX_FAILED_ATTEMPTS - failed
        )

    window_start = state.lock_window_started_at
    lock_count = state.lock_count
    if window_start is None or now - window_start > LOCK_WINDOW:
        window_start, lock_count = now, 0
    lock_count += 1
    requires_reset = lock_count >= LOCKS_BEFORE_RESET_REQUIRED
    new_state = PinState(0, now + LOCK_DURATION, lock_count, window_start, requires_reset)
    return PinFailureOutcome(new_state, locked=True, remaining_attempts=0)


def register_success(state: PinState) -> PinState:
    return PinState(0, None, state.lock_count, state.lock_window_started_at, False)
