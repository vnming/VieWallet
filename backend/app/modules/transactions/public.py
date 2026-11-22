"""Public interface of the transactions module (ADR-0001)."""

from app.modules.transactions.domain.state_machine import (
    TERMINAL,
    TRANSITIONS,
    InvalidStateTransition,
    TxnStatus,
    TxnType,
)
from app.modules.transactions.models import Transaction, TransactionEvent
from app.modules.transactions.service import (
    SYSTEM,
    Actor,
    TransactionNotFoundError,
    TxnConcurrentUpdateError,
    TxnInvalidStateError,
    get,
    get_by_reference,
    held_totals,
)
from app.modules.transactions.use_cases import adjustment
from app.modules.transactions.use_cases.adjustment import (
    AdjustmentDirection,
    AdjustmentOutcome,
    AdjustmentReason,
    AdjustmentRequest,
)

__all__ = [
    "SYSTEM",
    "TERMINAL",
    "TRANSITIONS",
    "Actor",
    "AdjustmentDirection",
    "AdjustmentOutcome",
    "AdjustmentReason",
    "AdjustmentRequest",
    "InvalidStateTransition",
    "Transaction",
    "TransactionEvent",
    "TransactionNotFoundError",
    "TxnConcurrentUpdateError",
    "TxnInvalidStateError",
    "TxnStatus",
    "TxnType",
    "adjustment",
    "get",
    "get_by_reference",
    "held_totals",
]
