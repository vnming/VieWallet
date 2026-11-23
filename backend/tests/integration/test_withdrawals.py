"""Withdrawals through the real simulator (FT-17..FT-19), bank accounts, notifications."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text, update

from app.core.clock import FixedClock
from app.core.outbox import OutboxEvent
from app.modules.providers.public import SIMULATOR, ProviderOperation, SimulatorProvider
from app.modules.transactions.public import run_inquiries, withdrawal
from tests.integration.conftest import SIM_API_KEY, SIM_BASE, SIM_WEBHOOK_SECRET, CallbackRelay
from tests.integration.identity_helpers import PIN, signed_in_customer
from tests.integration.money_helpers import (
    assert_books_clean,
    balance,
    customer,
    drain,
    finance,
    fund_wallet,
    key,
    make_tier1,
    settle,
    sim_admin,
)

FEE = 1_100


async def ready_customer(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock, *, funds: int = 5_000_000
) -> str:
    """Tier 1, funded, one bank account past its cooldown. Returns the destination id."""
    wallet = await customer(client, app)
    await make_tier1(app)
    await fund_wallet(app, wallet["id"], funds)
    added = await client.post(
        "/api/v1/wallet/destinations",
        json={
            "bank_code": "SENVANG",
            "account_number": "0123456789",
            "account_name": "tran thi lan",
        },
    )
    assert added.status_code == 201, added.text
    clock.advance(timedelta(minutes=61))
    return str(added.json()["id"])


async def quote(client: httpx.AsyncClient, destination: str, amount: int) -> dict[str, Any]:
    r = await client.post(
        "/api/v1/withdrawals",
        headers={"Idempotency-Key": key()},
        json={"destination_id": destination, "amount": str(amount)},
    )
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


async def confirm(
    client: httpx.AsyncClient, reference: str, idem: str | None = None, pin: str = PIN
) -> httpx.Response:
    return await client.post(
        f"/api/v1/withdrawals/{reference}/confirm",
        headers={"Idempotency-Key": idem or key()},
        json={"pin": pin},
    )


async def status_of(client: httpx.AsyncClient, reference: str) -> dict[str, Any]:
    body: dict[str, Any] = (await client.get(f"/api/v1/withdrawals/{reference}")).json()
    return body


async def test_tier_zero_cannot_withdraw(client: httpx.AsyncClient, app: FastAPI) -> None:
    await customer(client, app)
    added = await client.post(
        "/api/v1/wallet/destinations",
        json={"bank_code": "SIMBANK", "account_number": "99887766", "account_name": "Lan"},
    )
    r = await client.post(
        "/api/v1/withdrawals",
        headers={"Idempotency-Key": key()},
        json={"destination_id": added.json()["id"], "amount": "100000"},
    )
    assert r.json()["code"] == "TXN_TYPE_NOT_ALLOWED"


async def test_successful_withdrawal(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, clock: FixedClock
) -> None:
    destination = await ready_customer(client, app, clock)
    q = await quote(client, destination, 1_000_000)
    assert (q["status"], q["fee"]["amount"], q["total"]["amount"]) == ("CREATED", "1100", "1001100")
    assert q["destination"]["account_masked"] == "••••6789"

    wrong = await confirm(client, q["reference"], pin="999999")
    assert wrong.json()["code"] in {"PIN_INVALID", "PIN_WEAK"}
    assert (await status_of(client, q["reference"]))["status"] == "CREATED"

    idem = key()
    held = await confirm(client, q["reference"], idem)
    assert (held.json()["status"], held.json()["next_action"]) == ("PENDING", "WAIT_PROVIDER")
    assert await balance(client) == 5_000_000 - 1_001_100
    replay = await confirm(client, q["reference"], idem)
    assert replay.json()["status"] == "PENDING"
    assert (await confirm(client, q["reference"])).json()["code"] == "TXN_INVALID_STATE"

    await drain(app)  # dispatch: the payout is sent after commit
    assert (await status_of(client, q["reference"]))["status"] == "PROCESSING"
    await settle(app, sim_app)
    done = await status_of(client, q["reference"])
    assert done["status"] == "COMPLETED"
    assert [e["to_status"] for e in done["events"]] == [
        "CREATED",
        "PENDING",
        "PROCESSING",
        "COMPLETED",
    ]
    assert await balance(client) == 5_000_000 - 1_001_100
    titles = [n["title"] for n in (await client.get("/api/v1/notifications")).json()["items"]]
    assert titles[:2] == ["Rút tiền thành công", "Đã nhận lệnh rút tiền"]
    await assert_books_clean(app)


async def test_failed_payout_returns_amount_and_fee(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, clock: FixedClock
) -> None:
    destination = await ready_customer(client, app, clock)
    q = await quote(client, destination, 1_111_000)  # scenario 111: bank refuses
    await confirm(client, q["reference"])
    await settle(app, sim_app)
    failed = await status_of(client, q["reference"])
    assert (failed["status"], failed["failure_code"]) == ("FAILED", "ACCOUNT_CLOSED")
    assert await balance(client) == 5_000_000
    await assert_books_clean(app)


class LosingTransport(httpx.AsyncBaseTransport):
    """Forwards a payout to the simulator, then loses the response (read timeout)."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        if request.method == "POST" and request.url.path == "/v1/payouts":
            await response.aread()
            raise httpx.ReadTimeout("response lost", request=request)
        return response


