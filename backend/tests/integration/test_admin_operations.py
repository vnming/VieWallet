"""Phase 8: staff refunds (user-flows §9), fee/limit versions, role changes, provider
reconciliation (FT-28), simulator panel and provider incident (user-flows §10), console views,
customer lookup and the admin authorization matrix."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.clock import FixedClock
from app.modules.access.public import revoke_role
from app.modules.audit.public import AuditLog
from tests.integration.finance_helpers import sign_in_staff
from tests.integration.money_helpers import (
    approve_checkout,
    assert_books_clean,
    balance,
    customer,
    deposit,
    finance,
    key,
    settle,
)
from tests.integration.spend_helpers import confirm, make_merchant, quoted, two_customers
from tests.integration.test_staff_access import PUBLIC_ADMIN_ROUTES, admin_routes

OPS = "ops@example.com"
FIN = "fin@example.com"
ADMIN_A = "admin-a@example.com"
ADMIN_B = "admin-b@example.com"
ROOT = "root@example.com"


async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://viewallet.test", headers={"X-VW-CSRF": "1"}
    ) as http:
        yield http


@pytest.fixture
async def staff_a(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async for http in _client(app):
        yield http


@pytest.fixture
async def staff_b(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async for http in _client(app):
        yield http


async def audit_actions(app: FastAPI, target_id: str) -> list[str]:
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        rows = await session.scalars(
            select(AuditLog.action).where(AuditLog.target_id == target_id).order_by(AuditLog.id)
        )
        return list(rows.all())


async def merchant_payment(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI, amount: int
) -> str:
    """Lan pays Minh's shop; returns the payment reference."""
    await two_customers(client, other_client, app)
    await make_merchant(app)
    r = await other_client.post(
        "/api/v1/payment-requests",
        headers={"Idempotency-Key": key()},
        json={"kind": "MERCHANT", "amount": str(amount)},
    )
    assert r.status_code == 201, r.text
    quoted = await client.post(
        f"/api/v1/payment-requests/{r.json()['code']}/pay",
        headers={"Idempotency-Key": key()},
        json={"amount": None},
    )
    assert quoted.status_code == 201, quoted.text
    paid = await confirm(client, quoted.json()["reference"], kind="payments")
    assert paid.status_code == 200, paid.text
    return str(paid.json()["reference"])


async def propose_refund(
    maker: httpx.AsyncClient, reference: str, amount: int, idem: str | None = None
) -> httpx.Response:
    return await maker.post(
        f"/api/v1/admin/transactions/{reference}/refund-requests",
        headers={"Idempotency-Key": idem or key()},
        json={"amount": str(amount), "reason": "Khách khiếu nại món bị thiếu"},
    )


# ---------------------------------------------------------------- staff refunds (journey 9)


