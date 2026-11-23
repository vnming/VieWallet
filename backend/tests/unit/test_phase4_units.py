"""Pure parts of Phase 4: callback verification, inquiry back-off, notification texts, risk."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.notifications.inapp import format_vnd, render, template_for
from app.modules.providers.public import (
    InvalidCallback,
    Outcome,
    SimulatorProvider,
    expected_signature,
    next_inquiry_delay,
)
from app.modules.risk.domain import Decision, RiskContext, evaluate
from sim.signing import signature

SECRET = "unit-test-secret"  # gitleaks:allow
NOW = 1_800_000_000


def adapter() -> SimulatorProvider:
    import httpx  # noqa: PLC0415

    return SimulatorProvider(
        httpx.AsyncClient(), api_key="k", webhook_secret=SECRET, now=lambda: NOW
    )


def signed(
    body: Mapping[str, object], *, at: int = NOW, secret: str = SECRET
) -> tuple[dict[str, str], bytes]:
    raw = json.dumps(body).encode()
    return {
        "X-Sim-Timestamp": str(at),
        "X-Sim-Signature": expected_signature(secret, str(at), raw),
    }, raw


EVENT = {
    "event_id": "evt_1",
    "type": "payout.returned",
    "operation_key": "op",
    "provider_reference": "SIMP1",
    "amount": "1000",
    "currency": "VND",
}


def test_simulator_and_adapter_agree_on_signatures() -> None:
    assert signature(SECRET, "1", b"{}") == expected_signature(SECRET, "1", b"{}")


def test_valid_callback_is_parsed() -> None:
    headers, raw = signed(EVENT)
    event = adapter().verify_callback(headers, raw)
    assert (event.event_id, event.amount_minor, event.outcome) == ("evt_1", 1000, Outcome.RETURNED)


@pytest.mark.parametrize(
    "case",
    ["wrong_secret", "old", "future", "no_timestamp", "tampered", "malformed"],
)
def test_invalid_callbacks(case: str) -> None:
    headers, raw = signed(EVENT)
    if case == "wrong_secret":
        headers, raw = signed(EVENT, secret="other")
    elif case == "old":
        headers, raw = signed(EVENT, at=NOW - 301)
    elif case == "future":
        headers, raw = signed(EVENT, at=NOW + 301)
    elif case == "no_timestamp":
        headers.pop("X-Sim-Timestamp")
    elif case == "tampered":
        raw = raw.replace(b"1000", b"9000")
    else:
        headers, raw = signed({"event_id": "x"})
    with pytest.raises(InvalidCallback):
        adapter().verify_callback(headers, raw)


def test_inquiry_backoff() -> None:
    assert [next_inquiry_delay(i).total_seconds() for i in range(7)] == [
        30,
        60,
        120,
        300,
        900,
        3600,
        3600,
    ]


def test_notification_texts() -> None:
    assert format_vnd(1_250_000, "vi") == "1.250.000 ₫"
    assert format_vnd(-5_000, "en") == "-5,000 VND"
    cancelled = template_for("WITHDRAWAL", "PENDING", "CANCELLED")
    assert cancelled is not None
    assert template_for("WITHDRAWAL", "CREATED", "CANCELLED") is None  # nothing was held
    title, body = render(cancelled, "en", {"total": "1,001,100 VND", "reference": "VW1"})
    assert (title, body) == (
        "Withdrawal cancelled",
        "1,001,100 VND was returned to your wallet. Ref VW1.",
    )
    assert template_for("DEPOSIT", "CREATED", "CREATED") is None


def test_new_destination_cooldown_blocks() -> None:
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    base = {
        "user_id": uuid.uuid4(),
        "txn_type": "WITHDRAWAL",
        "amount_minor": 100_000,
        "currency": "VND",
        "now": now,
    }
    fresh = evaluate(RiskContext(**base, destination_added_at=now - timedelta(minutes=59)))  # type: ignore[arg-type]
    assert fresh.decision is Decision.BLOCK
    assert fresh.hits[0].code == "NEW_DESTINATION_COOLDOWN"
    ok = evaluate(RiskContext(**base, destination_added_at=now - timedelta(minutes=60)))  # type: ignore[arg-type]
    assert ok.decision is Decision.ALLOW
    none = evaluate(
        RiskContext(**base, destination_added_at=now, destination_cooldown=timedelta(0))  # type: ignore[arg-type]
    )
    assert none.decision is Decision.ALLOW
