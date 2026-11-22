"""Wallet API, maker-checker adjustments (FT-30), unfreeze approvals and reconciliation."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.core.clock import FixedClock
from app.core.db import Database, sqlstate_of
from app.core.jobs import JobScheduler
from app.modules.operations.models import ApprovalRequest
from app.modules.operations.public import run_internal
from app.modules.wallet.public import Wallet, ensure_wallet
from app.worker import build_jobs
from tests.integration.finance_helpers import browser, fund, make_user, sign_in_staff
from tests.integration.identity_helpers import PIN, signed_in_customer, totp_now

MAKER = "maker@example.com"
CHECKER = "checker@example.com"


async def customer_with_pin(client: httpx.AsyncClient, app: FastAPI) -> dict[str, Any]:
    await signed_in_customer(client, app)
    r = await client.post("/api/v1/me/pin", json={"password": "mây-trắng-bay-2026", "pin": PIN})
    assert r.status_code == 204, r.text
    wallet = await client.get("/api/v1/wallet")
    assert wallet.status_code == 200, wallet.text
    body: dict[str, Any] = wallet.json()
    return body


async def propose(
    maker: httpx.AsyncClient,
    wallet_number: str,
    amount: str,
    direction: str = "CREDIT",
    key: str | None = None,
) -> httpx.Response:
    return await maker.post(
        "/api/v1/admin/adjustments",
        headers={"Idempotency-Key": key or f"adj-{uuid.uuid4().hex}"},
        json={
            "wallet_number": wallet_number,
            "direction": direction,
            "amount": amount,
            "reason_code": "DEMO_FUNDING",
            "note": "Nạp tiền mô phỏng cho demo",
        },
    )


async def test_wallet_is_opened_once(
    client: httpx.AsyncClient, app: FastAPI, db: Database, clock: FixedClock
) -> None:
    await signed_in_customer(client, app)
    first, second = await asyncio.gather(client.get("/api/v1/wallet"), client.get("/api/v1/wallet"))
    assert first.status_code == second.status_code == 200, first.text
    assert first.json()["id"] == second.json()["id"]
    body = first.json()
    assert body["status"] == "ACTIVE"
    assert body["balance"] == {"amount": "0", "currency": "VND"}
    assert len(body["wallet_number"]) == 10
    async with db.transaction() as session:
        count = await session.scalar(text("SELECT count(*) FROM app.wallets"))
        assert count == 1


async def test_concurrent_ensure_wallet_creates_one(db: Database, clock: FixedClock) -> None:
    count_accounts = text("SELECT count(*) FROM app.ledger_accounts WHERE code = 'CUSTOMER_WALLET'")
    async with db.transaction() as session:
        user_id = await make_user(session, clock)
        before = await session.scalar(count_accounts)

    async def ensure() -> Wallet:
        async with db.transaction() as session:
            return await ensure_wallet(session, clock, user_id)

    wallets = await asyncio.gather(*(ensure() for _ in range(5)))
    assert len({w.id for w in wallets}) == 1
    async with db.transaction() as session:
        after = await session.scalar(count_accounts)
    assert after - before == 1  # losers rolled back their savepoint: no orphan accounts


async def test_self_freeze_and_unfreeze(client: httpx.AsyncClient, app: FastAPI) -> None:
    await customer_with_pin(client, app)
    frozen = await client.post("/api/v1/wallet/freeze", json={"reason": "mất điện thoại"})
    assert frozen.json()["status"] == "FROZEN"
    assert frozen.json()["freeze_source"] == "SELF"
    again = await client.post("/api/v1/wallet/freeze", json={})
    assert again.json()["code"] == "WALLET_STATE_CONFLICT"
    wrong = await client.post("/api/v1/wallet/unfreeze", json={"pin": "000001"})
    assert wrong.json()["code"] in {"PIN_INVALID", "PIN_WEAK"}
    ok = await client.post("/api/v1/wallet/unfreeze", json={"pin": PIN})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "ACTIVE"
    assert (await client.post("/api/v1/wallet/unfreeze", json={"pin": PIN})).status_code == 409


async def test_maker_checker_adjustment_flow(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI, db: Database
) -> None:
    wallet = await customer_with_pin(client, app)
    async with browser(app) as maker, browser(app) as checker:
        await sign_in_staff(maker, app, MAKER, "FINANCE")
        await sign_in_staff(checker, app, CHECKER, "FINANCE")

        missing_key = await maker.post("/api/v1/admin/adjustments", json={})
        assert missing_key.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
        bad_amount = await propose(maker, wallet["wallet_number"], "0")
        assert bad_amount.status_code == 422
        unknown = await propose(maker, "1234567890", "1000")
        assert unknown.json()["code"] == "WALLET_NOT_FOUND"

        created = await propose(maker, wallet["wallet_number"], "250000", key="adj-key-0001")
        assert created.status_code == 201, created.text
        replay = await propose(maker, wallet["wallet_number"], "250000", key="adj-key-0001")
        assert replay.headers["Idempotent-Replayed"] == "true"
        assert replay.json() == created.json()
        reused = await propose(maker, wallet["wallet_number"], "1", key="adj-key-0001")
        assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

        approval = created.json()["approval"]
        reference = created.json()["transaction_reference"]
        assert approval["status"] == "PENDING"
        assert approval["can_decide"] is False  # the maker sees it but cannot decide

        # Pending staff proposals are invisible to the customer.
        assert (await client.get("/api/v1/transactions")).json()["items"] == []
        assert (await client.get(f"/api/v1/transactions/{reference}")).status_code == 404

        # FT-30: the maker cannot approve their own request (service check, audited).
        self_ok = await maker.post(f"/api/v1/admin/approvals/{approval['id']}/approve", json={})
        assert self_ok.status_code == 403
        assert self_ok.json()["code"] == "APPROVAL_SELF_DECISION"

        listing = (await checker.get("/api/v1/admin/approvals")).json()
        assert [i["id"] for i in listing["items"]] == [approval["id"]]
        assert listing["items"][0]["can_decide"] is True

        approved = await checker.post(
            f"/api/v1/admin/approvals/{approval['id']}/approve", json={"note": "ok"}
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "EXECUTED"
        twice = await checker.post(f"/api/v1/admin/approvals/{approval['id']}/approve", json={})
        assert twice.json()["code"] == "APPROVAL_NOT_PENDING"

        admin_view = (await checker.get(f"/api/v1/admin/transactions/{reference}")).json()
        assert [e["to_status"] for e in admin_view["events"]] == ["CREATED", "PENDING", "COMPLETED"]
        assert admin_view["metadata"]["internal_note"] == "Nạp tiền mô phỏng cho demo"

    me = (await client.get("/api/v1/wallet")).json()
    assert me["balance"] == {"amount": "250000", "currency": "VND"}
    history = (await client.get("/api/v1/transactions")).json()
    assert len(history["items"]) == 1
    item = history["items"][0]
    assert (item["type"], item["direction"], item["status"]) == ("ADJUSTMENT", "IN", "COMPLETED")
    assert item["total"] == {"amount": "250000", "currency": "VND"}
    assert item["counterparty"] == {
        "kind": "SYSTEM",
        "name": "VieWallet",
        "wallet_number_masked": None,
    }
    detail = (await client.get(f"/api/v1/transactions/{reference}")).json()
    assert "internal_note" not in str(detail)
    assert [e["to_status"] for e in detail["events"]] == ["CREATED", "PENDING", "COMPLETED"]
    # Another customer cannot see it.
    await signed_in_customer(other_client, app, email="binh@example.com")
    assert (await other_client.get(f"/api/v1/transactions/{reference}")).status_code == 404


async def test_db_refuses_self_approval(
    db: Database, migrator_db: Database, app: FastAPI, client: httpx.AsyncClient
) -> None:
    """FT-30, second layer: CHECK (checker_id <> maker_id)."""
    wallet = await customer_with_pin(client, app)
    async with browser(app) as maker:
        await sign_in_staff(maker, app, MAKER, "FINANCE")
        created = (await propose(maker, wallet["wallet_number"], "1000")).json()
    approval_id = uuid.UUID(created["approval"]["id"])
    with pytest.raises(DBAPIError) as excinfo:
        async with db.transaction() as session:
            await session.execute(
                update(ApprovalRequest)
                .where(ApprovalRequest.id == approval_id)
                .values(checker_id=ApprovalRequest.maker_id, status="APPROVED")
            )
    assert sqlstate_of(excinfo.value) == "23514"


async def test_debit_adjustment_without_funds_fails_cleanly(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    wallet = await customer_with_pin(client, app)
    async with browser(app) as maker, browser(app) as checker:
        await sign_in_staff(maker, app, MAKER, "FINANCE")
        await sign_in_staff(checker, app, CHECKER, "FINANCE")
        created = (await propose(maker, wallet["wallet_number"], "5000", direction="DEBIT")).json()
        result = await checker.post(
            f"/api/v1/admin/approvals/{created['approval']['id']}/approve", json={}
        )
        assert result.status_code == 200
        assert (result.json()["status"], result.json()["failure_code"]) == (
            "FAILED",
            "INSUFFICIENT_BALANCE",
        )
        txn = (
            await checker.get(f"/api/v1/admin/transactions/{created['transaction_reference']}")
        ).json()
        assert txn["status"] == "CANCELLED"
    assert (await client.get("/api/v1/wallet")).json()["balance"]["amount"] == "0"


async def test_reject_cancel_and_expire(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock, db: Database
) -> None:
    wallet = await customer_with_pin(client, app)
    async with browser(app) as maker, browser(app) as checker:
        await sign_in_staff(maker, app, MAKER, "FINANCE")
        await sign_in_staff(checker, app, CHECKER, "FINANCE")

        first = (await propose(maker, wallet["wallet_number"], "1000")).json()
        short = await checker.post(
            f"/api/v1/admin/approvals/{first['approval']['id']}/reject", json={"note": "x"}
        )
        assert short.status_code == 422
        rejected = await checker.post(
            f"/api/v1/admin/approvals/{first['approval']['id']}/reject",
            json={"note": "không có ticket"},
        )
        assert rejected.json()["status"] == "REJECTED"
        txn = (
            await checker.get(f"/api/v1/admin/transactions/{first['transaction_reference']}")
        ).json()
        assert (txn["status"], txn["events"][-1]["reason_code"]) == (
            "CANCELLED",
            "APPROVAL_REJECTED",
        )

        second = (await propose(maker, wallet["wallet_number"], "2000")).json()
        not_mine = await checker.post(f"/api/v1/admin/approvals/{second['approval']['id']}/cancel")
        assert not_mine.status_code == 403
        cancelled = await maker.post(f"/api/v1/admin/approvals/{second['approval']['id']}/cancel")
        assert cancelled.json()["status"] == "CANCELLED"

        third = (await propose(maker, wallet["wallet_number"], "3000")).json()
        clock.advance(timedelta(hours=73))
        # Sessions expired with time; the expiry job runs without them.
        jobs = [
            j
            for j in build_jobs(db, clock, app.state.identity.settings)
            if j.name == "expire_approval_requests"
        ]
        assert await JobScheduler(db, clock, jobs).run_due() == ["expire_approval_requests"]
    async with db.transaction() as session:
        status = await session.scalar(
            select(ApprovalRequest.status).where(
                ApprovalRequest.id == uuid.UUID(third["approval"]["id"])
            )
        )
        assert status == "EXPIRED"
        txn_status = await session.scalar(
            text("SELECT status FROM app.transactions WHERE reference = :r"),
            {"r": third["transaction_reference"]},
        )
        assert txn_status == "CANCELLED"


async def test_staff_freeze_requires_approval_to_lift(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    wallet = await customer_with_pin(client, app)
    number = wallet["wallet_number"]
    async with browser(app) as analyst, browser(app) as second:
        await sign_in_staff(analyst, app, "risk1@example.com", "RISK_ANALYST")
        await sign_in_staff(second, app, "risk2@example.com", "RISK_ANALYST")

        not_frozen = await analyst.post(
            f"/api/v1/admin/wallets/{number}/unfreeze-requests",
            json={"reason": "kiểm tra xong rồi"},
        )
        assert not_frozen.json()["code"] == "WALLET_STATE_CONFLICT"
        frozen = await analyst.post(
            f"/api/v1/admin/wallets/{number}/freeze", json={"reason": "nghi ngờ gian lận"}
        )
        assert (frozen.json()["status"], frozen.json()["freeze_source"]) == ("FROZEN", "STAFF")

        # The customer cannot lift a staff freeze.
        lifted = await client.post("/api/v1/wallet/unfreeze", json={"pin": PIN})
        assert lifted.json()["code"] == "WALLET_STATE_CONFLICT"

        request = await analyst.post(
            f"/api/v1/admin/wallets/{number}/unfreeze-requests",
            json={"reason": "đã xác minh chủ ví"},
        )
        assert request.status_code == 201, request.text
        duplicate = await second.post(
            f"/api/v1/admin/wallets/{number}/unfreeze-requests",
            json={"reason": "đã xác minh chủ ví"},
        )
        assert duplicate.json()["code"] == "APPROVAL_DUPLICATE"
        approved = await second.post(
            f"/api/v1/admin/approvals/{request.json()['id']}/approve", json={}
        )
        assert approved.json()["status"] == "EXECUTED", approved.text
        view = (await second.get(f"/api/v1/admin/wallets/{number}")).json()
        assert view["status"] == "ACTIVE"


async def test_step_up_needed_to_approve(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    wallet = await customer_with_pin(client, app)
    async with browser(app) as maker, browser(app) as checker:
        await sign_in_staff(maker, app, MAKER, "FINANCE")
        secret = await sign_in_staff(checker, app, CHECKER, "FINANCE")
        created = (await propose(maker, wallet["wallet_number"], "1000")).json()
        clock.advance(timedelta(minutes=16))
        await checker.get("/api/v1/admin/me")
        stale = await checker.post(
            f"/api/v1/admin/approvals/{created['approval']['id']}/approve", json={}
        )
        assert stale.json()["code"] == "MFA_STEP_UP_REQUIRED"
        await checker.post("/api/v1/admin/auth/step-up", json={"code": totp_now(secret, app)})
        ok = await checker.post(
            f"/api/v1/admin/approvals/{created['approval']['id']}/approve", json={}
        )
        assert ok.json()["status"] == "EXECUTED"


async def test_roles_without_approve_permission_see_no_approvals(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    async with browser(app) as support:
        await sign_in_staff(support, app, "support@example.com", "SUPPORT")
        assert (await support.get("/api/v1/admin/approvals")).status_code == 403
        denied = await support.post(
            "/api/v1/admin/adjustments", headers={"Idempotency-Key": "support-key-1"}, json={}
        )
        assert denied.status_code == 403


async def test_reconciliation_run_is_clean(
    client: httpx.AsyncClient, app: FastAPI, db: Database, clock: FixedClock
) -> None:
    async with db.transaction() as session:
        wallet = await ensure_wallet(session, clock, await make_user(session, clock))
    await fund(db, clock, wallet, 10_000)
    async with browser(app) as finance:
        await sign_in_staff(finance, app, "finance@example.com", "FINANCE")
        run = await finance.post("/api/v1/admin/reconciliation/runs")
        assert run.status_code == 201, run.text
        body = run.json()
        assert (body["status"], body["violations"], body["items"]) == ("SUCCEEDED", 0, [])
        assert body["stats"]["checked"] == ["I1", "I2", "I3", "I4", "I5"]
        listing = (await finance.get("/api/v1/admin/reconciliation/runs")).json()
        assert listing["total"] == 1
        detail = await finance.get(f"/api/v1/admin/reconciliation/runs/{body['id']}")
        assert detail.json()["id"] == body["id"]
        missing = await finance.get(f"/api/v1/admin/reconciliation/runs/{uuid.uuid4()}")
        assert missing.status_code == 404


async def test_reconciliation_reports_hold_mismatch(
    migrator_db: Database, clock: FixedClock
) -> None:
    """I5: a HOLD balance not backed by held transactions is reported (and rolled back here)."""

    async with migrator_db.transaction() as session:
        await session.execute(
            text(
                "UPDATE app.account_balances SET balance_minor = 7 WHERE account_id ="
                " (SELECT id FROM app.ledger_accounts WHERE code = 'HOLD' AND currency = 'VND')"
            )
        )
        result = await run_internal(session, clock, triggered_by=None)
        invariants = {i.invariant for i in result.items}
        assert "I5" in invariants
        assert result.run.violations == len(result.items) > 0
        await session.rollback()
