"""QR payments, payment requests, refunds (FT-20, FT-21, FT-32) and reversals (FT-22, FT-23)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.core.clock import FixedClock
from app.modules.payments.public import expire_due
from app.modules.risk.public import Decision
from tests.integration.finance_helpers import sign_in_staff
from tests.integration.money_helpers import (
    assert_books_clean,
    balance,
    customer,
    drain,
    finance,
    fund_wallet,
    key,
)
from tests.integration.spend_helpers import (
    OTHER,
    confirm,
    journal_count,
    make_merchant,
    risk_decision,
    send,
    two_customers,
)

THIRD = "ha@example.com"


@pytest.fixture
async def third_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://viewallet.test", headers={"X-VW-CSRF": "1"}
    ) as http:
        yield http


async def create_request(client: httpx.AsyncClient, **body: Any) -> dict[str, Any]:
    r = await client.post("/api/v1/payment-requests", headers={"Idempotency-Key": key()}, json=body)
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


async def pay(
    client: httpx.AsyncClient, code: str, amount: int | None = None, idem: str | None = None
) -> httpx.Response:
    return await client.post(
        f"/api/v1/payment-requests/{code}/pay",
        headers={"Idempotency-Key": idem or key()},
        json={"amount": str(amount) if amount is not None else None},
    )


async def pay_and_confirm(
    client: httpx.AsyncClient, code: str, amount: int | None = None
) -> dict[str, Any]:
    quoted = await pay(client, code, amount)
    assert quoted.status_code == 201, quoted.text
    kind = "payments" if quoted.json()["type"] == "PAYMENT" else "transfers"
    r = await confirm(client, quoted.json()["reference"], kind=kind)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def shop(
    client: httpx.AsyncClient, merchant_client: httpx.AsyncClient, app: FastAPI
) -> dict[str, Any]:
    """Lan (funded customer) and Minh's simulated shop 'Cà phê Mây'."""
    await two_customers(client, merchant_client, app)
    return await make_merchant(app)


async def merchant_balance(merchant_client: httpx.AsyncClient) -> int:
    return int((await merchant_client.get("/api/v1/merchant")).json()["balance"]["amount"])


# ---------------------------------------------------------------- merchant QR


