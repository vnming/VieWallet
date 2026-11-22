"""Double-entry rules, wallet numbers and references (pure domain)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.money import Money
from app.modules.ledger.domain import (
    CHART,
    AccountCode,
    JournalDraft,
    JournalKind,
    LedgerError,
    PostingLine,
    Side,
    balance_delta,
    credit,
    debit,
)
from app.modules.transactions.domain.reference import is_reference, new_reference
from app.modules.wallet.domain import (
    generate_wallet_number,
    is_valid_wallet_number,
    luhn_check_digit,
    mask_wallet_number,
)

A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def vnd(amount: int) -> Money:
    return Money(amount, "VND")


def test_balanced_journal_and_total() -> None:
    draft = JournalDraft(
        JournalKind.TRANSFER, (debit(A, vnd(1_100)), credit(B, vnd(1_000)), credit(C, vnd(100)))
    )
    assert draft.total == vnd(1_100)
    assert draft.currency == "VND"


@pytest.mark.parametrize(
    "lines",
    [
        (debit(A, vnd(100)),),
        (debit(A, vnd(100)), credit(B, vnd(99))),
        (debit(A, vnd(100)), credit(B, Money(100, "USD"))),
    ],
)
def test_invalid_journals_are_rejected(lines: tuple[PostingLine, ...]) -> None:
    with pytest.raises(LedgerError):
        JournalDraft(JournalKind.ADJUSTMENT, lines)


@pytest.mark.parametrize("amount", [0, -5])
def test_posting_amount_must_be_positive(amount: int) -> None:
    with pytest.raises(LedgerError):
        PostingLine(A, Side.DEBIT, vnd(amount))


def test_reversal_mirrors_every_line() -> None:
    draft = JournalDraft(JournalKind.TRANSFER, (debit(A, vnd(500)), credit(B, vnd(500))))
    mirror = draft.reversed()
    assert mirror.kind is JournalKind.REVERSAL
    assert [(line.account_id, line.side) for line in mirror.lines] == [
        (A, Side.CREDIT),
        (B, Side.DEBIT),
    ]


def test_balance_delta_follows_the_normal_side() -> None:
    wallet = CHART[AccountCode.CUSTOMER_WALLET]
    provider = CHART[AccountCode.PROVIDER_SETTLEMENT]
    assert balance_delta(wallet.normal_side, Side.CREDIT, 10) == 10  # credit grows a liability
    assert balance_delta(wallet.normal_side, Side.DEBIT, 10) == -10
    assert balance_delta(provider.normal_side, Side.DEBIT, 10) == 10  # debit grows an asset
    assert not wallet.allow_negative
    assert not CHART[AccountCode.HOLD].allow_negative


@pytest.mark.parametrize(
    ("payload", "check"), [("7992739871", "3"), ("123456789", "7"), ("000000000", "0")]
)
def test_luhn_known_vectors(payload: str, check: str) -> None:
    assert luhn_check_digit(payload) == check


def test_generated_wallet_numbers_are_valid() -> None:
    numbers = {generate_wallet_number() for _ in range(500)}
    assert all(is_valid_wallet_number(n) and n[0] != "0" and len(n) == 10 for n in numbers)
    sample = next(iter(numbers))
    wrong = sample[:-1] + str((int(sample[-1]) + 1) % 10)
    assert not is_valid_wallet_number(wrong)
    assert not is_valid_wallet_number("12345")
    assert not is_valid_wallet_number("abcdefghij")
    assert mask_wallet_number(sample) == "******" + sample[-4:]
    with pytest.raises(ValueError, match="digits"):
        luhn_check_digit("12a")


def test_references_use_business_date_and_crockford_alphabet() -> None:
    # 2026-11-21 20:00 UTC is already 2026-11-22 in Ho Chi Minh City.
    ref = new_reference(datetime(2026, 11, 21, 20, 0, tzinfo=UTC))
    assert ref.startswith("VW261122")
    assert len(ref) == 18
    assert is_reference(ref)
    assert not any(ch in ref[8:] for ch in "ILOU")
    assert not is_reference("VW261122ILOU000000")
    assert not is_reference("XX2611220000000000")
