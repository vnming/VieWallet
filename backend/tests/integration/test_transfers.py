"""Transfers between customers (FT-01..FT-12, FT-31), recipients, review holds."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.clock import FixedClock
from app.core.db import run_in_transaction
from app.modules.risk.public import Decision
from app.modules.transactions.public import Transaction, review, transfer
from app.modules.transactions.use_cases import spend
from app.modules.wallet.public import get_wallet
from tests.integration.finance_helpers import sign_in_staff
from tests.integration.identity_helpers import PIN
from tests.integration.money_helpers import (
    assert_books_clean,
    balance,
    customer,
    drain,
    finance,
    fund_wallet,
    key,
    make_tier1,
)
from tests.integration.spend_helpers import (
    OTHER,
    confirm,
    journal_count,
    make_merchant,
    quote,
    quoted,
    risk_decision,
    send,
    two_customers,
    vnd,
)

# ---------------------------------------------------------------- happy path and views


async def test_transfer_moves_money_and_both_sides_see_it(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    lan, minh = await two_customers(client, other_client, app, funds=1_000_000)
    q = await quoted(client, minh["wallet_number"], 250_000, note="Tiền cơm\x07")
    assert q["status"] == "CREATED"
    assert q["next_action"] == "CONFIRM"
    assert q["total"] == {"amount": "250000", "currency": "VND"}
    assert q["counterparty"]["name"] == "TRAN T*** L"  # masked, never the full name
    assert q["counterparty"]["wallet_number_masked"].endswith(minh["wallet_number"][-4:])
    assert q["description"] == "Tiền cơm"  # control characters removed
    assert q["warnings"] == ["NEW_RECIPIENT"]

    done = await confirm(client, q["reference"])
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "COMPLETED"
    assert [e["to_status"] for e in done.json()["events"]] == ["CREATED", "COMPLETED"]
    assert await balance(client) == 750_000
    assert await balance(other_client) == 250_000
    assert await journal_count(app, q["reference"]) == 1

    # The receiver sees the sender (not themselves) as the counterparty.
    seen = (await other_client.get(f"/api/v1/transactions/{q['reference']}")).json()
    assert seen["direction"] == "IN"
    assert seen["counterparty"]["wallet_number_masked"].endswith(lan["wallet_number"][-4:])
    await drain(app)
    inbox = (await other_client.get("/api/v1/notifications")).json()["items"]
    assert inbox[0]["title"] == "Bạn nhận được tiền"
    sender_inbox = (await client.get("/api/v1/notifications")).json()["items"]
    assert sender_inbox[0]["title"] == "Chuyển tiền thành công"
    await assert_books_clean(app)


async def test_quote_validation_and_lookup_errors(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    lan, minh = await two_customers(client, other_client, app)
    both = await client.post(
        "/api/v1/transfers",
        headers={"Idempotency-Key": key()},
        json={"wallet_number": minh["wallet_number"], "email": OTHER, "amount": "1000"},
    )
    assert both.status_code == 422
    self_transfer = await quote(client, to=lan["wallet_number"], amount=1_000)
    assert self_transfer.json()["code"] == "RECIPIENT_IS_SELF"
    unknown = await quote(client, to="1234567890", amount=1_000)
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "RECIPIENT_NOT_FOUND"
    assert (await quote(client, to=minh["wallet_number"], amount=0)).status_code == 422
    over = await quote(client, to=minh["wallet_number"], amount=2_000_001)
    assert over.json()["code"] == "LIMIT_PER_TXN_EXCEEDED"
    by_email = await quote(client, email=OTHER.upper(), amount=1_000)
    assert by_email.status_code == 201, by_email.text
    no_key = await client.post(
        "/api/v1/transfers", json={"wallet_number": minh["wallet_number"], "amount": "1000"}
    )
    assert no_key.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"


# ---------------------------------------------------------------- FT-01 .. FT-04


async def test_ft01_insufficient_balance_fails_without_journal(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    _, minh = await two_customers(client, other_client, app, funds=1_000_000)
    q = await quoted(client, minh["wallet_number"], 1_500_000)
    r = await confirm(client, q["reference"])
    assert r.status_code == 200
    assert r.json()["status"] == "FAILED"
    assert r.json()["failure_code"] == "INSUFFICIENT_BALANCE"
    assert await journal_count(app, q["reference"]) == 0
    assert await balance(client) == 1_000_000
    await assert_books_clean(app)


async def test_ft02_ft03_replays_return_the_same_body(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    _, minh = await two_customers(client, other_client, app)
    quote_key = key()
    first_quote = await quote(client, to=minh["wallet_number"], amount=100_000, idem=quote_key)
    again = await quote(client, to=minh["wallet_number"], amount=100_000, idem=quote_key)
    assert again.json() == first_quote.json()
    assert again.headers["Idempotent-Replayed"] == "true"
    reused = await quote(client, to=minh["wallet_number"], amount=200_000, idem=quote_key)
    assert reused.status_code == 422
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"  # FT-03

    reference = first_quote.json()["reference"]
    confirm_key = key()
    first = await confirm(client, reference, idem=confirm_key)
    replays = [await confirm(client, reference, idem=confirm_key) for _ in range(4)]
    for replay in replays:
        assert replay.json() == first.json()
        assert replay.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert await journal_count(app, reference) == 1
    assert await balance(client) == 2_900_000
    other = await confirm(client, reference)  # a different key after completion
    assert other.status_code == 409
    assert other.json()["code"] == "TXN_INVALID_STATE"


async def test_ft04_same_key_ten_times_at_once(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    _, minh = await two_customers(client, other_client, app)
    q = await quoted(client, minh["wallet_number"], 100_000)
    same = key()
    responses = await asyncio.gather(
        *(confirm(client, q["reference"], idem=same) for _ in range(10))
    )
    codes = sorted(r.status_code for r in responses)
    assert set(codes) <= {200, 409}
    assert 200 in codes
    for r in responses:
        if r.status_code == 409:
            assert r.json()["code"] == "IDEMPOTENCY_IN_PROGRESS"
        else:
            assert r.json()["status"] == "COMPLETED"
    assert await journal_count(app, q["reference"]) == 1
    assert await balance(client) == 2_900_000


# ---------------------------------------------------------------- concurrency (use-case level)


async def _user_id(client: httpx.AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/me")).json()["id"])


async def _quote_uc(app: FastAPI, user_id: uuid.UUID, to: str, amount: int) -> Transaction:
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        return await transfer.quote_transfer(
            session,
            ctx,
            user_id=user_id,
            target=transfer.RecipientTarget(wallet_number=to),
            amount=vnd(amount),
            note="",
            key=key(),
        )


async def _confirm_uc(app: FastAPI, user_id: uuid.UUID, txn_id: uuid.UUID) -> Transaction:
    ctx = finance(app)
    confirm_key = key()
    return await run_in_transaction(
        ctx.db,
        lambda s: spend.confirm_locked(
            s, ctx, user_id=user_id, transaction_id=txn_id, key=confirm_key
        ),
    )


async def test_ft05_fifty_concurrent_transfers_twenty_affordable(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, minh = await two_customers(client, other_client, app, funds=2_000_000)
    lan_id = await _user_id(client)
    quotes = [await _quote_uc(app, lan_id, minh["wallet_number"], 100_000) for _ in range(50)]
    with risk_decision(monkeypatch, None):  # locking under test, not the risk rules
        results = await asyncio.gather(*(_confirm_uc(app, lan_id, q.id) for q in quotes))
    statuses = [r.status for r in results]
    assert statuses.count("COMPLETED") == 20
    assert statuses.count("FAILED") == 30
    assert {r.failure_code for r in results if r.status == "FAILED"} == {"INSUFFICIENT_BALANCE"}
    assert await balance(client) == 0
    assert await balance(other_client) == 2_000_000
    await assert_books_clean(app)


async def test_ft06_opposite_transfers_do_not_deadlock(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lan, minh = await two_customers(client, other_client, app, funds=2_000_000)
    await fund_wallet(app, minh["id"], 2_000_000)
    lan_id, minh_id = await _user_id(client), await _user_id(other_client)
    pairs = 100
    a_to_b = [await _quote_uc(app, lan_id, minh["wallet_number"], 10_000) for _ in range(pairs)]
    b_to_a = [await _quote_uc(app, minh_id, lan["wallet_number"], 10_000) for _ in range(pairs)]
    with risk_decision(monkeypatch, None):
        results = await asyncio.gather(
            *(_confirm_uc(app, lan_id, q.id) for q in a_to_b),
            *(_confirm_uc(app, minh_id, q.id) for q in b_to_a),
        )
    assert {r.status for r in results} == {"COMPLETED"}
    assert await balance(client) + await balance(other_client) == 4_000_000
    assert await balance(client) == 2_000_000
    await assert_books_clean(app)


async def test_ft07_two_confirms_of_one_transfer(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    _, minh = await two_customers(client, other_client, app)
    q = await quoted(client, minh["wallet_number"], 100_000)
    first, second = await asyncio.gather(
        confirm(client, q["reference"]), confirm(client, q["reference"])
    )
    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 409]
    loser = first if first.status_code == 409 else second
    assert loser.json()["code"] == "TXN_INVALID_STATE"
    assert await journal_count(app, q["reference"]) == 1


async def test_ft12_concurrent_transfers_respect_the_daily_limit(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, minh = await two_customers(client, other_client, app, funds=9_000_000)
    lan_id = await _user_id(client)
    # Tier 0: 2 000 000 per transfer, 5 000 000 per day. Quotes alone count nothing.
    quotes = [await _quote_uc(app, lan_id, minh["wallet_number"], 1_000_000) for _ in range(8)]
    with risk_decision(monkeypatch, None):
        results = await asyncio.gather(*(_confirm_uc(app, lan_id, q.id) for q in quotes))
    completed = [r for r in results if r.status == "COMPLETED"]
    assert sum(r.amount_minor for r in completed) == 5_000_000
    assert {r.failure_code for r in results if r.status == "FAILED"} == {
        "LIMIT_DAILY_AMOUNT_EXCEEDED"
    }


# ---------------------------------------------------------------- FT-08 .. FT-11, FT-31


async def test_ft08_confirm_after_the_quote_expired(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    _, minh = await two_customers(client, other_client, app)
    q = await quoted(client, minh["wallet_number"], 100_000)
    late = await quoted(client, minh["wallet_number"], 100_000)
    clock.advance(timedelta(minutes=11))
    r = await confirm(client, q["reference"])
    assert r.json()["status"] == "EXPIRED"
    assert r.json()["failure_code"] == "QUOTE_EXPIRED"
    assert await journal_count(app, q["reference"]) == 0
    assert await transfer.expire_quotes(finance(app)) == 1  # the worker job takes the other
    assert (await client.get(f"/api/v1/transfers/{late['reference']}")).json()[
        "status"
    ] == "EXPIRED"
    assert await balance(client) == 3_000_000


async def test_ft09_sender_wallet_frozen_after_the_quote(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    _, minh = await two_customers(client, other_client, app)
    q = await quoted(client, minh["wallet_number"], 100_000)
    assert (await client.post("/api/v1/wallet/freeze", json={})).status_code == 200
    r = await confirm(client, q["reference"])
    assert r.json()["status"] == "FAILED"
    assert r.json()["failure_code"] == "WALLET_FROZEN"
    assert await journal_count(app, q["reference"]) == 0
    blocked = await quote(client, to=minh["wallet_number"], amount=1_000)
    assert blocked.json()["code"] == "WALLET_FROZEN"


async def test_ft10_receiver_wallet_frozen(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    _, minh = await two_customers(client, other_client, app)
    q = await quoted(client, minh["wallet_number"], 100_000)
    assert (await other_client.post("/api/v1/wallet/freeze", json={})).status_code == 200
    r = await confirm(client, q["reference"])
    assert r.json()["status"] == "FAILED"
    assert r.json()["failure_code"] == "RECIPIENT_UNAVAILABLE"
    at_quote = await quote(client, to=minh["wallet_number"], amount=1_000)
    assert at_quote.status_code == 422
    assert at_quote.json()["code"] == "RECIPIENT_UNAVAILABLE"
    assert await balance(client) == 3_000_000


async def test_ft11_pin_lockout_keeps_the_quote(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    _, minh = await two_customers(client, other_client, app)
    q = await quoted(client, minh["wallet_number"], 100_000)
    codes = [(await confirm(client, q["reference"], pin="000000")).json()["code"] for _ in range(5)]
    assert codes == ["PIN_INVALID"] * 4 + ["PIN_LOCKED"]
    right = await confirm(client, q["reference"], pin=PIN)
    assert right.json()["code"] == "PIN_LOCKED"
    assert (await client.get(f"/api/v1/transfers/{q['reference']}")).json()["status"] == "CREATED"
    assert await journal_count(app, q["reference"]) == 0


async def test_ft31_receiver_balance_cap(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    _, minh = await two_customers(client, other_client, app)
    await fund_wallet(app, minh["id"], 9_500_000)  # tier 0 cap: 10 000 000
    q = await quoted(client, minh["wallet_number"], 1_000_000)
    r = await confirm(client, q["reference"])
    assert r.json()["status"] == "FAILED"
    assert r.json()["failure_code"] == "RECEIVER_LIMIT_EXCEEDED"
    assert await balance(other_client) == 9_500_000
    ok = await send(client, minh["wallet_number"], 500_000)  # exactly to the cap
    assert ok["status"] == "COMPLETED"


async def test_cancel_only_while_created(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    _, minh = await two_customers(client, other_client, app)
    q = await quoted(client, minh["wallet_number"], 100_000)
    cancelled = await client.post(f"/api/v1/transfers/{q['reference']}/cancel")
    assert cancelled.json()["status"] == "CANCELLED"
    assert (await client.post(f"/api/v1/transfers/{q['reference']}/cancel")).status_code == 409
    assert (await confirm(client, q["reference"])).status_code == 409
    # Someone else's transfer does not exist for them.
    assert (await other_client.get(f"/api/v1/transfers/{q['reference']}")).status_code == 404


# ---------------------------------------------------------------- recipients


async def test_lookup_saved_recipients_and_warnings(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    _, minh = await two_customers(client, other_client, app)
    found = await client.get(
        "/api/v1/recipients/lookup", params={"wallet_number": minh["wallet_number"]}
    )
    assert found.status_code == 200, found.text
    assert found.json()["display_name"] == "TRAN T*** L"
    assert found.json()["kind"] == "USER"
    by_email = await client.get("/api/v1/recipients/lookup", params={"email": OTHER})
    assert by_email.json()["wallet_number"] is None  # not echoed for e-mail lookups
    assert (await client.get("/api/v1/recipients/lookup")).status_code == 422

    saved = await client.post(
        "/api/v1/recipients", json={"wallet_number": minh["wallet_number"], "nickname": "Minh"}
    )
    assert saved.status_code == 201, saved.text
    rid = saved.json()["id"]
    again = await client.post("/api/v1/recipients", json={"wallet_number": minh["wallet_number"]})
    assert again.json()["id"] == rid  # saving twice keeps one entry
    renamed = await client.patch(f"/api/v1/recipients/{rid}", json={"nickname": "Anh Minh"})
    assert renamed.json()["nickname"] == "Anh Minh"
    assert (await other_client.delete(f"/api/v1/recipients/{rid}")).status_code == 404

    first = await quote(client, recipient_id=rid, amount=50_000)
    assert first.json()["warnings"] == ["NEW_RECIPIENT"]
    assert (await confirm(client, first.json()["reference"])).json()["status"] == "COMPLETED"
    second = await quote(client, recipient_id=rid, amount=50_000)
    assert second.json()["warnings"] == ["POSSIBLE_DUPLICATE"]
    listed = (await client.get("/api/v1/recipients")).json()
    assert listed[0]["last_used_at"] is not None
    assert (await other_client.post("/api/v1/transfers", headers={"Idempotency-Key": key()},
            json={"recipient_id": rid, "amount": "1000"})).status_code == 404  # fmt: skip
    assert (await client.delete(f"/api/v1/recipients/{rid}")).status_code == 204
    assert (await client.get("/api/v1/recipients")).json() == []


async def test_lookup_is_rate_limited(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await two_customers(client, other_client, app, funds=0)
    statuses = [
        (
            await client.get("/api/v1/recipients/lookup", params={"wallet_number": "1234567890"})
        ).status_code
        for _ in range(21)
    ]
    assert statuses[:20] == [404] * 20
    assert statuses[20] == 429


async def test_merchant_wallets_are_not_transfer_targets(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await two_customers(client, other_client, app)
    merchant = await make_merchant(app)
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        wallet = await get_wallet(session, uuid.UUID(merchant["wallet_id"]))
    r = await quote(client, to=wallet.wallet_number, amount=1_000)
    assert r.json()["code"] == "RECIPIENT_NOT_FOUND"


async def test_personal_receive_qr(client: httpx.AsyncClient, app: FastAPI) -> None:
    wallet = await customer(client, app)
    qr = (await client.get("/api/v1/wallet/qr")).json()
    assert qr["payload"] == f"http://127.0.0.1:8080/transfer?to={wallet['wallet_number']}"
    assert qr["display_name"] == "TRAN T*** L"


# ---------------------------------------------------------------- review holds


async def _analyst(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    staff = httpx.AsyncClient(
        transport=transport, base_url="http://viewallet.test", headers={"X-VW-CSRF": "1"}
    )
    await sign_in_staff(staff, app, "risk@example.com", "RISK_ANALYST")
    return staff


async def test_risk_review_holds_then_analyst_decides(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    _, minh = await two_customers(client, other_client, app, funds=10_000_000)
    await make_tier1(app)
    # PIN set just now (credential change, 50) + same amount to the same wallet (40) = REVIEW.
    first = await send(client, minh["wallet_number"], 3_000_000)
    assert first["status"] == "COMPLETED"
    held: dict[str, Any] = await send(client, minh["wallet_number"], 3_000_000)
    assert held["status"] == "PENDING"
    assert held["next_action"] == "WAIT_REVIEW"
    assert await balance(client) == 4_000_000  # both debited
    assert await balance(other_client) == 3_000_000  # only the first arrived
    await assert_books_clean(app)  # I5: HOLD equals the held transaction

    staff = await _analyst(app)
    try:
        assert (await client.get("/api/v1/admin/risk/holds")).status_code == 401
        holds = (await staff.get("/api/v1/admin/risk/holds")).json()
        assert [h["reference"] for h in holds] == [held["reference"]]
        approved = await staff.post(
            f"/api/v1/admin/risk/holds/{held['reference']}/approve",
            json={"note": "đã gọi xác minh"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "COMPLETED"
        assert await balance(other_client) == 6_000_000
        again = await staff.post(f"/api/v1/admin/risk/holds/{held['reference']}/approve", json={})
        assert again.status_code == 409

        third = await send(client, minh["wallet_number"], 3_000_000)
        assert third["status"] == "PENDING"
        short = await staff.post(
            f"/api/v1/admin/risk/holds/{third['reference']}/reject", json={"note": "x"}
        )
        assert short.status_code == 422
        rejected = await staff.post(
            f"/api/v1/admin/risk/holds/{third['reference']}/reject",
            json={"note": "nghi gian lận"},
        )
        assert rejected.json()["status"] == "CANCELLED"
        assert await balance(client) == 4_000_000  # 10M - 3M - 3M; the third came back
        view = (await client.get(f"/api/v1/transfers/{third['reference']}")).json()
        assert view["failure_code"] == "REVIEW_REJECTED"
    finally:
        await staff.aclose()
    await drain(app)
    titles = [n["title"] for n in (await client.get("/api/v1/notifications")).json()["items"]]
    assert "Chuyển tiền bị hủy sau kiểm tra" in titles
    assert "Chuyển tiền đang được kiểm tra" in titles
    await assert_books_clean(app)


async def test_review_holds_expire_after_the_sla(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    app: FastAPI,
    clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, minh = await two_customers(client, other_client, app, funds=1_000_000)
    with risk_decision(monkeypatch, Decision.REVIEW):
        held = await send(client, minh["wallet_number"], 400_000)
    assert held["status"] == "PENDING"
    assert await review.expire_holds(finance(app)) == 0
    clock.advance(timedelta(hours=72, minutes=1))
    assert await review.expire_holds(finance(app)) == 1
    view = (await client.get(f"/api/v1/transfers/{held['reference']}")).json()
    assert view["status"] == "CANCELLED"
    assert view["failure_code"] == "REVIEW_EXPIRED"
    assert await balance(client) == 1_000_000
    await assert_books_clean(app)


async def test_risk_block_fails_the_transfer(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, minh = await two_customers(client, other_client, app)
    with risk_decision(monkeypatch, Decision.BLOCK):
        r = await send(client, minh["wallet_number"], 100_000)
    assert r["status"] == "FAILED"
    assert r["failure_code"] == "RISK_BLOCKED"
    assert await balance(client) == 3_000_000