async def test_journey9_staff_refund_needs_a_second_person(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    staff_a: httpx.AsyncClient,
    staff_b: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    ref = await merchant_payment(client, other_client, app, 300_000)
    await sign_in_staff(staff_a, app, OPS, "OPERATIONS")
    await sign_in_staff(staff_b, app, FIN, "FINANCE")

    idem = key()
    proposed = await propose_refund(staff_a, ref, 120_000, idem)
    assert proposed.status_code == 201, proposed.text
    approval = proposed.json()["approval"]
    assert approval["status"] == "PENDING"
    assert approval["payload"]["already_refunded"] == "0"
    replay = await propose_refund(staff_a, ref, 120_000, idem)
    assert replay.headers.get("Idempotent-Replayed") == "true"
    # The pending amount counts: 120 000 + 200 000 > 300 000.
    too_much = await propose_refund(staff_a, ref, 200_000)
    assert too_much.json()["code"] == "REFUND_EXCEEDS_REMAINING"

    # Nothing visible to the customer before approval.
    refunds = (await client.get("/api/v1/transactions", params={"type": "REFUND"})).json()
    assert refunds["items"] == []
    hidden = await client.get(f"/api/v1/transactions/{proposed.json()['refund_reference']}")
    assert hidden.status_code == 404

    # Operations cannot approve (no permission); Finance can.
    own = await staff_a.post(f"/api/v1/admin/approvals/{approval['id']}/approve", json={})
    assert own.status_code == 403  # Operations may propose refunds, not approve them
    listed = (await staff_b.get("/api/v1/admin/approvals")).json()["items"]
    assert [a["id"] for a in listed if a["action_type"] == "REFUND"] == [approval["id"]]
    assert listed[0]["can_decide"] is True
    done = await staff_b.post(
        f"/api/v1/admin/approvals/{approval['id']}/approve", json={"note": "Đã xem hóa đơn"}
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "EXECUTED"

    assert await balance(client) == 3_000_000 - 300_000 + 120_000
    seen = (await client.get("/api/v1/transactions", params={"type": "REFUND"})).json()["items"]
    assert [(t["status"], t["amount"]["amount"]) for t in seen] == [("COMPLETED", "120000")]
    payment = (await other_client.get(f"/api/v1/merchant/payments/{ref}")).json()
    assert payment["status"] == "PARTIALLY_REFUNDED"
    assert await audit_actions(app, approval["id"]) == ["approval.proposed", "approval.executed"]
    await assert_books_clean(app)


async def test_staff_refund_rechecks_at_approval_and_can_be_rejected(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    staff_a: httpx.AsyncClient,
    staff_b: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    ref = await merchant_payment(client, other_client, app, 200_000)
    await sign_in_staff(staff_a, app, OPS, "OPERATIONS")
    await sign_in_staff(staff_b, app, FIN, "FINANCE")

    rejected = (await propose_refund(staff_a, ref, 50_000)).json()
    r = await staff_b.post(
        f"/api/v1/admin/approvals/{rejected['approval']['id']}/reject",
        json={"note": "Không đủ bằng chứng"},
    )
    assert r.json()["status"] == "REJECTED"

    full = (await propose_refund(staff_a, ref, 200_000)).json()
    # Meanwhile the merchant refunds part of it directly.
    own = await other_client.post(
        f"/api/v1/merchant/payments/{ref}/refunds",
        headers={"Idempotency-Key": key()},
        json={"amount": "10000", "reason": "trả một món"},
    )
    assert own.json()["status"] == "COMPLETED", own.text
    late = await staff_b.post(f"/api/v1/admin/approvals/{full['approval']['id']}/approve", json={})
    assert late.json()["status"] == "FAILED"
    assert late.json()["failure_code"] == "REFUND_EXCEEDS_REMAINING"
    assert await balance(client) == 3_000_000 - 200_000 + 10_000
    await assert_books_clean(app)


async def test_refund_only_for_completed_payments(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    staff_a: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    lan, minh = await two_customers(client, other_client, app)
    sent = await quoted(client, minh["wallet_number"], 10_000)
    await sign_in_staff(staff_a, app, OPS, "OPERATIONS")
    r = await propose_refund(staff_a, sent["reference"], 1_000)
    assert r.json()["code"] == "TXN_INVALID_STATE"
    bad = await staff_a.post(
        "/api/v1/admin/transactions/X/refund-requests",
        headers={"Idempotency-Key": key()},
        json={"amount": "-5", "reason": "Khách khiếu nại món bị thiếu"},
    )
    assert bad.status_code == 422
    assert lan["wallet_number"]


# ---------------------------------------------------------------- fee and limit versions


async def test_fee_version_maker_checker(
    staff_a: httpx.AsyncClient, staff_b: httpx.AsyncClient, app: FastAPI
) -> None:
    await sign_in_staff(staff_a, app, ADMIN_A, "ADMIN")
    await sign_in_staff(staff_b, app, ROOT, "SUPER_ADMIN")
    fees = (await staff_a.get("/api/v1/admin/config/fees")).json()
    rule = next(f for f in fees if f["txn_type"] == "WITHDRAWAL")
    body = {
        "fixed": str(int(rule["fixed"]) + 1000),
        "bps": rule["bps"],
        "min_fee": rule["min_fee"],
        "max_fee": rule["max_fee"],
        "reason": "Điều chỉnh phí rút tiền theo chi phí mô phỏng",
    }
    same = await staff_a.post(
        f"/api/v1/admin/config/fees/{rule['code']}/versions", json={**body, "fixed": rule["fixed"]}
    )
    assert same.json()["code"] == "PRICING_RULE_UNCHANGED"
    proposed = await staff_a.post(f"/api/v1/admin/config/fees/{rule['code']}/versions", json=body)
    assert proposed.status_code == 201, proposed.text
    payload = proposed.json()["payload"]
    assert (payload["before"]["fixed_minor"], payload["after"]["fixed_minor"]) == (
        rule["fixed"],
        body["fixed"],
    )
    duplicate = await staff_a.post(f"/api/v1/admin/config/fees/{rule['code']}/versions", json=body)
    assert duplicate.json()["code"] == "PRICING_RULE_DRAFT_EXISTS"
    # ADMIN may propose but not approve configuration.
    approve = f"/api/v1/admin/approvals/{proposed.json()['id']}/approve"
    assert (await staff_a.post(approve, json={})).status_code == 404  # not even visible
    assert (await staff_b.post(approve, json={})).json()["status"] == "EXECUTED"
    after = [
        f
        for f in (await staff_b.get("/api/v1/admin/config/fees")).json()
        if f["code"] == rule["code"]
    ]
    assert [(f["version"], f["status"], f["fixed"]) for f in after] == [
        (2, "ACTIVE", body["fixed"])
    ]
    assert await audit_actions(app, after[0]["id"]) == ["pricing.rule_activated"]


async def test_limit_version_rejected_keeps_the_active_rule(
    staff_a: httpx.AsyncClient, staff_b: httpx.AsyncClient, app: FastAPI
) -> None:
    await sign_in_staff(staff_a, app, ADMIN_A, "ADMIN")
    await sign_in_staff(staff_b, app, ROOT, "SUPER_ADMIN")
    limits = (await staff_a.get("/api/v1/admin/config/limits")).json()
    rule = next(r for r in limits if r["period"] == "DAILY" and r["txn_type"] == "TRANSFER")
    bad = await staff_a.post(
        f"/api/v1/admin/config/limits/{rule['code']}/versions",
        json={"max_amount": "5", "min_amount": "10", "reason": "Thử giá trị không hợp lệ"},
    )
    assert bad.status_code == 422
    proposed = await staff_a.post(
        f"/api/v1/admin/config/limits/{rule['code']}/versions",
        json={"max_amount": "1000", "reason": "Giảm hạn mức để thử nghiệm"},
    )
    assert proposed.status_code == 201, proposed.text
    drafts = [
        r
        for r in (await staff_a.get("/api/v1/admin/config/limits")).json()
        if r["code"] == rule["code"]
    ]
    assert sorted(r["status"] for r in drafts) == ["ACTIVE", "DRAFT"]
    r = await staff_b.post(
        f"/api/v1/admin/approvals/{proposed.json()['id']}/reject", json={"note": "Quá thấp"}
    )
    assert r.json()["status"] == "REJECTED"
    after = [
        r
        for r in (await staff_a.get("/api/v1/admin/config/limits")).json()
        if r["code"] == rule["code"]
    ]
    assert [(r["version"], r["status"]) for r in after] == [(1, "ACTIVE")]


# ---------------------------------------------------------------- role changes


async def test_role_changes_need_another_admin(
    staff_a: httpx.AsyncClient,
    staff_b: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    await sign_in_staff(staff_a, app, ADMIN_A, "ADMIN")
    await sign_in_staff(staff_b, app, ADMIN_B, "ADMIN")
    await sign_in_staff(other_client, app, OPS, "SUPPORT")
    target = str((await other_client.get("/api/v1/admin/me")).json()["id"])

    catalog = (await staff_a.get("/api/v1/admin/roles")).json()
    assert {r["code"] for r in catalog["roles"]} >= {"SUPPORT", "SUPER_ADMIN"}
    staff = {s["email"]: s for s in (await staff_a.get("/api/v1/admin/staff")).json()}
    assert staff[OPS]["roles"] == ["SUPPORT"]

    self_change = await staff_a.post(
        f"/api/v1/admin/staff/{staff[ADMIN_A]['id']}/role-changes",
        json={"role": "FINANCE", "op": "GRANT", "reason": "Tự cấp quyền cho mình"},
    )
    assert self_change.json()["code"] == "APPROVAL_SELF_DECISION"
    super_grant = await staff_a.post(
        f"/api/v1/admin/staff/{target}/role-changes",
        json={"role": "SUPER_ADMIN", "op": "GRANT", "reason": "Cấp quyền cao nhất"},
    )
    assert super_grant.json()["code"] == "APPROVAL_SUPER_ADMIN_ONLY"

    grant = await staff_a.post(
        f"/api/v1/admin/staff/{target}/role-changes",
        json={"role": "KYC_REVIEWER", "op": "GRANT", "reason": "Hỗ trợ hàng đợi KYC tuần này"},
    )
    assert grant.status_code == 201, grant.text
    assert (await other_client.get("/api/v1/admin/kyc/submissions")).status_code == 403
    done = await staff_b.post(f"/api/v1/admin/approvals/{grant.json()['id']}/approve", json={})
    assert done.json()["status"] == "EXECUTED", done.text
    # Takes effect on the next request (permissions are loaded per request).
    assert (await other_client.get("/api/v1/admin/kyc/submissions")).status_code == 200
    again = await staff_a.post(
        f"/api/v1/admin/staff/{target}/role-changes",
        json={"role": "KYC_REVIEWER", "op": "GRANT", "reason": "Cấp lại lần nữa để thử"},
    )
    replay = await staff_b.post(f"/api/v1/admin/approvals/{again.json()['id']}/approve", json={})
    assert replay.json()["failure_code"] == "ROLE_ALREADY_HELD"
    assert "access.role_granted" in await audit_actions(app, target)


async def test_target_of_a_role_change_cannot_decide_it(
    staff_a: httpx.AsyncClient, staff_b: httpx.AsyncClient, app: FastAPI
) -> None:
    await sign_in_staff(staff_a, app, ADMIN_A, "ADMIN")
    await sign_in_staff(staff_b, app, ADMIN_B, "ADMIN")
    staff = {s["email"]: s for s in (await staff_a.get("/api/v1/admin/staff")).json()}
    proposed = await staff_a.post(
        f"/api/v1/admin/staff/{staff[ADMIN_B]['id']}/role-changes",
        json={"role": "ADMIN", "op": "REVOKE", "reason": "Nhân viên chuyển bộ phận"},
    )
    r = await staff_b.post(f"/api/v1/admin/approvals/{proposed.json()['id']}/approve", json={})
    assert r.json()["code"] == "APPROVAL_SELF_DECISION"


# ---------------------------------------------------------------- reconciliation (FT-28)


async def completed_deposit(
    client: httpx.AsyncClient, app: FastAPI, sim_app: FastAPI, amount: int
) -> str:
    created = (await deposit(client, amount)).json()
    await approve_checkout(sim_app, created["checkout_url"])
    await settle(app, sim_app)
    return str(created["reference"])


async def test_ft28_provider_statement_reconciliation(
    client: httpx.AsyncClient,
    staff_a: httpx.AsyncClient,
    app: FastAPI,
    sim_app: FastAPI,
    clock: FixedClock,
) -> None:
    await customer(client, app)
    await completed_deposit(client, app, sim_app, 250_000)
    await completed_deposit(client, app, sim_app, 888_000)  # statement shows +1 000
    await sign_in_staff(staff_a, app, FIN, "FINANCE")
    today = clock.now().astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    run = await staff_a.post(
        "/api/v1/admin/reconciliation/provider-runs", json={"business_date": today.isoformat()}
    )
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["kind"] == "PROVIDER"
    assert body["stats"]["statement_lines"] == 2
    assert [(i["type"], i["expected"], i["actual"]) for i in body["items"]] == [
        ("AMOUNT_MISMATCH", "888000 VND", "889000 VND")
    ]
    item = body["items"][0]["id"]
    listed = (await staff_a.get("/api/v1/admin/reconciliation/items")).json()
    assert item in [i["id"] for i in listed]
    step = await staff_a.post(
        f"/api/v1/admin/reconciliation/items/{item}",
        json={"status": "RESOLVED", "note": "Sao kê provider ghi sai, đã xác nhận với provider"},
    )
    assert step.json()["status"] == "RESOLVED", step.text
    closed = await staff_a.post(
        f"/api/v1/admin/reconciliation/items/{item}",
        json={"status": "INVESTIGATING", "note": "Mở lại thử"},
    )
    assert closed.json()["code"] == "RECONCILIATION_ITEM_CLOSED"
    assert await audit_actions(app, item) == ["reconciliation.item_updated"]
    empty_day = await staff_a.post(
        "/api/v1/admin/reconciliation/provider-runs",
        json={"business_date": (today - timedelta(days=3)).isoformat()},
    )
    assert empty_day.json()["violations"] == 0


# ---------------------------------------------------------------- provider incident (journey 10)


async def test_journey10_provider_outage_is_visible_and_controlled(
    client: httpx.AsyncClient, staff_a: httpx.AsyncClient, staff_b: httpx.AsyncClient, app: FastAPI
) -> None:
    await customer(client, app)
    await sign_in_staff(staff_a, app, OPS, "OPERATIONS")
    await sign_in_staff(staff_b, app, FIN, "FINANCE")
    assert (await staff_b.get("/api/v1/admin/simulator")).status_code == 403
    panel = await staff_a.get("/api/v1/admin/simulator")
    assert panel.json()["mode"] == "NORMAL", panel.text

    outage = await staff_a.put(
        "/api/v1/admin/simulator/mode", json={"mode": "OUTAGE", "reason": "Diễn tập sự cố"}
    )
    assert outage.json()["mode"] == "OUTAGE"
    failed = (await deposit(client, 100_000)).json()
    assert (failed["status"], failed["failure_code"]) == ("FAILED", "PROVIDER_UNAVAILABLE")
    health = (await staff_a.get("/api/v1/admin/system-health")).json()
    assert health["provider"]["status"] == "DOWN"
    assert health["provider"]["mode"] == "OUTAGE"
    assert health["components"]["database"] == "UP"

    normal = await staff_a.put(
        "/api/v1/admin/simulator/mode", json={"mode": "NORMAL", "reason": "Kết thúc diễn tập"}
    )
    assert normal.json()["mode"] == "NORMAL"
    assert (await staff_a.get("/api/v1/admin/system-health")).json()["provider"]["mode"] == "NORMAL"
    assert (await audit_actions(app, "SIMULATOR")).count("simulator.mode_changed") == 2

    app.state.sim_control = None  # production-like: the panel is off
    off = await staff_a.get("/api/v1/admin/simulator")
    assert off.json()["code"] == "SIMULATOR_PANEL_DISABLED"


async def test_operations_queue_lists_stuck_money_movements(
    client: httpx.AsyncClient,
    staff_a: httpx.AsyncClient,
    app: FastAPI,
    clock: FixedClock,
) -> None:
    await customer(client, app)
    created = (await deposit(client, 150_000)).json()
    await sign_in_staff(staff_a, app, OPS, "OPERATIONS")
    assert (await staff_a.get("/api/v1/admin/operations-queue")).json()["stuck_transactions"] == []
    for _ in range(2):  # stay inside the 30-minute idle timeout of the staff session
        clock.advance(timedelta(minutes=16))
        await staff_a.get("/api/v1/admin/me")
    queue = (await staff_a.get("/api/v1/admin/operations-queue")).json()
    assert [t["reference"] for t in queue["stuck_transactions"]] == [created["reference"]]
    assert queue["stuck_transactions"][0]["age_minutes"] >= 31


async def test_dashboard_sections_follow_permissions(
    client: httpx.AsyncClient,
    staff_a: httpx.AsyncClient,
    staff_b: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    await customer(client, app)
    await sign_in_staff(staff_a, app, OPS, "SUPPORT")
    await sign_in_staff(staff_b, app, FIN, "FINANCE")
    support = (await staff_a.get("/api/v1/admin/dashboard")).json()
    assert support["users"]["customers"] >= 1
    assert support["queues"]["approvals"] is None
    assert support["queues"]["tickets"] == 0
    fin = (await staff_b.get("/api/v1/admin/dashboard")).json()
    assert fin["queues"]["approvals"] == 0
    assert fin["queues"]["tickets"] is None
    assert isinstance(fin["transactions"]["completed_today"], list)


# ---------------------------------------------------------------- customer lookup


async def test_customer_lookup_is_masked_and_reveal_is_audited(
    client: httpx.AsyncClient, staff_a: httpx.AsyncClient, staff_b: httpx.AsyncClient, app: FastAPI
) -> None:
    lan = await customer(client, app)
    await sign_in_staff(staff_a, app, OPS, "SUPPORT")
    await sign_in_staff(staff_b, app, FIN, "RISK_ANALYST")
    assert (await staff_a.get("/api/v1/admin/users", params={"q": "ab"})).status_code == 422
    found = (await staff_a.get("/api/v1/admin/users", params={"q": "lan@"})).json()
    assert len(found) == 1
    assert found[0]["email"] == "l***@example.com"
    by_wallet = (
        await staff_a.get("/api/v1/admin/users", params={"q": lan["wallet_number"]})
    ).json()
    assert [u["id"] for u in by_wallet] == [found[0]["id"]]
    detail = (await staff_a.get(f"/api/v1/admin/users/{found[0]['id']}")).json()
    assert detail["wallet"]["wallet_number"].startswith("******")
    assert detail["roles"] == ["CUSTOMER"]

    denied = await staff_a.post(f"/api/v1/admin/users/{found[0]['id']}/pii-reveals")
    assert denied.status_code == 403
    revealed = await staff_b.post(f"/api/v1/admin/users/{found[0]['id']}/pii-reveals")
    assert revealed.json()["email"] == "lan@example.com", revealed.text
    assert (await audit_actions(app, found[0]["id"])).count("user.pii_revealed") == 1
    staff_id = (await staff_a.get("/api/v1/admin/me")).json()["id"]
    assert (await staff_a.get(f"/api/v1/admin/users/{staff_id}")).status_code == 404


# ---------------------------------------------------------------- authorization matrix

# Routes every signed-in staff member may call, whatever their roles.
ANY_STAFF = {
    ("GET", "/api/v1/admin/me"),
    ("POST", "/api/v1/admin/auth/logout"),
    ("POST", "/api/v1/admin/auth/step-up"),
    ("GET", "/api/v1/admin/dashboard"),
    ("GET", "/api/v1/admin/system-health"),
    ("POST", "/api/v1/admin/approvals/{approval_id}/cancel"),
}


async def test_staff_without_permissions_is_refused_everywhere(
    staff_a: httpx.AsyncClient, app: FastAPI
) -> None:
    """FR authZ matrix: a staff member holding no role gets 403 on every admin operation except
    the explicit any-staff set (404/422 there only come from missing resources/bodies)."""
    await sign_in_staff(staff_a, app, OPS, "SUPPORT")
    user_id = uuid.UUID((await staff_a.get("/api/v1/admin/me")).json()["id"])
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        await revoke_role(session, user_id=user_id, role_code="SUPPORT")
    routes = [r for r in admin_routes(app) if r[1] not in PUBLIC_ADMIN_ROUTES]
    checked = 0
    offenders: list[tuple[str, str, int]] = []
    for method, path in routes:
        if (method, path) in ANY_STAFF:
            continue
        concrete = path.replace("{", "").replace("}", "")
        for name in ("approval_id", "user_id", "run_id", "item_id"):
            concrete = concrete.replace(name, str(uuid.uuid4()))
        response = await staff_a.request(
            method, concrete, json={}, headers={"Idempotency-Key": key()}
        )
        if response.status_code != 403:
            offenders.append((method, path, response.status_code))
        checked += 1
    assert offenders == []
    assert checked >= 40
