"""Deposits through the real simulator: scenarios and callback handling (FT-13..FT-16)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy import text

from app.core.clock import FixedClock
from app.modules.transactions.public import run_inquiries
from tests.integration.money_helpers import (
    WEBHOOK,
    approve_checkout,
    assert_books_clean,
    balance,
    callback_results,
    customer,
    deposit,
    drain,
    finance,
    key,
    marker,
    operation_key_of,
    settle,
    signed_callback,
    sim_admin,
    sim_tick,
)


async def test_deposit_through_simbank(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, relay: object
) -> None:
    since = marker()
    await customer(client, app)
    idem = key()
    created = await deposit(client, 250_000, idem)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["status"], body["next_action"]) == ("PENDING", "REDIRECT"), (
        body["failure_code"],
        body["events"],
    )
    assert body["checkout_url"].startswith("http://simulator.test/checkout/SIMC")
    # Same key: the same transaction, no second charge at the provider.
    again = await deposit(client, 250_000, idem)
    assert again.json()["reference"] == body["reference"]
    reused = await deposit(client, 300_000, idem)
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    ops = (await sim_admin(sim_app, "GET", "/v1/admin/operations")).json()["items"]
    assert len(ops) == 1

    redirect = await approve_checkout(sim_app, body["checkout_url"])
    assert redirect.status_code == 303
    assert redirect.headers["location"] == f"http://127.0.0.1:8080/deposits/{body['reference']}"
    await settle(app, sim_app)

    done = (await client.get(f"/api/v1/deposits/{body['reference']}")).json()
    assert (done["status"], done["next_action"], done["checkout_url"]) == (
        "COMPLETED",
        "NONE",
        None,
    )
    assert [e["to_status"] for e in done["events"]] == ["CREATED", "PENDING", "COMPLETED"]
    assert await balance(client) == 250_000
    assert await callback_results(app, since) == ["APPLIED"]
    inbox = (await client.get("/api/v1/notifications")).json()["items"]
    assert inbox[0]["title"] == "Nạp tiền thành công"
    assert "250.000 ₫" in inbox[0]["body"]
    await assert_books_clean(app)


async def test_declined_failed_and_unavailable(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI
) -> None:
    await customer(client, app)
    declined = (await deposit(client, 100_000)).json()
    await approve_checkout(sim_app, declined["checkout_url"], approve=False)
    await settle(app, sim_app)
    result = (await client.get(f"/api/v1/deposits/{declined['reference']}")).json()
    assert (result["status"], result["failure_code"]) == ("FAILED", "CUSTOMER_DECLINED")

    instant = (await deposit(client, 1_111_000)).json()  # scenario 111
    assert (instant["status"], instant["failure_code"]) == ("FAILED", "INSUFFICIENT_FUNDS")
    down = (await deposit(client, 2_777_000)).json()  # scenario 777
    assert (down["status"], down["failure_code"]) == ("FAILED", "PROVIDER_UNAVAILABLE")

    await sim_admin(sim_app, "PUT", "/v1/admin/settings", {"mode": "OUTAGE"})
    outage = (await deposit(client, 100_000)).json()
    assert outage["failure_code"] == "PROVIDER_UNAVAILABLE"
    assert await balance(client) == 0
    await assert_books_clean(app)


async def test_validation_and_limits(client: httpx.AsyncClient, app: FastAPI) -> None:
    await customer(client, app)
    assert (await client.post("/api/v1/deposits", json={"amount": "1000"})).json()["code"] == (
        "IDEMPOTENCY_KEY_REQUIRED"
    )
    assert (await deposit(client, 1_500)).json()["code"] == "AMOUNT_NOT_MULTIPLE"
    assert (await deposit(client, 6_000_000)).json()["code"] == "LIMIT_PER_TXN_EXCEEDED"
    await client.post("/api/v1/wallet/freeze", json={})
    assert (await deposit(client, 10_000)).json()["code"] == "WALLET_NOT_ACTIVE"


async def test_delayed_success_and_lost_callback(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, clock: FixedClock
) -> None:
    await customer(client, app)
    delayed = (await deposit(client, 1_222_000)).json()  # scenario 222: 2 minutes
    await approve_checkout(sim_app, delayed["checkout_url"])
    await settle(app, sim_app)
    assert (await client.get(f"/api/v1/deposits/{delayed['reference']}")).json()[
        "status"
    ] == "PENDING"
    clock.advance(timedelta(seconds=121))
    await settle(app, sim_app)
    assert (await client.get(f"/api/v1/deposits/{delayed['reference']}")).json()[
        "status"
    ] == "COMPLETED"

    # Callback never arrives: after 30 minutes the inquiry job asks the provider.
    lost = (await deposit(client, 100_000)).json()
    await approve_checkout(sim_app, lost["checkout_url"])
    await _drop_sim_callbacks(sim_app)
    clock.advance(timedelta(minutes=31))
    assert await run_inquiries(finance(app)) >= 1
    await drain(app)
    assert (await client.get(f"/api/v1/deposits/{lost['reference']}")).json()[
        "status"
    ] == "COMPLETED"
    assert await balance(client) == 1_322_000
    await assert_books_clean(app)


async def _drop_sim_callbacks(sim_app: FastAPI) -> None:
    simulator = sim_app.state.simulator
    async with simulator.db.transaction() as session:
        await session.execute(text("DELETE FROM simulator.sim_callbacks"))


async def test_duplicate_callbacks_credit_once(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, clock: FixedClock
) -> None:
    """FT-13: three identical callbacks (and one later), even delivered concurrently."""
    since = marker()
    await customer(client, app)
    body = (await deposit(client, 1_555_000)).json()  # scenario 555
    await approve_checkout(sim_app, body["checkout_url"])
    await sim_tick(sim_app)
    clock.advance(timedelta(seconds=61))
    await sim_tick(sim_app)
    # And the same signed event posted concurrently by hand.
    op_key = await operation_key_of(app, body["reference"])
    payload = {
        "event_id": "evt-concurrent-1",
        "type": "charge.succeeded",
        "provider_reference": None,
        "operation_key": op_key,
        "amount": "1555000",
        "currency": "VND",
        "failure_code": None,
        "occurred_at": clock.now().isoformat(),
    }
    raw, headers = signed_callback(clock, payload)
    answers = await asyncio.gather(
        *(client.post(WEBHOOK, content=raw, headers=headers) for _ in range(5))
    )
    assert all(a.status_code == 200 for a in answers)
    await drain(app)
    assert await balance(client) == 1_555_000
    results = await callback_results(app, since)
    assert results.count("APPLIED") == 1
    assert "IGNORED_DUPLICATE" in results
    assert "IGNORED_STALE" in results  # the hand-made event has a different id
    await assert_books_clean(app)


async def test_bad_signature_is_rejected_then_genuine_applies(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, clock: FixedClock, relay: object
) -> None:
    """FT-14: 401, stored as REJECTED, no state change; the genuine retry applies."""
    since = marker()
    await customer(client, app)
    body = (await deposit(client, 1_666_000)).json()  # scenario 666
    await approve_checkout(sim_app, body["checkout_url"])
    await sim_tick(sim_app)
    assert relay.responses[-1] == 401  # type: ignore[attr-defined]
    await drain(app)
    assert (await client.get(f"/api/v1/deposits/{body['reference']}")).json()["status"] == "PENDING"
    assert await callback_results(app, since) == ["REJECTED"]
    stale, headers = signed_callback(clock, {"event_id": "x"})
    clock.advance(timedelta(minutes=10))  # replayed old signature
    assert (await client.post(WEBHOOK, content=stale, headers=headers)).status_code == 401
    clock.advance(timedelta(seconds=-600 + 61))
    await settle(app, sim_app)
    assert (await client.get(f"/api/v1/deposits/{body['reference']}")).json()[
        "status"
    ] == "COMPLETED"
    unknown = await client.post("/api/v1/webhooks/providers/nobank", content=b"{}")
    assert unknown.status_code == 404


async def test_amount_mismatch_and_out_of_order(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, clock: FixedClock
) -> None:
    """FT-15 (wrong amount: not applied, recovery case) and FT-16 (failed after succeeded)."""
    since = marker()
    await customer(client, app)
    body = (await deposit(client, 200_000)).json()
    op_key = await operation_key_of(app, body["reference"])

    def event(event_id: str, kind: str, amount: str) -> tuple[bytes, dict[str, str]]:
        return signed_callback(
            clock,
            {
                "event_id": event_id,
                "type": kind,
                "provider_reference": None,
                "operation_key": op_key,
                "amount": amount,
                "currency": "VND",
                "failure_code": None,
                "occurred_at": clock.now().isoformat(),
            },
        )

    raw, headers = event("evt-wrong-amount", "charge.succeeded", "2000000")
    assert (await client.post(WEBHOOK, content=raw, headers=headers)).status_code == 200
    await drain(app)
    assert (await client.get(f"/api/v1/deposits/{body['reference']}")).json()["status"] == "PENDING"
    async with finance(app).db.transaction() as session:
        reasons = (
            (await session.execute(text("SELECT reason FROM app.recovery_cases"))).scalars().all()
        )
    assert reasons == ["AMOUNT_MISMATCH"]

    for event_id, kind in (("evt-ok", "charge.succeeded"), ("evt-late-fail", "charge.failed")):
        raw, headers = event(event_id, kind, "200000")
        await client.post(WEBHOOK, content=raw, headers=headers)
        await drain(app)
    assert (await client.get(f"/api/v1/deposits/{body['reference']}")).json()[
        "status"
    ] == "COMPLETED"
    assert (await callback_results(app, since))[-2:] == ["APPLIED", "IGNORED_STALE"]
    orphan, headers = signed_callback(
        clock,
        {
            "event_id": "evt-orphan",
            "type": "charge.succeeded",
            "provider_reference": None,
            "operation_key": "not-a-key",
            "amount": "1",
            "currency": "VND",
            "failure_code": None,
        },
    )
    await client.post(WEBHOOK, content=orphan, headers=headers)
    await drain(app)
    assert (await callback_results(app, since))[-1] == "ORPHAN"
    assert await balance(client) == 200_000


async def test_expiry_and_late_success(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI
) -> None:
    await customer(client, app)
    body = (await deposit(client, 300_000)).json()
    op_key = await operation_key_of(app, body["reference"])
    await sim_admin(sim_app, "POST", f"/v1/admin/operations/{op_key}/actions", {"action": "EXPIRE"})
    await settle(app, sim_app)
    assert (await client.get(f"/api/v1/deposits/{body['reference']}")).json()["status"] == "EXPIRED"
    # The provider changes its mind: never credited silently, a recovery case is opened.
    forced = await sim_admin(
        sim_app, "POST", f"/v1/admin/operations/{op_key}/actions", {"action": "SUCCEED"}
    )
    assert forced.status_code == 409  # the simulator itself refuses: already expired
    assert await balance(client) == 0


async def test_success_after_expiry_opens_a_recovery_case(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, clock: FixedClock
) -> None:
    since = marker()
    await customer(client, app)
    body = (await deposit(client, 400_000)).json()
    op_key = await operation_key_of(app, body["reference"])
    await sim_admin(sim_app, "POST", f"/v1/admin/operations/{op_key}/actions", {"action": "EXPIRE"})
    await settle(app, sim_app)
    raw, headers = signed_callback(
        clock,
        {
            "event_id": "evt-late-success",
            "type": "charge.succeeded",
            "provider_reference": None,
            "operation_key": op_key,
            "amount": "400000",
            "currency": "VND",
            "failure_code": None,
        },
    )
    await client.post(WEBHOOK, content=raw, headers=headers)
    await drain(app)
    assert (await callback_results(app, since))[-1] == "STATUS_MISMATCH"
    assert await balance(client) == 0
    async with finance(app).db.transaction() as session:
        reason = await session.scalar(text("SELECT reason FROM app.recovery_cases"))
    assert reason == "STATUS_MISMATCH"