@pytest.fixture
def losing_provider(app: FastAPI, sim_app: FastAPI, clock: FixedClock) -> SimulatorProvider:
    provider = SimulatorProvider.build(
        SIM_BASE,
        api_key=SIM_API_KEY,
        webhook_secret=SIM_WEBHOOK_SECRET,
        transport=LosingTransport(httpx.ASGITransport(app=sim_app)),
        now=lambda: clock.now().timestamp(),
    )
    finance(app).providers.providers[SIMULATOR] = provider
    return provider


@pytest.mark.parametrize(
    ("amount", "final", "expected_balance"),
    [
        (1_000_000, "COMPLETED", 5_000_000 - 1_001_100),  # FT-17
        (1_111_000, "FAILED", 5_000_000),  # FT-18: refund of amount + fee
    ],
)
async def test_timeout_then_inquiry_resolves(
    client: httpx.AsyncClient,
    app: FastAPI,
    sim_app: FastAPI,
    clock: FixedClock,
    relay: CallbackRelay,
    losing_provider: SimulatorProvider,
    amount: int,
    final: str,
    expected_balance: int,
) -> None:
    destination = await ready_customer(client, app, clock)
    q = await quote(client, destination, amount)
    await confirm(client, q["reference"])
    relay.drop = True  # and the callback is lost as well
    await drain(app)
    assert (await status_of(client, q["reference"]))["status"] == "PROCESSING"
    async with finance(app).db.transaction() as session:
        op_status = await session.scalar(
            text("SELECT status FROM app.provider_operations WHERE operation = 'PAYOUT'")
        )
    assert op_status == "UNKNOWN"
    await settle(app, sim_app)  # the simulator completes the payout; the callback is lost
    clock.advance(timedelta(seconds=31))
    assert await run_inquiries(finance(app)) == 1
    await drain(app)
    assert (await status_of(client, q["reference"]))["status"] == final
    assert await balance(client) == expected_balance
    ops = (await sim_admin(sim_app, "GET", "/v1/admin/operations")).json()["items"]
    assert len(ops) == 1  # exactly one payout at the provider
    await assert_books_clean(app)


