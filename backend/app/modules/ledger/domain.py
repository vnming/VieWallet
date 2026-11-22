"""Pure double-entry rules (financial-processing.md §3-§4). No I/O, no framework imports."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.core.money import Money


class AccountCode(StrEnum):
    CUSTOMER_WALLET = "CUSTOMER_WALLET"
    HOLD = "HOLD"
    PROVIDER_SETTLEMENT = "PROVIDER_SETTLEMENT"
    FEE_REVENUE = "FEE_REVENUE"
    ADJUSTMENT_EXPENSE = "ADJUSTMENT_EXPENSE"
    SUSPENSE = "SUSPENSE"


class AccountType(StrEnum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"


class Side(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


@dataclass(frozen=True, slots=True)
class AccountSpec:
    type: AccountType
    normal_side: Side
    allow_negative: bool


# Chart of accounts. Customer wallets and HOLD may never go negative (DB CHECK as backstop).
CHART: dict[AccountCode, AccountSpec] = {
    AccountCode.CUSTOMER_WALLET: AccountSpec(
        AccountType.LIABILITY, Side.CREDIT, allow_negative=False
    ),
    AccountCode.HOLD: AccountSpec(AccountType.LIABILITY, Side.CREDIT, allow_negative=False),
    AccountCode.PROVIDER_SETTLEMENT: AccountSpec(
        AccountType.ASSET, Side.DEBIT, allow_negative=True
    ),
    AccountCode.FEE_REVENUE: AccountSpec(AccountType.INCOME, Side.CREDIT, allow_negative=False),
    AccountCode.ADJUSTMENT_EXPENSE: AccountSpec(
        AccountType.EXPENSE, Side.DEBIT, allow_negative=True
    ),
    AccountCode.SUSPENSE: AccountSpec(AccountType.LIABILITY, Side.CREDIT, allow_negative=True),
}

# Accounts locked with SELECT ... FOR UPDATE before posting (business decisions read them).
LOCKED_CODES = frozenset({AccountCode.CUSTOMER_WALLET})


class JournalKind(StrEnum):
    DEPOSIT_SETTLE = "DEPOSIT_SETTLE"
    WITHDRAWAL_HOLD = "WITHDRAWAL_HOLD"
    WITHDRAWAL_SETTLE = "WITHDRAWAL_SETTLE"
    WITHDRAWAL_RELEASE = "WITHDRAWAL_RELEASE"
    WITHDRAWAL_RETURN = "WITHDRAWAL_RETURN"
    TRANSFER = "TRANSFER"
    TRANSFER_HOLD = "TRANSFER_HOLD"
    TRANSFER_RELEASE_TO_RECEIVER = "TRANSFER_RELEASE_TO_RECEIVER"
    TRANSFER_RELEASE_TO_SENDER = "TRANSFER_RELEASE_TO_SENDER"
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    REVERSAL = "REVERSAL"
    ADJUSTMENT = "ADJUSTMENT"
    SUSPENSE_IN = "SUSPENSE_IN"


class LedgerError(ValueError):
    """A journal that violates double-entry rules (programming error, never user input)."""


@dataclass(frozen=True, slots=True)
class PostingLine:
    account_id: uuid.UUID
    side: Side
    amount: Money

    def __post_init__(self) -> None:
        if not self.amount.is_positive:
            raise LedgerError("posting amounts must be positive")


@dataclass(frozen=True, slots=True)
class JournalDraft:
    kind: JournalKind
    lines: tuple[PostingLine, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if len(self.lines) < 2:  # noqa: PLR2004
            raise LedgerError("a journal needs at least two postings")
        currencies = {line.amount.currency for line in self.lines}
        if len(currencies) != 1:
            raise LedgerError("all postings of a journal must use one currency")
        debit = sum(line.amount.amount_minor for line in self.lines if line.side is Side.DEBIT)
        credit = sum(line.amount.amount_minor for line in self.lines if line.side is Side.CREDIT)
        if debit != credit:
            raise LedgerError(f"unbalanced journal: debit {debit} != credit {credit}")

    @property
    def currency(self) -> str:
        return self.lines[0].amount.currency

    @property
    def total(self) -> Money:
        return Money(
            sum(line.amount.amount_minor for line in self.lines if line.side is Side.DEBIT),
            self.currency,
        )

    def reversed(self, kind: JournalKind = JournalKind.REVERSAL) -> JournalDraft:
        """Exact mirror: every debit becomes a credit and vice versa."""
        flipped = tuple(
            PostingLine(
                line.account_id,
                Side.CREDIT if line.side is Side.DEBIT else Side.DEBIT,
                line.amount,
            )
            for line in self.lines
        )
        return JournalDraft(kind=kind, lines=flipped, description=f"reversal of {self.kind}")


def balance_delta(normal_side: Side, side: Side, amount_minor: int) -> int:
    """Effect of one posting on a balance stored positive in the account's normal direction."""
    return amount_minor if side is normal_side else -amount_minor


def debit(account_id: uuid.UUID, amount: Money) -> PostingLine:
    return PostingLine(account_id, Side.DEBIT, amount)


def credit(account_id: uuid.UUID, amount: Money) -> PostingLine:
    return PostingLine(account_id, Side.CREDIT, amount)
