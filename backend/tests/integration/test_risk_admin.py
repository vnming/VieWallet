"""Risk rules as versioned config behind maker-checker, account flags, asynchronous alerts
(fan-in, round trip, repeated PIN locks), the full rule catalogue on real transfers and the
remaining-limits API."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.clock import FixedClock
from app.core.db import run_in_transaction
from app.modules.identity.public import PinInvalidError, PinLockedError, RequestMeta, verify_pin
from app.modules.risk.public import RiskAssessment, seed_rules
from app.modules.transactions.public import Transaction, transfer
from app.modules.transactions.use_cases import spend
from app.modules.wallet.public import Wallet, ensure_wallet
from tests.integration.finance_helpers import fund, make_user, sign_in_staff
from tests.integration.money_helpers import (
    assert_books_clean,
    customer,
    drain,
    finance,
    key,
    make_tier1,
)
from tests.integration.spend_helpers import send, two_customers, vnd


async def _staff(app: FastAPI, email: str, *roles: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    http = httpx.AsyncClient(
        transport=transport, base_url="http://viewallet.test", headers={"X-VW-CSRF": "1"}
    )
    await sign_in_staff(http, app, email, *roles)
    return http


@pytest.fixture
async def analyst(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    http = await _staff(app, "risk1@example.com", "RISK_ANALYST")
    yield http
    await http.aclose()


async def _me(client: httpx.AsyncClient) -> str:
    return str((await client.get("/api/v1/me")).json()["id"])


async def _assessments(app: FastAPI, user_id: str) -> list[RiskAssessment]:
    async with finance(app).db.transaction() as session:
        rows = await session.scalars(
            select(RiskAssessment)
            .where(RiskAssessment.user_id == uuid.UUID(user_id))
            .order_by(RiskAssessment.id)
        )
        return list(rows.all())


# ---------------------------------------------------------------- rule versions


async def test_rule_change_needs_a_second_analyst(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    analyst: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        await seed_rules(session, ctx.clock)
    rules = (await analyst.get("/api/v1/admin/risk/rules")).json()
    assert len(rules) == 11
    assert {r["status"] for r in rules} == {"ACTIVE"}

    bad = await analyst.post(
        "/api/v1/admin/risk/rules/VELOCITY_COUNT/versions",
        json={"score": 70, "params": {"max_count": 1, "x": 2}, "reason": "siết chặt thử nghiệm"},
    )
    assert bad.json()["code"] == "RISK_RULE_INVALID"
    proposed = await analyst.post(
        "/api/v1/admin/risk/rules/VELOCITY_COUNT/versions",
        json={"score": 70, "params": {"max_count": 1}, "reason": "siết chặt thử nghiệm"},
    )
    assert proposed.status_code == 201, proposed.text
    assert proposed.json()["rule"]["version"] == 2
    assert proposed.json()["rule"]["status"] == "PENDING"
    again = await analyst.post(
        "/api/v1/admin/risk/rules/VELOCITY_COUNT/versions",
        json={"score": 60, "params": {"max_count": 2}, "reason": "đề xuất thứ hai"},
    )
    assert again.json()["code"] == "APPROVAL_DUPLICATE"
    approval = proposed.json()["approval"]
    own = await analyst.post(f"/api/v1/admin/approvals/{approval['id']}/approve", json={})
    assert own.json()["code"] == "APPROVAL_SELF_DECISION"

    checker = await _staff(app, "risk2@example.com", "RISK_ANALYST")
    try:
        done = await checker.post(f"/api/v1/admin/approvals/{approval['id']}/approve", json={})
        assert done.json()["status"] == "EXECUTED", done.text
    finally:
        await checker.aclose()
    versions = [
        (r["version"], r["status"])
        for r in (await analyst.get("/api/v1/admin/risk/rules")).json()
        if r["code"] == "VELOCITY_COUNT"
    ]
    assert versions == [(2, "ACTIVE"), (1, "RETIRED")]

    # The new version is what transfers are judged by: the second transfer goes to review.
    _, minh = await two_customers(client, other_client, app)
    assert (await send(client, minh["wallet_number"], 10_000))["status"] == "COMPLETED"
    held = await send(client, minh["wallet_number"], 20_000)
    assert held["status"] == "PENDING"
    latest = (await _assessments(app, await _me(client)))[-1]
    assert [r["code"] for r in latest.matched_rules] == ["VELOCITY_COUNT"]
    assert latest.rules_version.startswith("rs-")
    # Release the hold so the books stay clean for the next tests (HOLD is shared).
    released = await analyst.post(
        f"/api/v1/admin/risk/holds/{held['reference']}/reject", json={"note": "kết thúc thử"}
    )
    assert released.json()["status"] == "CANCELLED"
    await assert_books_clean(app)


# ---------------------------------------------------------------- flags


async def test_watchlist_flag_feeds_the_rules(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    analyst: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    _, minh = await two_customers(client, other_client, app)
    lan = await _me(client)
    path = f"/api/v1/admin/users/{lan}/risk-flags"
    assert (await client.post(path, json={"flag": "WATCHLIST", "reason": "x"})).status_code == 401
    unknown = await analyst.post(path, json={"flag": "VIP", "reason": "không có cờ này"})
    assert unknown.status_code == 422
    added = await analyst.post(path, json={"flag": "WATCHLIST", "reason": "theo dõi thử"})
    assert added.status_code == 201, added.text
    assert (await analyst.post(path, json={"flag": "WATCHLIST", "reason": "lần hai"})).json()[
        "code"
    ] == "RISK_FLAG_EXISTS"
    assert (await send(client, minh["wallet_number"], 10_000))["status"] == "COMPLETED"  # 50 < 70
    hits = (await _assessments(app, lan))[-1].matched_rules
    assert hits == [
        {"code": "ACCOUNT_FLAG_WATCHLIST", "score": 50, "block": False,
         "evidence": {"flag": "WATCHLIST"}},
    ]  # fmt: skip
    removed = await analyst.request(
        "DELETE", f"{path}/WATCHLIST", json={"reason": "đã xác minh xong"}
    )
    assert removed.status_code == 204
    again = await analyst.request("DELETE", f"{path}/WATCHLIST", json={"reason": "lần hai"})
    assert again.json()["code"] == "RISK_FLAG_NOT_FOUND"
    flags = (await analyst.get(path)).json()
    assert flags[0]["removed_at"] is not None


# ---------------------------------------------------------------- the catalogue on real money


async def test_new_account_large_transfer_goes_to_review(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    analyst: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    _, minh = await two_customers(client, other_client, app, funds=8_000_000)
    await make_tier1(app)
    held = await send(client, minh["wallet_number"], 6_000_000)
    assert held["status"] == "PENDING"
    hits = {r["code"] for r in (await _assessments(app, await _me(client)))[-1].matched_rules}
    assert hits == {"NEW_ACCOUNT_LARGE", "CREDENTIAL_CHANGE_COOLDOWN", "NEW_RECIPIENT_LARGE"}
    await assert_books_clean(app)
    approved = await analyst.post(f"/api/v1/admin/risk/holds/{held['reference']}/approve", json={})
    assert approved.json()["status"] == "COMPLETED"


# ---------------------------------------------------------------- asynchronous alerts


async def _alerts(analyst: httpx.AsyncClient, status: str = "OPEN") -> list[dict[str, Any]]:
    r = await analyst.get("/api/v1/admin/risk/alerts", params={"status": status})
    assert r.status_code == 200, r.text
    items: list[dict[str, Any]] = r.json()["items"]
    return items


async def _pay(app: FastAPI, sender: Wallet, to_number: str, amount: int) -> Transaction:
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        quote = await transfer.quote_transfer(
            session,
            ctx,
            user_id=sender.owner_user_id,
            target=transfer.RecipientTarget(wallet_number=to_number),
            amount=vnd(amount),
            note="",
            key=key(),
        )
    confirm_key = key()
    return await run_in_transaction(
        ctx.db,
        lambda s: spend.confirm_locked(
            s, ctx, user_id=sender.owner_user_id, transaction_id=quote.id, key=confirm_key
        ),
    )


async def test_fan_in_and_round_trip_alerts(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    analyst: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    lan, minh = await two_customers(client, other_client, app)
    ctx = finance(app)
    senders = []
    for index in range(5):
        async with ctx.db.transaction() as session:
            user = await make_user(session, ctx.clock, f"Người Gửi {index}")
            wallet = await ensure_wallet(session, ctx.clock, user)
        await fund(ctx.db, ctx.clock, wallet, 100_000)
        senders.append(wallet)
    for wallet in senders[:4]:
        assert (await _pay(app, wallet, minh["wallet_number"], 10_000)).status == "COMPLETED"
    await drain(app)
    assert await _alerts(analyst) == []  # four senders: not yet
    await _pay(app, senders[4], minh["wallet_number"], 10_000)
    await drain(app)
    [fan_in] = await _alerts(analyst)
    assert fan_in["type"] == "FAN_IN"
    assert fan_in["detail"]["distinct_senders_1h"] == "5"
    assert fan_in["user_id"] == await _me(other_client)

    await send(client, minh["wallet_number"], 77_000)
    await send(other_client, lan["wallet_number"], 77_000)
    await drain(app)
    await drain(app)  # replayed events create nothing new
    types = sorted(a["type"] for a in await _alerts(analyst))
    assert types == ["FAN_IN", "ROUND_TRIP"]  # the sixth sender is the same hourly pattern

    resolved = await analyst.post(
        f"/api/v1/admin/risk/alerts/{fan_in['id']}/resolve",
        json={"outcome": "FALSE_POSITIVE", "note": "nhóm bạn góp tiền ăn"},
    )
    assert resolved.json()["status"] == "FALSE_POSITIVE"
    closed = await analyst.post(
        f"/api/v1/admin/risk/alerts/{fan_in['id']}/resolve",
        json={"outcome": "RESOLVED", "note": "lần hai"},
    )
    assert closed.json()["code"] == "RISK_ALERT_CLOSED"


async def test_repeated_pin_locks_raise_an_alert(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    await customer(client, app)
    user_id = uuid.UUID(await _me(client))
    identity = finance(app).identity
    for _ in range(2):
        for _ in range(5):
            with pytest.raises((PinInvalidError, PinLockedError)):
                await verify_pin(identity, user_id=user_id, pin="135790", meta=RequestMeta())
        clock.advance(timedelta(minutes=31))
    await drain(app)
    analyst = await _staff(app, "risk3@example.com", "RISK_ANALYST")  # after the clock moved
    try:
        [alert] = await _alerts(analyst)
    finally:
        await analyst.aclose()
    assert (alert["type"], alert["detail"]["locks_in_window"]) == ("REPEATED_PIN_LOCK", "2")


# ---------------------------------------------------------------- remaining limits


async def test_limits_show_what_is_left(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    _, minh = await two_customers(client, other_client, app)
    await send(client, minh["wallet_number"], 1_000_000)
    limits = (await client.get("/api/v1/wallet/limits")).json()
    assert (limits["kyc_tier"], limits["balance_cap"], limits["balance"]) == (
        0,
        "10000000",
        "2000000",
    )
    by_type = {t["type"]: t for t in limits["types"]}
    transfer_limits = {limit["period"]: limit for limit in by_type["TRANSFER"]["limits"]}
    assert transfer_limits["PER_TXN"]["max_amount"] == "2000000"
    assert (
        transfer_limits["DAILY"]["used_amount"],
        transfer_limits["DAILY"]["remaining_amount"],
    ) == (
        "1000000",
        "4000000",
    )
    assert transfer_limits["MONTHLY"]["remaining_amount"] == "19000000"
    assert by_type["WITHDRAWAL"]["allowed"] is False
    assert by_type["DEPOSIT"]["allowed"] is True
    minh_transfer = (await other_client.get("/api/v1/wallet/limits")).json()["types"][0]
    assert {limit["used_amount"] for limit in minh_transfer["limits"]} == {"0"}
