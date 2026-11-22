"""FT-27 at the domain level: random money movements keep invariants I1-I5.

An in-memory book applies journals built with the real posting rules (financial-processing.md
§4) and the real balance arithmetic, refusing any journal that would push a non-negative
account below zero (as the posting engine does under its row locks). After every step the
invariants are recomputed from scratch.
"""

from __future__ import annotations

import random
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.money import Money
from app.modules.ledger.domain import (
    CHART,
    AccountCode,
    JournalDraft,
    JournalKind,
    Side,
    balance_delta,
    credit,
    debit,
)

WALLETS = 5


def vnd(amount: int) -> Money:
    return Money(amount, "VND")


@dataclass
class Book:
    codes: dict[uuid.UUID, AccountCode] = field(default_factory=dict)
    balances: dict[uuid.UUID, int] = field(default_factory=lambda: defaultdict(int))
    journals: list[JournalDraft] = field(default_factory=list)
    reversed_ids: set[int] = field(default_factory=set)
    holds: list[tuple[int, int, int]] = field(default_factory=list)  # (wallet idx, amount, fee)

    def __post_init__(self) -> None:
        self.folded = 0
        self.recomputed: dict[uuid.UUID, int] = defaultdict(int)
        self.wallets = [self._open(AccountCode.CUSTOMER_WALLET) for _ in range(WALLETS)]
        self.provider = self._open(AccountCode.PROVIDER_SETTLEMENT)
        self.hold = self._open(AccountCode.HOLD)
        self.fees = self._open(AccountCode.FEE_REVENUE)
        self.expense = self._open(AccountCode.ADJUSTMENT_EXPENSE)

    def _open(self, code: AccountCode) -> uuid.UUID:
        account = uuid.uuid4()
        self.codes[account] = code
        self.balances[account] = 0
        return account

    def apply(self, draft: JournalDraft) -> bool:
        deltas: dict[uuid.UUID, int] = defaultdict(int)
        for line in draft.lines:
            spec = CHART[self.codes[line.account_id]]
            deltas[line.account_id] += balance_delta(
                spec.normal_side, line.side, line.amount.amount_minor
            )
        for account, delta in deltas.items():
            if not CHART[self.codes[account]].allow_negative and self.balances[account] + delta < 0:
                return False
        for account, delta in deltas.items():
            self.balances[account] += delta
        self.journals.append(draft)
        return True

    # -- use cases (posting rules of §4) ---------------------------------------------------
    def deposit(self, w: int, amount: int) -> None:
        self.apply(
            JournalDraft(
                JournalKind.DEPOSIT_SETTLE,
                (debit(self.provider, vnd(amount)), credit(self.wallets[w], vnd(amount))),
            )
        )

    def transfer(self, s: int, r: int, amount: int, fee: int) -> None:
        if s == r:
            return
        lines = [debit(self.wallets[s], vnd(amount + fee)), credit(self.wallets[r], vnd(amount))]
        if fee:
            lines.append(credit(self.fees, vnd(fee)))
        self.apply(JournalDraft(JournalKind.TRANSFER, tuple(lines)))

    def withdraw_hold(self, w: int, amount: int, fee: int) -> None:
        total = vnd(amount + fee)
        held = JournalDraft(
            JournalKind.WITHDRAWAL_HOLD, (debit(self.wallets[w], total), credit(self.hold, total))
        )
        if self.apply(held):
            self.holds.append((w, amount, fee))

    def withdraw_finish(self, pick: int, *, success: bool) -> None:
        if not self.holds:
            return
        w, amount, fee = self.holds.pop(pick % len(self.holds))
        total = vnd(amount + fee)
        if success:
            lines = [debit(self.hold, total), credit(self.provider, vnd(amount))]
            if fee:
                lines.append(credit(self.fees, vnd(fee)))
            draft = JournalDraft(JournalKind.WITHDRAWAL_SETTLE, tuple(lines))
        else:
            draft = JournalDraft(
                JournalKind.WITHDRAWAL_RELEASE,
                (debit(self.hold, total), credit(self.wallets[w], total)),
            )
        assert self.apply(draft), "releasing a hold can never fail"

    def payment_or_refund(self, payer: int, merchant: int, amount: int) -> None:
        if payer != merchant:
            self.apply(
                JournalDraft(
                    JournalKind.PAYMENT,
                    (
                        debit(self.wallets[payer], vnd(amount)),
                        credit(self.wallets[merchant], vnd(amount)),
                    ),
                )
            )

    def adjust(self, w: int, amount: int, *, to_customer: bool) -> None:
        a, b = (self.expense, self.wallets[w]) if to_customer else (self.wallets[w], self.expense)
        self.apply(
            JournalDraft(JournalKind.ADJUSTMENT, (debit(a, vnd(amount)), credit(b, vnd(amount))))
        )

    def reverse(self, pick: int) -> None:
        # Holds are settled/released by their own journals, never reversed directly.
        candidates = [
            i
            for i, j in enumerate(self.journals)
            if i not in self.reversed_ids
            and j.kind not in {JournalKind.REVERSAL, JournalKind.WITHDRAWAL_HOLD}
            and all(line.account_id != self.hold for line in j.lines)
        ]
        if not candidates:
            return
        index = candidates[pick % len(candidates)]
        if self.apply(self.journals[index].reversed()):
            self.reversed_ids.add(index)

    # -- invariants --------------------------------------------------------------------------
    def check(self, *, full: bool = False) -> None:
        """I1-I5. Journals are folded in incrementally; ``full`` recomputes from scratch."""
        if full:
            self.folded, self.recomputed = 0, defaultdict(int)
        for journal in self.journals[self.folded :]:
            debit_total = sum(x.amount.amount_minor for x in journal.lines if x.side is Side.DEBIT)
            credit_total = sum(
                x.amount.amount_minor for x in journal.lines if x.side is Side.CREDIT
            )
            assert debit_total == credit_total  # I1
            for line in journal.lines:
                spec = CHART[self.codes[line.account_id]]
                self.recomputed[line.account_id] += balance_delta(
                    spec.normal_side, line.side, line.amount.amount_minor
                )
        self.folded = len(self.journals)
        for account, code in self.codes.items():
            assert self.balances[account] == self.recomputed[account]  # I2
            if not CHART[code].allow_negative:
                assert self.balances[account] >= 0  # I3
        debit_side = sum(
            b for a, b in self.balances.items() if CHART[self.codes[a]].normal_side is Side.DEBIT
        )
        credit_side = sum(
            b for a, b in self.balances.items() if CHART[self.codes[a]].normal_side is Side.CREDIT
        )
        assert debit_side == credit_side  # I4
        assert self.balances[self.hold] == sum(a + f for _, a, f in self.holds)  # I5


