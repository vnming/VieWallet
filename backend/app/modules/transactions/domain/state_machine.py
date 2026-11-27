"""Declarative transaction state machines (financial-processing.md §5).

``TRANSITIONS[type][from]`` is the complete set of allowed targets. Anything else raises
``InvalidStateTransition``. A unit test walks every (type, from, to) triple against this table.
"""

from __future__ import annotations

from enum import StrEnum


class TxnType(StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    TRANSFER = "TRANSFER"
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    REVERSAL = "REVERSAL"
    ADJUSTMENT = "ADJUSTMENT"


class TxnStatus(StrEnum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"
    REVERSED = "REVERSED"


S = TxnStatus

TRANSITIONS: dict[TxnType, dict[TxnStatus, frozenset[TxnStatus]]] = {
    TxnType.DEPOSIT: {
        S.CREATED: frozenset({S.PENDING, S.FAILED, S.CANCELLED}),
        S.PENDING: frozenset({S.COMPLETED, S.FAILED, S.EXPIRED}),
        S.COMPLETED: frozenset({S.REVERSED}),
    },
    TxnType.WITHDRAWAL: {
        S.CREATED: frozenset({S.PENDING, S.FAILED, S.EXPIRED, S.CANCELLED}),
        S.PENDING: frozenset({S.PROCESSING, S.CANCELLED}),
        S.PROCESSING: frozenset({S.COMPLETED, S.FAILED}),
        S.COMPLETED: frozenset({S.REVERSED}),
    },
    TxnType.TRANSFER: {
        S.CREATED: frozenset({S.COMPLETED, S.PENDING, S.FAILED, S.EXPIRED, S.CANCELLED}),
        S.PENDING: frozenset({S.COMPLETED, S.CANCELLED}),
        S.COMPLETED: frozenset({S.REVERSED}),
    },
    TxnType.PAYMENT: {
        S.CREATED: frozenset({S.COMPLETED, S.PENDING, S.FAILED, S.EXPIRED, S.CANCELLED}),
        S.PENDING: frozenset({S.COMPLETED, S.CANCELLED}),
        S.COMPLETED: frozenset({S.PARTIALLY_REFUNDED, S.REFUNDED, S.REVERSED}),
        S.PARTIALLY_REFUNDED: frozenset({S.PARTIALLY_REFUNDED, S.REFUNDED}),
    },
    TxnType.REFUND: {
        S.CREATED: frozenset({S.PENDING, S.COMPLETED, S.FAILED}),
        # PENDING = a staff refund awaiting approval (Phase 8).
        S.PENDING: frozenset({S.COMPLETED, S.FAILED, S.CANCELLED}),
    },
    TxnType.REVERSAL: {
        S.CREATED: frozenset({S.PENDING, S.COMPLETED, S.FAILED, S.CANCELLED}),
        S.PENDING: frozenset({S.COMPLETED, S.FAILED, S.CANCELLED}),
    },
    TxnType.ADJUSTMENT: {
        S.CREATED: frozenset({S.PENDING}),
        S.PENDING: frozenset({S.COMPLETED, S.CANCELLED}),
    },
}

# States each type may ever be in (§5.8).
STATES: dict[TxnType, frozenset[TxnStatus]] = {
    t: frozenset({S.CREATED}).union(*table.values()) for t, table in TRANSITIONS.items()
}

TERMINAL: frozenset[TxnStatus] = frozenset(
    {S.COMPLETED, S.FAILED, S.CANCELLED, S.EXPIRED, S.PARTIALLY_REFUNDED, S.REFUNDED, S.REVERSED}
)


class InvalidStateTransition(ValueError):  # noqa: N818 - name used throughout the design docs
    def __init__(self, txn_type: TxnType, from_status: TxnStatus, to_status: TxnStatus) -> None:
        super().__init__(f"{txn_type}: {from_status} -> {to_status} is not allowed")
        self.txn_type = txn_type
        self.from_status = from_status
        self.to_status = to_status


def allowed_targets(txn_type: TxnType, from_status: TxnStatus) -> frozenset[TxnStatus]:
    return TRANSITIONS[txn_type].get(from_status, frozenset())


def is_allowed(txn_type: TxnType, from_status: TxnStatus, to_status: TxnStatus) -> bool:
    return to_status in allowed_targets(txn_type, from_status)


def check_transition(txn_type: TxnType, from_status: TxnStatus, to_status: TxnStatus) -> None:
    if not is_allowed(txn_type, from_status, to_status):
        raise InvalidStateTransition(txn_type, from_status, to_status)