async def test_crash_before_dispatch_is_recovered_once(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, clock: FixedClock
) -> None:
    """FT-19: the process died after marking the payout SUBMITTED, before calling the bank."""
    destination = await ready_customer(client, app, clock)
    q = await quote(client, destination, 500_000)
    await confirm(client, q["reference"])
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        await session.execute(
            update(ProviderOperation)
            .where(ProviderOperation.operation == "PAYOUT")
            .values(
                status="SUBMITTED", attempt=1, next_inquiry_at=clock.now() + timedelta(minutes=1)
            )
        )
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.topic == "withdrawal.dispatch_requested")
            .values(status="DONE")
        )
    clock.advance(timedelta(minutes=2))
    await run_inquiries(ctx)  # the bank never heard of it -> send again with the same key
    await drain(app)
    await drain(app)  # a duplicate dispatch event would be ignored
    await settle(app, sim_app)
    assert (await status_of(client, q["reference"]))["status"] == "COMPLETED"
    ops = (await sim_admin(sim_app, "GET", "/v1/admin/operations")).json()["items"]
    assert len(ops) == 1
    await assert_books_clean(app)


async def test_cancel_expire_and_insufficient_funds(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, clock: FixedClock
) -> None:
    destination = await ready_customer(client, app, clock, funds=2_000_000)
    first = await quote(client, destination, 500_000)
    await confirm(client, first["reference"])
    cancelled = (await client.post(f"/api/v1/withdrawals/{first['reference']}/cancel")).json()
    assert cancelled["status"] == "CANCELLED"
    assert await balance(client) == 2_000_000
    await drain(app)  # the dispatch event finds nothing to send
    ops = (await sim_admin(sim_app, "GET", "/v1/admin/operations")).json()["items"]
    assert ops == []

    second = await quote(client, destination, 500_000)
    await confirm(client, second["reference"])
    await drain(app)
    late = await client.post(f"/api/v1/withdrawals/{second['reference']}/cancel")
    assert late.json()["code"] == "TXN_INVALID_STATE"

    stale = await quote(client, destination, 100_000)
    clock.advance(timedelta(minutes=11))
    expired = (await confirm(client, stale["reference"])).json()
    assert (expired["status"], expired["next_action"]) == ("EXPIRED", "NONE")
    leftover = await quote(client, destination, 100_000)
    clock.advance(timedelta(minutes=11))
    assert await withdrawal.expire_quotes(finance(app)) == 1
    assert (await status_of(client, leftover["reference"]))["status"] == "EXPIRED"

    too_big = await quote(client, destination, 1_900_000)
    result = (await confirm(client, too_big["reference"])).json()
    assert (result["status"], result["failure_code"]) == ("FAILED", "INSUFFICIENT_BALANCE")
    await settle(app, sim_app)
    await assert_books_clean(app)


