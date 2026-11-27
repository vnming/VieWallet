"""FT-24: every (type, from, to) triple is allowed exactly when financial-processing.md §5 says so.

The expected edges are written out again here, independently of the implementation table, so
a typo in either place fails the test.
"""

from __future__ import annotations

import itertools

import pytest

from app.modules.transactions.domain.state_machine import (
    STATES,
    TERMINAL,
    TRANSITIONS,
    InvalidStateTransition,
    TxnStatus,
    TxnType,
    check_transition,
    is_allowed,
)

DOCUMENTED_EDGES: dict[str, set[str]] = {
    "DEPOSIT": {
        "CREATED>PENDING",
        "CREATED>FAILED",
        "CREATED>CANCELLED",
        "PENDING>COMPLETED",
        "PENDING>FAILED",
        "PENDING>EXPIRED",
        "COMPLETED>REVERSED",
    },
    "WITHDRAWAL": {
        "CREATED>PENDING",
        "CREATED>FAILED",
        "CREATED>EXPIRED",
        "CREATED>CANCELLED",
        "PENDING>PROCESSING",
        "PENDING>CANCELLED",
        "PROCESSING>COMPLETED",
        "PROCESSING>FAILED",
        "COMPLETED>REVERSED",
    },
    "TRANSFER": {
        "CREATED>COMPLETED",
        "CREATED>PENDING",
        "CREATED>FAILED",
        "CREATED>EXPIRED",
        "CREATED>CANCELLED",
        "PENDING>COMPLETED",
        "PENDING>CANCELLED",
        "COMPLETED>REVERSED",
    },
    "PAYMENT": {
        "CREATED>COMPLETED",
        "CREATED>PENDING",
        "CREATED>FAILED",
        "CREATED>EXPIRED",
        "CREATED>CANCELLED",
        "PENDING>COMPLETED",
        "PENDING>CANCELLED",
        "COMPLETED>PARTIALLY_REFUNDED",
        "PARTIALLY_REFUNDED>PARTIALLY_REFUNDED",
        "COMPLETED>REFUNDED",
        "PARTIALLY_REFUNDED>REFUNDED",
        "COMPLETED>REVERSED",
    },
    "REFUND": {
        "CREATED>PENDING",
        "CREATED>COMPLETED",
        "CREATED>FAILED",
        "PENDING>COMPLETED",
        "PENDING>FAILED",
        "PENDING>CANCELLED",
    },
    "REVERSAL": {
        "CREATED>PENDING",
        "CREATED>COMPLETED",
        "CREATED>FAILED",
        "CREATED>CANCELLED",
        "PENDING>COMPLETED",
        "PENDING>FAILED",
        "PENDING>CANCELLED",
    },
    "ADJUSTMENT": {"CREATED>PENDING", "PENDING>COMPLETED", "PENDING>CANCELLED"},
}

# §5.8
DOCUMENTED_STATES: dict[str, set[str]] = {
    "DEPOSIT": {"CREATED", "PENDING", "COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "REVERSED"},
    "WITHDRAWAL": {
        "CREATED",
        "PENDING",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "EXPIRED",
        "REVERSED",
    },
    "TRANSFER": {"CREATED", "PENDING", "COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "REVERSED"},
    "PAYMENT": {
        "CREATED",
        "PENDING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "EXPIRED",
        "PARTIALLY_REFUNDED",
        "REFUNDED",
        "REVERSED",
    },
    "REFUND": {"CREATED", "PENDING", "COMPLETED", "FAILED", "CANCELLED"},
    "REVERSAL": {"CREATED", "PENDING", "COMPLETED", "FAILED", "CANCELLED"},
    "ADJUSTMENT": {"CREATED", "PENDING", "COMPLETED", "CANCELLED"},
}

ALL_TRIPLES = list(itertools.product(TxnType, TxnStatus, TxnStatus))


def test_table_covers_every_type() -> None:
    assert set(TRANSITIONS) == set(TxnType)
    assert set(DOCUMENTED_EDGES) == {t.value for t in TxnType}


@pytest.mark.parametrize(("txn_type", "source", "target"), ALL_TRIPLES)
def test_every_transition_matches_the_design(
    txn_type: TxnType, source: TxnStatus, target: TxnStatus
) -> None:
    expected = f"{source}>{target}" in DOCUMENTED_EDGES[txn_type.value]
    assert is_allowed(txn_type, source, target) is expected
    if expected:
        check_transition(txn_type, source, target)
    else:
        with pytest.raises(InvalidStateTransition):
            check_transition(txn_type, source, target)


@pytest.mark.parametrize("txn_type", list(TxnType))
def test_states_per_type(txn_type: TxnType) -> None:
    assert {s.value for s in STATES[txn_type]} == DOCUMENTED_STATES[txn_type.value]


def test_failed_cancelled_expired_are_dead_ends() -> None:
    for txn_type in TxnType:
        for status in (TxnStatus.FAILED, TxnStatus.CANCELLED, TxnStatus.EXPIRED):
            assert not TRANSITIONS[txn_type].get(status)
    assert TxnStatus.CREATED not in TERMINAL
    assert TxnStatus.PROCESSING not in TERMINAL


def test_error_message_names_the_edge() -> None:
    with pytest.raises(InvalidStateTransition, match="ADJUSTMENT: CREATED -> COMPLETED"):
        check_transition(TxnType.ADJUSTMENT, TxnStatus.CREATED, TxnStatus.COMPLETED)
