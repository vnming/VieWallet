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
    get_for_wallet,
    held_totals,
)
from app.modules.transactions.use_cases import (
    adjustment,
    deposit,
    refund,
    reversal,
    review,
    transfer,
    withdrawal,
)
from app.modules.transactions.use_cases.adjustment import (
    AdjustmentDirection,
    AdjustmentOutcome,
    AdjustmentReason,
    AdjustmentRequest,
)
from app.modules.transactions.use_cases.common import FinanceContext
from app.modules.transactions.use_cases.workers import (
    process_callback,
    register_handlers,
    resubmit_stalled_deposits,
    run_inquiries,
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
    "FinanceContext",
    "InvalidStateTransition",
    "Transaction",
    "TransactionEvent",
    "TransactionNotFoundError",
    "TxnConcurrentUpdateError",
    "TxnInvalidStateError",
    "TxnStatus",
    "TxnType",
    "adjustment",
    "deposit",
    "get",
    "get_by_reference",
    "get_for_wallet",
    "held_totals",
    "process_callback",
    "refund",
    "register_handlers",
    "resubmit_stalled_deposits",
    "reversal",
    "review",
    "run_inquiries",
    "transfer",
    "withdrawal",
]