async def test_new_account_cooldown_blocks(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    wallet = await customer(client, app)
    await make_tier1(app)
    await fund_wallet(app, wallet["id"], 1_000_000)
    added = await client.post(
        "/api/v1/wallet/destinations",
        json={"bank_code": "SENVANG", "account_number": "0123456789", "account_name": "Lan"},
    )
    assert added.json()["usable_from"] == "2026-11-21T09:00:00Z"
    q = await quote(client, added.json()["id"], 100_000)
    blocked = (await confirm(client, q["reference"])).json()
    assert (blocked["status"], blocked["failure_code"]) == ("FAILED", "RISK_BLOCKED")
    assert await balance(client) == 1_000_000
    async with finance(app).db.transaction() as session:
        decision = await session.scalar(
            text(
                "SELECT r.decision FROM app.risk_assessments r JOIN app.transactions t"
                " ON t.id = r.transaction_id WHERE t.reference = :r"
            ),
            {"r": q["reference"]},
        )
    assert decision == "BLOCK"


async def test_payout_returned_by_the_bank(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, clock: FixedClock
) -> None:
    destination = await ready_customer(client, app, clock)
    q = await quote(client, destination, 1_999_000)  # scenario 999: returned after 5 minutes
    await confirm(client, q["reference"])
    await settle(app, sim_app)
    assert (await status_of(client, q["reference"]))["status"] == "COMPLETED"
    clock.advance(timedelta(seconds=301))
    await settle(app, sim_app)
    assert (await status_of(client, q["reference"]))["status"] == "REVERSED"
    assert await balance(client) == 5_000_000  # amount and fee came back
    history = (await client.get("/api/v1/transactions?type=REVERSAL")).json()["items"]
    assert history[0]["direction"] == "IN"
    assert history[0]["total"]["amount"] == str(1_999_000 + FEE)
    await assert_books_clean(app)


async def test_provider_outage_retries_then_fails(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, clock: FixedClock
) -> None:
    destination = await ready_customer(client, app, clock)
    q = await quote(client, destination, 300_000)
    await confirm(client, q["reference"])
    await sim_admin(sim_app, "PUT", "/v1/admin/settings", {"mode": "OUTAGE"})
    for _ in range(4):  # attempts are retried by the outbox with back-off
        await drain(app)
        clock.advance(timedelta(minutes=5))
    failed = await status_of(client, q["reference"])
    assert (failed["status"], failed["failure_code"]) == ("FAILED", "PROVIDER_UNAVAILABLE")
    assert await balance(client) == 5_000_000
    await assert_books_clean(app)


async def test_bank_accounts(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await customer(client, app)
    banks = (await client.get("/api/v1/wallet/banks")).json()
    assert {b["code"] for b in banks} >= {"SIMBANK", "SENVANG"}
    bad = await client.post(
        "/api/v1/wallet/destinations",
        json={"bank_code": "REALBANK", "account_number": "12ab", "account_name": "x y"},
    )
    assert bad.status_code == 422
    for number in ("11111111", "22222222", "33333333", "44444444", "55555555"):
        r = await client.post(
            "/api/v1/wallet/destinations",
            json={"bank_code": "SIMBANK", "account_number": number, "account_name": "Lan"},
        )
        assert r.status_code == 201, r.text
        assert number not in r.text  # the full number is never echoed
    dup = await client.post(
        "/api/v1/wallet/destinations",
        json={"bank_code": "SIMBANK", "account_number": "11111111", "account_name": "Lan"},
    )
    assert dup.json()["code"] in {"DESTINATION_LIMIT", "DESTINATION_DUPLICATE"}
    listed = (await client.get("/api/v1/wallet/destinations")).json()
    assert listed[0]["account_number_masked"] == "••••1111"
    first = listed[0]["id"]
    await signed_in_customer(other_client, app, email="binh@example.com")
    assert (await other_client.delete(f"/api/v1/wallet/destinations/{first}")).status_code == 404
    assert (await client.delete(f"/api/v1/wallet/destinations/{first}")).status_code == 204
    again = await client.post(
        "/api/v1/wallet/destinations",
        json={"bank_code": "SIMBANK", "account_number": "11111111", "account_name": "Lan"},
    )
    assert again.status_code == 201
    async with finance(app).db.transaction() as session:
        stored = await session.scalar(
            text("SELECT account_number_encrypted FROM app.bank_destinations LIMIT 1")
        )
    assert stored is not None
    assert "1111" not in stored


async def test_notification_inbox(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    app: FastAPI,
    sim_app: FastAPI,
    clock: FixedClock,
) -> None:
    destination = await ready_customer(client, app, clock)
    q = await quote(client, destination, 1_111_000)
    await confirm(client, q["reference"])
    await settle(app, sim_app)
    assert (await client.get("/api/v1/notifications/unread-count")).json() == {"unread": 2}
    items = (await client.get("/api/v1/notifications?limit=1")).json()
    assert items["next_cursor"] is not None
    newest = items["items"][0]
    assert newest["title"] == "Rút tiền không thành công"
    assert newest["data"]["link"] == f"/history/{q['reference']}"
    await signed_in_customer(other_client, app, email="binh@example.com")
    assert (
        await other_client.post(f"/api/v1/notifications/{newest['id']}/read")
    ).status_code == 404
    assert (await client.post(f"/api/v1/notifications/{newest['id']}/read")).status_code == 204
    assert (await client.get("/api/v1/notifications?unread=true")).json()["items"][0][
        "id"
    ] != newest["id"]
    assert (await client.post("/api/v1/notifications/read-all")).json() == {"unread": 0}
    await drain(app)  # replaying events never duplicates notifications
    assert len((await client.get("/api/v1/notifications")).json()["items"]) == 2
