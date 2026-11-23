"""Pure pieces of Phase 5: name masking, payment request codes and tags, net reversal,
notification template selection, payability rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.money import Money
from app.modules.ledger.domain import (
    JournalDraft,
    JournalKind,
    LedgerError,
    Side,
    credit,
    debit,
    net_reversal,
)
from app.modules.notifications.inapp import template_for
from app.modules.payments.models import PaymentRequest
from app.modules.payments.service import (
    PaymentRequestAmountError,
    PaymentRequestClosedError,
    PaymentRequestNotForYouError,
    PaymentRequestNotFoundError,
    effective_status,
    ensure_payable,
    new_code,
    normalize_code,
    sign,
    verify,
)
from app.modules.transactions.use_cases.transfer import clean_note
from app.modules.wallet.recipients import mask_name

NOW = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
KEY = b"k" * 32


def vnd(amount: int) -> Money:
    return Money(amount, "VND")


@pytest.mark.parametrize(
    ("full", "masked"),
    [
        ("Nguyễn Văn An", "NGUYEN V*** A"),
        ("Trần Thị Bích Ngọc", "TRAN T*** B*** N"),
        ("Đỗ Đức", "DO D"),
        ("Lan", "L***"),
        ("  ", "***"),
    ],
)
def test_mask_name(full: str, masked: str) -> None:
    assert mask_name(full) == masked


def test_codes_are_crockford_and_normalized() -> None:
    code = new_code()
    assert len(code) == 16
    assert normalize_code(code.lower()) == code
    assert normalize_code("o1il" + code[4:]) == "0111" + code[4:]  # look-alikes accepted
    assert normalize_code("U" * 16) is None  # U is not in the alphabet
    assert normalize_code("short") is None


def _request(**overrides: object) -> PaymentRequest:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "code": "ABCDEFGHJKMNPQRS",
        "issuer_type": "MERCHANT",
        "issuer_user_id": uuid.uuid4(),
        "destination_wallet_id": uuid.uuid4(),
        "amount_minor": 150_000,
        "currency": "VND",
        "single_use": True,
        "status": "OPEN",
        "payer_user_id": None,
        "paid_count": 0,
        "expires_at": NOW + timedelta(minutes=15),
        "signature": "",
    }
    values.update(overrides)
    request = PaymentRequest(**values)
    request.signature = sign(KEY, request)
    return request


def test_tag_covers_where_the_money_goes() -> None:
    request = _request()
    verify(KEY, request)
    for field, value in (
        ("amount_minor", 1_000),
        ("destination_wallet_id", uuid.uuid4()),
        ("single_use", False),
        ("expires_at", NOW + timedelta(days=1)),
    ):
        tampered = _request()
        setattr(tampered, field, value)
        with pytest.raises(PaymentRequestNotFoundError):
            verify(KEY, tampered)
    with pytest.raises(PaymentRequestNotFoundError):
        verify(b"x" * 32, request)


def test_payability() -> None:
    payer = uuid.uuid4()
    request = _request()
    assert ensure_payable(request, payer_id=payer, amount=None, now=NOW) == vnd(150_000)
    with pytest.raises(PaymentRequestAmountError):
        ensure_payable(request, payer_id=payer, amount=vnd(1), now=NOW)
    with pytest.raises(PaymentRequestNotForYouError):
        ensure_payable(request, payer_id=request.issuer_user_id, amount=None, now=NOW)
    with pytest.raises(PaymentRequestClosedError):
        ensure_payable(request, payer_id=payer, amount=None, now=NOW + timedelta(hours=1))
    assert effective_status(request, NOW + timedelta(hours=1)).value == "EXPIRED"
    addressed = _request(payer_user_id=uuid.uuid4())
    with pytest.raises(PaymentRequestNotForYouError):
        ensure_payable(addressed, payer_id=payer, amount=None, now=NOW)
    open_amount = _request(amount_minor=None)
    with pytest.raises(PaymentRequestAmountError):
        ensure_payable(open_amount, payer_id=payer, amount=None, now=NOW)
    assert ensure_payable(open_amount, payer_id=payer, amount=vnd(7), now=NOW) == vnd(7)


def test_net_reversal_of_a_held_transfer_leaves_hold_untouched() -> None:
    sender, receiver, hold, fees = (uuid.uuid4() for _ in range(4))
    held = JournalDraft(
        JournalKind.TRANSFER_HOLD, (debit(sender, vnd(1_100)), credit(hold, vnd(1_100)))
    )
    release = JournalDraft(
        JournalKind.TRANSFER_RELEASE_TO_RECEIVER,
        (debit(hold, vnd(1_100)), credit(receiver, vnd(1_000)), credit(fees, vnd(100))),
    )
    undo = net_reversal([held, release])
    assert undo.kind is JournalKind.REVERSAL
    by_account = {line.account_id: (line.side, line.amount.amount_minor) for line in undo.lines}
    assert by_account == {
        sender: (Side.CREDIT, 1_100),
        receiver: (Side.DEBIT, 1_000),
        fees: (Side.DEBIT, 100),
    }
    with pytest.raises(LedgerError):
        net_reversal([undo])
    with pytest.raises(LedgerError):
        net_reversal([])


def test_templates_pick_direction_and_subtype() -> None:
    sent = template_for("TRANSFER", "CREATED", "COMPLETED", "OUT")
    got = template_for("TRANSFER", "CREATED", "COMPLETED", "IN")
    assert sent is not None
    assert got is not None
    assert sent.title["vi"] == "Chuyển tiền thành công"
    assert got.title["vi"] == "Bạn nhận được tiền"
    assert template_for("TRANSFER", "CREATED", "PENDING", "IN") is None  # receiver: not yet
    staff = template_for("REVERSAL", "PENDING", "COMPLETED", "IN", "STAFF")
    bank = template_for("REVERSAL", "CREATED", "COMPLETED", "IN")
    assert staff is not None
    assert bank is not None
    assert staff.title["vi"] != bank.title["vi"]
    withdrawal = template_for("WITHDRAWAL", "PROCESSING", "COMPLETED", "OUT")
    assert withdrawal is not None
    assert withdrawal.title["vi"] == "Rút tiền thành công"


def test_clean_note() -> None:
    assert clean_note("  Tiền\ncơm\x00 ") == "Tiền cơm"
    assert len(clean_note("x" * 500)) == 140