def run_operation(book: Book, rng: random.Random) -> None:
    w1, w2 = rng.randrange(WALLETS), rng.randrange(WALLETS)
    amount = rng.choice([1, 999, 1_000, 50_000, 1_000_000, rng.randint(1, 5_000_000)])
    fee = rng.choice([0, 0, 1_100, rng.randint(0, 10_000)])
    match rng.randrange(8):
        case 0:
            book.deposit(w1, amount)
        case 1:
            book.transfer(w1, w2, amount, fee)
        case 2:
            book.withdraw_hold(w1, amount, fee)
        case 3:
            book.withdraw_finish(rng.randrange(1_000), success=rng.random() < 0.7)
        case 4:
            book.payment_or_refund(w1, w2, amount)
        case 5:
            book.reverse(rng.randrange(1_000))
        case 6:
            book.adjust(w1, amount, to_customer=rng.random() < 0.6)
        case _:
            book.transfer(w2, w1, amount, 0)


def test_ten_thousand_random_operations_keep_invariants() -> None:
    rng = random.Random(20261122)  # deterministic: a failure is reproducible
    book = Book()
    for _ in range(10_000):
        run_operation(book, rng)
        book.check()
    book.check(full=True)
    assert len(book.journals) > 3_000  # the run really moved money


@settings(max_examples=60, deadline=None)
@given(st.lists(st.integers(min_value=0, max_value=2**32), min_size=1, max_size=150))
def test_hypothesis_sequences_keep_invariants(seeds: list[int]) -> None:
    book = Book()
    for seed in seeds:
        run_operation(book, random.Random(seed))
        book.check()