async def test_single_use_merchant_qr_payment(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await shop(client, other_client, app)
    request = await create_request(
        other_client, kind="MERCHANT", amount="150000", description="2 cà phê sữa"
    )
    assert request["url"] == f"http://127.0.0.1:8080/pay/{request['code']}"
    assert request["is_mine"] is True
    seen = (await client.get(f"/api/v1/payment-requests/{request['code'].lower()}")).json()
    assert seen["issuer"] == {
        "kind": "MERCHANT",
        "name": "Cà phê Mây",
        "wallet_number_masked": seen["issuer"]["wallet_number_masked"],
        "mcc": "5814",
    }
    assert seen["amount"] == {"amount": "150000", "currency": "VND"}
    assert seen["status"] == "OPEN"
    assert seen["is_mine"] is False

    wrong = await pay(client, request["code"], 1_000)
    assert wrong.json()["code"] == "PAYMENT_REQUEST_AMOUNT_MISMATCH"
    paid = await pay_and_confirm(client, request["code"])
    assert paid["type"] == "PAYMENT"
    assert paid["status"] == "COMPLETED"
    assert paid["counterparty"]["name"] == "Cà phê Mây"
    assert paid["payment_request_code"] == request["code"]
    assert await balance(client) == 2_850_000
    assert await merchant_balance(other_client) == 150_000
    assert await balance(other_client) == 0  # the shop wallet is separate from Minh's own
    after = (await client.get(f"/api/v1/payment-requests/{request['code']}")).json()
    assert after["status"] == "PAID"
    again = await pay(client, request["code"])
    assert again.json()["code"] == "PAYMENT_REQUEST_CLOSED"

    listed = (await other_client.get("/api/v1/merchant/payments")).json()["items"]
    assert [p["reference"] for p in listed] == [paid["reference"]]
    assert listed[0]["payer"]["name"] == "TRAN T*** L"
    assert listed[0]["refundable"]["amount"] == "150000"
    history = (await client.get("/api/v1/transactions")).json()["items"]
    assert history[0]["type"] == "PAYMENT"
    assert history[0]["counterparty"]["name"] == "Cà phê Mây"
    await drain(app)
    titles = [n["title"] for n in (await other_client.get("/api/v1/notifications")).json()["items"]]
    assert "Cửa hàng nhận được thanh toán" in titles
    await assert_books_clean(app)


async def test_ft32_single_use_request_paid_by_two_people_at_once(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    third_client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    await shop(client, other_client, app)
    ha = await customer(third_client, app, email=THIRD)
    await fund_wallet(app, ha["id"], 1_000_000)
    request = await create_request(other_client, kind="MERCHANT", amount="200000")
    lan_quote = (await pay(client, request["code"])).json()
    ha_quote = (await pay(third_client, request["code"])).json()
    results = await asyncio.gather(
        confirm(client, lan_quote["reference"], kind="payments"),
        confirm(third_client, ha_quote["reference"], kind="payments"),
    )
    statuses = sorted(r.json()["status"] for r in results)
    assert statuses == ["COMPLETED", "FAILED"]
    failed = next(r.json() for r in results if r.json()["status"] == "FAILED")
    assert failed["failure_code"] == "PAYMENT_REQUEST_CLOSED"
    assert await merchant_balance(other_client) == 200_000
    assert await balance(client) + await balance(third_client) == 3_800_000
    await assert_books_clean(app)


async def test_reusable_open_amount_qr(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await shop(client, other_client, app)
    request = await create_request(other_client, kind="MERCHANT", single_use=False)
    assert request["amount"] is None
    missing = await pay(client, request["code"])
    assert missing.json()["code"] == "PAYMENT_REQUEST_AMOUNT_MISMATCH"
    await pay_and_confirm(client, request["code"], 45_000)
    await pay_and_confirm(client, request["code"], 30_000)
    state = (await other_client.get(f"/api/v1/payment-requests/{request['code']}")).json()
    assert state["status"] == "OPEN"
    assert state["paid_count"] == 2
    assert await merchant_balance(other_client) == 75_000


async def test_request_validation_and_lifecycle(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    await two_customers(client, other_client, app)
    no_shop = await client.post(
        "/api/v1/payment-requests", headers={"Idempotency-Key": key()}, json={"kind": "MERCHANT"}
    )
    assert no_shop.json()["code"] == "MERCHANT_NOT_FOUND"
    no_amount = await client.post(
        "/api/v1/payment-requests", headers={"Idempotency-Key": key()}, json={"kind": "USER"}
    )
    assert no_amount.status_code == 422
    assert (await client.get("/api/v1/payment-requests/not-a-code")).status_code == 404
    assert (await client.get("/api/v1/payment-requests/0000000000000000")).status_code == 404

    await make_merchant(app)
    short = await create_request(
        other_client, kind="MERCHANT", amount="10000", expires_in_minutes=5
    )
    cancelled = await create_request(other_client, kind="MERCHANT", amount="10000")
    r = await other_client.post(f"/api/v1/payment-requests/{cancelled['code']}/cancel")
    assert r.json()["status"] == "CANCELLED"
    assert (await pay(client, cancelled["code"])).json()["code"] == "PAYMENT_REQUEST_CLOSED"
    assert (
        await client.post(f"/api/v1/payment-requests/{short['code']}/cancel")
    ).status_code == 404  # only the issuer cancels

    quoted = (await pay(client, short["code"])).json()
    clock.advance(timedelta(minutes=6))
    assert (await client.get(f"/api/v1/payment-requests/{short['code']}")).json()[
        "status"
    ] == "EXPIRED"
    late = await confirm(client, quoted["reference"], kind="payments")
    assert late.json()["status"] == "EXPIRED"  # the quote never outlives its request
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        assert await expire_due(session, ctx.clock) == 1


async def test_tampered_request_is_refused(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await shop(client, other_client, app)
    request = await create_request(other_client, kind="MERCHANT", amount="150000")
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        await session.execute(
            text("UPDATE app.payment_requests SET amount_minor = 1000 WHERE code = :c"),
            {"c": request["code"]},
        )
    assert (await client.get(f"/api/v1/payment-requests/{request['code']}")).status_code == 404
    assert (await pay(client, request["code"])).status_code == 404


# ---------------------------------------------------------------- request money (P2P)


async def test_request_money_from_a_person_is_paid_as_a_transfer(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    third_client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    await two_customers(client, other_client, app)
    await customer(third_client, app, email=THIRD)
    ask = await create_request(
        other_client, kind="USER", amount="120000", description="Tiền vé xem phim",
        payer_email="lan@example.com",
    )  # fmt: skip
    assert ask["payer_name"] == "TRAN T*** L"
    incoming = (await client.get("/api/v1/payment-requests", params={"role": "incoming"})).json()
    assert [r["code"] for r in incoming] == [ask["code"]]
    assert incoming[0]["addressed_to_me"] is True
    # Addressed to Lan: nobody else can see or pay it.
    assert (await third_client.get(f"/api/v1/payment-requests/{ask['code']}")).status_code == 404
    paid = await pay_and_confirm(client, ask["code"])
    assert paid["type"] == "TRANSFER"
    assert paid["status"] == "COMPLETED"
    assert paid["description"] == "Tiền vé xem phim"
    assert await balance(other_client) == 120_000
    issued = (await other_client.get("/api/v1/payment-requests")).json()
    assert issued[0]["status"] == "PAID"

    second = await create_request(
        other_client, kind="USER", amount="50000", payer_wallet_number=None
    )
    declined = await client.post(f"/api/v1/payment-requests/{second['code']}/decline")
    assert declined.status_code == 404  # not addressed to anyone: nothing to decline
    third = await create_request(
        other_client, kind="USER", amount="50000", payer_email="lan@example.com"
    )
    declined = await client.post(f"/api/v1/payment-requests/{third['code']}/decline")
    assert declined.json()["status"] == "DECLINED"
    assert (await pay(client, third["code"])).json()["code"] == "PAYMENT_REQUEST_CLOSED"


# ---------------------------------------------------------------- refunds


async def test_ft20_partial_refunds_up_to_the_amount(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await shop(client, other_client, app)
    request = await create_request(other_client, kind="MERCHANT", amount="300000")
    paid = await pay_and_confirm(client, request["code"])
    ref = paid["reference"]

    async def refund(amount: int) -> httpx.Response:
        return await other_client.post(
            f"/api/v1/merchant/payments/{ref}/refunds",
            headers={"Idempotency-Key": key()},
            json={"amount": str(amount), "reason": "khách trả món"},
        )

    first = await refund(100_000)
    assert first.status_code == 201, first.text
    assert first.json()["type"] == "REFUND"
    assert first.json()["status"] == "COMPLETED"
    state = (await other_client.get(f"/api/v1/merchant/payments/{ref}")).json()
    assert state["status"] == "PARTIALLY_REFUNDED"
    assert state["refundable"]["amount"] == "200000"
    assert (await refund(100_000)).json()["status"] == "COMPLETED"
    too_much = await refund(150_000)
    assert too_much.status_code == 422
    assert too_much.json()["code"] == "REFUND_EXCEEDS_REMAINING"
    assert (await refund(100_000)).json()["status"] == "COMPLETED"
    state = (await other_client.get(f"/api/v1/merchant/payments/{ref}")).json()
    assert state["status"] == "REFUNDED"
    assert state["refunded"]["amount"] == "300000"
    assert (await refund(1_000)).json()["code"] == "TXN_INVALID_STATE"
    assert await balance(client) == 3_000_000
    assert await merchant_balance(other_client) == 0

    seen = (await client.get("/api/v1/transactions", params={"type": "REFUND"})).json()["items"]
    assert len(seen) == 3
    assert seen[0]["direction"] == "IN"
    assert seen[0]["counterparty"]["name"] == "Cà phê Mây"
    # Lan is not this payment's merchant.
    not_mine = await client.post(
        f"/api/v1/merchant/payments/{ref}/refunds",
        headers={"Idempotency-Key": key()},
        json={"amount": "1000", "reason": "thử"},
    )
    assert not_mine.json()["code"] == "MERCHANT_NOT_FOUND"
    await drain(app)
    titles = [n["title"] for n in (await client.get("/api/v1/notifications")).json()["items"]]
    assert "Bạn được hoàn tiền" in titles
    await assert_books_clean(app)


async def test_ft21_concurrent_refunds_never_exceed_the_payment(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await shop(client, other_client, app)
    request = await create_request(other_client, kind="MERCHANT", amount="300000")
    ref = (await pay_and_confirm(client, request["code"]))["reference"]
    results = await asyncio.gather(
        *(
            other_client.post(
                f"/api/v1/merchant/payments/{ref}/refunds",
                headers={"Idempotency-Key": key()},
                json={"amount": "100000", "reason": "hoàn đồng thời"},
            )
            for _ in range(5)
        )
    )
    created = [r for r in results if r.status_code == 201]
    assert len(created) == 3
    assert {r.json()["code"] for r in results if r.status_code != 201} <= {
        "REFUND_EXCEEDS_REMAINING",
        "TXN_INVALID_STATE",
    }
    assert await balance(client) == 3_000_000
    await assert_books_clean(app)


async def test_payment_review_hold_reject_reopens_the_request(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await shop(client, other_client, app)
    request = await create_request(other_client, kind="MERCHANT", amount="250000")
    with risk_decision(monkeypatch, Decision.REVIEW):
        held = await pay_and_confirm(client, request["code"])
    assert held["status"] == "PENDING"
    assert held["next_action"] == "WAIT_REVIEW"
    assert (await client.get(f"/api/v1/payment-requests/{request['code']}")).json()[
        "status"
    ] == "PAID"  # reserved while under review

    staff = await _staff(app, "risk@example.com", "RISK_ANALYST")
    try:
        r = await staff.post(
            f"/api/v1/admin/risk/holds/{held['reference']}/reject", json={"note": "không rõ"}
        )
        assert r.json()["status"] == "CANCELLED"
        assert (await client.get(f"/api/v1/payment-requests/{request['code']}")).json()[
            "status"
        ] == "OPEN"
        assert await balance(client) == 3_000_000
        with risk_decision(monkeypatch, Decision.REVIEW):
            held = await pay_and_confirm(client, request["code"])
        r = await staff.post(f"/api/v1/admin/risk/holds/{held['reference']}/approve", json={})
        assert r.json()["status"] == "COMPLETED"
    finally:
        await staff.aclose()
    assert await merchant_balance(other_client) == 250_000
    await assert_books_clean(app)


# ---------------------------------------------------------------- reversals (maker-checker)


async def _staff(app: FastAPI, email: str, *roles: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    staff = httpx.AsyncClient(
        transport=transport, base_url="http://viewallet.test", headers={"X-VW-CSRF": "1"}
    )
    await sign_in_staff(staff, app, email, *roles)
    return staff


async def _propose(staff: httpx.AsyncClient, reference: str) -> httpx.Response:
    return await staff.post(
        f"/api/v1/admin/transactions/{reference}/reversals",
        headers={"Idempotency-Key": key()},
        json={"reason": "khách chuyển nhầm, có xác nhận"},
    )


async def test_reversal_needs_a_second_person_and_restores_balances(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    _, minh = await two_customers(client, other_client, app)
    sent = await send(client, minh["wallet_number"], 500_000)
    maker = await _staff(app, "ops@example.com", "OPERATIONS")
    checker = await _staff(app, "fin@example.com", "FINANCE")
    try:
        proposed = await _propose(maker, sent["reference"])
        assert proposed.status_code == 201, proposed.text
        approval = proposed.json()["approval"]
        assert approval["action_type"] == "REVERSAL"
        assert (await _propose(maker, sent["reference"])).status_code == 409  # one at a time
        # Hidden from customers until it completes.
        types = [t["type"] for t in (await client.get("/api/v1/transactions")).json()["items"]]
        assert "REVERSAL" not in types
        # Operations may propose but approve nothing: approval requests are refused (403).
        assert (
            await maker.post(f"/api/v1/admin/approvals/{approval['id']}/approve", json={})
        ).status_code == 403
        done = await checker.post(f"/api/v1/admin/approvals/{approval['id']}/approve", json={})
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "EXECUTED"
        assert await balance(client) == 3_000_000
        assert await balance(other_client) == 0
        original = (await client.get(f"/api/v1/transactions/{sent['reference']}")).json()
        assert original["status"] == "REVERSED"
        mine = (await client.get("/api/v1/transactions", params={"type": "REVERSAL"})).json()
        assert mine["items"][0]["direction"] == "IN"
        assert mine["items"][0]["status"] == "COMPLETED"
        # FT-23: a reversed transaction cannot be reversed again.
        assert (await _propose(maker, sent["reference"])).json()["code"] == "TXN_INVALID_STATE"
    finally:
        await maker.aclose()
        await checker.aclose()
    await drain(app)
    titles = [n["title"] for n in (await client.get("/api/v1/notifications")).json()["items"]]
    assert "Giao dịch được đảo, tiền đã hoàn về ví" in titles
    await assert_books_clean(app)


async def test_ft22_reversal_refused_when_the_receiver_spent_the_money(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    lan, minh = await two_customers(client, other_client, app)
    sent = await send(client, minh["wallet_number"], 500_000)
    await send(other_client, lan["wallet_number"], 400_000)  # Minh spent most of it
    maker = await _staff(app, "ops@example.com", "OPERATIONS")
    checker = await _staff(app, "fin@example.com", "FINANCE")
    try:
        approval = (await _propose(maker, sent["reference"])).json()["approval"]
        r = await checker.post(f"/api/v1/admin/approvals/{approval['id']}/approve", json={})
        assert r.json()["status"] == "FAILED"
        assert r.json()["failure_code"] == "INSUFFICIENT_BALANCE"
        await drain(app)
        cases = (await checker.get("/api/v1/admin/recovery-cases")).json()["items"]
        assert [c["reason"] for c in cases] == ["REVERSAL_INSUFFICIENT_FUNDS"]
    finally:
        await maker.aclose()
        await checker.aclose()
    assert await balance(other_client) == 100_000  # never negative
    assert (await client.get(f"/api/v1/transactions/{sent['reference']}")).json()[
        "status"
    ] == "COMPLETED"
    assert await journal_count(app, sent["reference"]) == 1
    await assert_books_clean(app)


async def test_rejected_reversal_and_refunded_payments(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await shop(client, other_client, app)
    request = await create_request(other_client, kind="MERCHANT", amount="100000")
    paid = await pay_and_confirm(client, request["code"])
    maker = await _staff(app, "ops@example.com", "OPERATIONS")
    checker = await _staff(app, "fin@example.com", "FINANCE")
    try:
        approval = (await _propose(maker, paid["reference"])).json()["approval"]
        r = await checker.post(
            f"/api/v1/admin/approvals/{approval['id']}/reject", json={"note": "không đủ căn cứ"}
        )
        assert r.json()["status"] == "REJECTED"
        assert await merchant_balance(other_client) == 100_000
        await other_client.post(
            f"/api/v1/merchant/payments/{paid['reference']}/refunds",
            headers={"Idempotency-Key": key()},
            json={"amount": "10000", "reason": "giảm giá"},
        )
        # Refunded in part: a reversal is no longer allowed (§5.5).
        assert (await _propose(maker, paid["reference"])).json()["code"] == "TXN_INVALID_STATE"
    finally:
        await maker.aclose()
        await checker.aclose()
    await assert_books_clean(app)


async def test_merchant_endpoints_need_a_merchant(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await two_customers(client, other_client, app)
    assert (await client.get("/api/v1/merchant")).json()["code"] == "MERCHANT_NOT_FOUND"
    assert (await client.get("/api/v1/merchant/payments")).status_code == 404
    merchant = await make_merchant(app, email=OTHER)
    assert (await make_merchant(app, email=OTHER))["id"] == merchant["id"]  # idempotent
    profile = (await other_client.get("/api/v1/merchant")).json()
    assert profile["display_name"] == "Cà phê Mây"
    assert profile["balance"]["amount"] == "0"
