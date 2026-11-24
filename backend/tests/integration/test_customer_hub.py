"""Phase 7: profile and settings, history filters, categories, CSV exports, help center,
support tickets."""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.clock import FixedClock
from app.modules.transactions.exports import process_pending
from tests.integration.finance_helpers import sign_in_staff
from tests.integration.money_helpers import customer, drain, finance, key, make_tier1
from tests.integration.spend_helpers import (
    OTHER,
    confirm,
    make_merchant,
    send,
    two_customers,
)

# ---------------------------------------------------------------- profile and settings


async def test_profile_phone_theme_privacy_and_name_lock(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await customer(client, app)
    await customer(other_client, app, email=OTHER)
    me = (await client.get("/api/v1/me")).json()
    assert (me["phone"], me["theme"], me["privacy_mode"]) == (None, "SYSTEM", False)

    updated = await client.patch(
        "/api/v1/me", json={"phone": "091 234 5678", "theme": "DARK", "privacy_mode": True}
    )
    assert updated.status_code == 200, updated.text
    assert (updated.json()["phone"], updated.json()["theme"], updated.json()["privacy_mode"]) == (
        "+84912345678",
        "DARK",
        True,
    )
    bad = await client.patch("/api/v1/me", json={"phone": "12345"})
    assert bad.json()["code"] == "VALIDATION_ERROR"
    assert (await client.patch("/api/v1/me", json={"theme": "PINK"})).status_code == 422
    taken = await other_client.patch("/api/v1/me", json={"phone": "+84912345678"})
    assert taken.json()["code"] == "PHONE_TAKEN"
    removed = await client.patch("/api/v1/me", json={"phone": ""})
    assert removed.json()["phone"] is None
    assert (await client.patch("/api/v1/me", json={})).status_code == 422

    # After KYC the verified name is locked; the display name stays free.
    await make_tier1(app)
    locked = await client.patch("/api/v1/me", json={"full_name": "Tên Khác Hẳn"})
    assert locked.json()["code"] == "PROFILE_NAME_LOCKED"
    same = await client.patch(
        "/api/v1/me", json={"full_name": "Trần Thị Lan", "display_name": "Lan"}
    )
    assert same.json()["display_name"] == "Lan"


# ---------------------------------------------------------------- history


async def test_history_filters_and_categories(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    lan, minh = await two_customers(client, other_client, app)
    sent = await send(client, minh["wallet_number"], 120_000)
    clock.advance(timedelta(days=2))
    await send(other_client, lan["wallet_number"], 45_000)

    async def refs(**params: Any) -> list[str]:
        r = await client.get("/api/v1/transactions", params=params)
        assert r.status_code == 200, r.text
        return [t["reference"] for t in r.json()["items"]]

    all_refs = await refs()
    assert len(all_refs) == 2
    assert await refs(direction="OUT") == [sent["reference"]]
    assert await refs(min_amount=100_000) == [sent["reference"]]
    assert await refs(max_amount=50_000) == [all_refs[0]]
    assert await refs(q=sent["reference"].lower()) == [sent["reference"]]
    assert await refs(q="TRAN T***") == all_refs  # counterparty name, both directions
    assert await refs(q="100%") == []  # wildcards are literal
    today = clock.now().date()
    assert await refs(**{"from": str(today), "to": str(today)}) == [all_refs[0]]
    assert len(await refs(**{"from": str(today - timedelta(days=3))})) == 2

    items = (await client.get("/api/v1/transactions")).json()["items"]
    by_ref = {t["reference"]: t for t in items}
    assert by_ref[sent["reference"]]["category"] == {
        "code": "TRANSFER_OUT",
        "name": "Chuyển tiền đi",
    }
    assert by_ref[all_refs[0]]["category"]["code"] == "INCOME"
    categories = (await client.get("/api/v1/transactions/categories")).json()
    assert {"code": "FOOD", "name": "Ăn uống"} in categories

    changed = await client.patch(
        f"/api/v1/transactions/{sent['reference']}/category", json={"category_code": "FOOD"}
    )
    assert changed.json()["category"]["code"] == "FOOD"
    # The override is personal: the receiver still sees their own default.
    theirs = (await other_client.get(f"/api/v1/transactions/{sent['reference']}")).json()
    assert theirs["category"]["code"] == "INCOME"
    unknown = await client.patch(
        f"/api/v1/transactions/{sent['reference']}/category", json={"category_code": "NOPE"}
    )
    assert unknown.status_code == 422


async def test_payment_category_follows_the_shop(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await two_customers(client, other_client, app)
    await make_merchant(app)
    request = (
        await other_client.post(
            "/api/v1/payment-requests",
            headers={"Idempotency-Key": key()},
            json={"kind": "MERCHANT", "amount": "50000"},
        )
    ).json()
    quote = (
        await client.post(
            f"/api/v1/payment-requests/{request['code']}/pay",
            headers={"Idempotency-Key": key()},
            json={},
        )
    ).json()
    await confirm(client, quote["reference"], kind="payments")
    detail = (await client.get(f"/api/v1/transactions/{quote['reference']}")).json()
    assert detail["category"]["code"] == "FOOD"  # MCC 5814: coffee shop


# ---------------------------------------------------------------- CSV export


async def test_csv_export(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    _, minh = await two_customers(client, other_client, app)
    q = await client.post(
        "/api/v1/transfers",
        headers={"Idempotency-Key": key()},
        json={"wallet_number": minh["wallet_number"], "amount": "10000", "note": "=1+2"},
    )
    await confirm(client, q.json()["reference"])
    today = clock.now().date()
    bad = await client.post(
        "/api/v1/transactions/exports",
        headers={"Idempotency-Key": key()},
        json={"date_from": str(today), "date_to": str(today - timedelta(days=1))},
    )
    assert bad.status_code == 422
    too_long = await client.post(
        "/api/v1/transactions/exports",
        headers={"Idempotency-Key": key()},
        json={"date_from": str(today - timedelta(days=400)), "date_to": str(today)},
    )
    assert too_long.status_code == 422

    idem = key()
    created = await client.post(
        "/api/v1/transactions/exports",
        headers={"Idempotency-Key": idem},
        json={"date_from": str(today - timedelta(days=1)), "date_to": str(today)},
    )
    assert created.status_code == 202, created.text
    job = created.json()
    assert (job["status"], job["download_url"]) == ("PENDING", None)
    replay = await client.post(
        "/api/v1/transactions/exports",
        headers={"Idempotency-Key": idem},
        json={"date_from": str(today - timedelta(days=1)), "date_to": str(today)},
    )
    assert replay.json()["id"] == job["id"]
    early = await client.get(f"/api/v1/transactions/exports/{job['id']}/download")
    assert early.json()["code"] == "EXPORT_NOT_READY"
    assert (await other_client.get(f"/api/v1/transactions/exports/{job['id']}")).status_code == 404

    ctx = finance(app)
    assert await process_pending(ctx.db, ctx.clock, ctx.settings.business_timezone) == 1
    ready = (await client.get(f"/api/v1/transactions/exports/{job['id']}")).json()
    assert (ready["status"], ready["row_count"]) == ("READY", 1)  # funding is not a transaction
    download = await client.get(ready["download_url"])
    assert download.headers["content-type"].startswith("text/csv")
    assert "attachment" in download.headers["content-disposition"]
    text = download.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "Mã giao dịch"
    transfer_row = next(r for r in rows[1:] if r[2] == "TRANSFER")
    assert transfer_row[3:8] == ["OUT", "COMPLETED", "10000", "0", "-10000"]
    assert transfer_row[10] == "'=1+2"  # formula injection neutralised
    assert transfer_row[11] == "TRANSFER_OUT"

    clock.advance(timedelta(hours=25))
    await process_pending(ctx.db, ctx.clock, ctx.settings.business_timezone)
    # The session is still valid (idle 7 days); the file is gone.
    expired = (await client.get(f"/api/v1/transactions/exports/{job['id']}")).json()
    assert expired["status"] == "EXPIRED"


# ---------------------------------------------------------------- help center


async def test_help_center_search_is_accent_insensitive(client: httpx.AsyncClient) -> None:
    listed = (await client.get("/api/v1/help/articles")).json()
    assert len(listed) == 9
    found = (await client.get("/api/v1/help/articles", params={"q": "rut tien"})).json()
    assert "withdraw" in [a["slug"] for a in found]
    assert (await client.get("/api/v1/help/articles", params={"q": "zzzqqq"})).json() == []
    english = (
        await client.get("/api/v1/help/articles", params={"q": "freeze", "locale": "en"})
    ).json()
    assert [a["slug"] for a in english] == ["security"]
    article = (await client.get("/api/v1/help/articles/security")).json()
    assert article["title"] == "Bảo mật tài khoản"
    assert "## Mất điện thoại?" in article["body"]
    assert (await client.get("/api/v1/help/articles/nope")).status_code == 404


# ---------------------------------------------------------------- tickets


@pytest.fixture
async def support_staff(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://viewallet.test", headers={"X-VW-CSRF": "1"}
    ) as http:
        await sign_in_staff(http, app, "support@example.com", "SUPPORT")
        yield http


async def test_ticket_conversation(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    support_staff: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    _, minh = await two_customers(client, other_client, app)
    sent = await send(client, minh["wallet_number"], 30_000)
    not_mine = await client.post(
        "/api/v1/support/tickets",
        json={
            "category": "TRANSACTION",
            "subject": "Chuyển nhầm người",
            "message": "Tôi chuyển nhầm số ví, nhờ hỗ trợ.",
            "transaction_reference": "VW261123AAAAAAAAAA",
        },
    )
    assert not_mine.status_code == 404
    opened = await client.post(
        "/api/v1/support/tickets",
        json={
            "category": "TRANSACTION",
            "subject": "Chuyển nhầm người",
            "message": "Tôi chuyển nhầm số ví, nhờ hỗ trợ.\x00",
            "transaction_reference": sent["reference"],
        },
    )
    assert opened.status_code == 201, opened.text
    ticket = opened.json()
    ref = ticket["reference"]
    assert ticket["status"] == "OPEN"
    assert ticket["messages"][0]["body"] == "Tôi chuyển nhầm số ví, nhờ hỗ trợ."
    assert (await other_client.get(f"/api/v1/support/tickets/{ref}")).status_code == 404
    assert (await client.get("/api/v1/admin/tickets")).status_code == 401

    queue = (await support_staff.get("/api/v1/admin/tickets", params={"status": "OPEN"})).json()
    assert [t["reference"] for t in queue["items"]] == [ref]
    await support_staff.post(
        f"/api/v1/admin/tickets/{ref}/messages",
        json={"body": "Đã gọi khách xác nhận", "internal": True},
    )
    replied = await support_staff.post(
        f"/api/v1/admin/tickets/{ref}/messages",
        json={"body": "Chúng tôi đang liên hệ người nhận."},
    )
    assert replied.json()["status"] == "PENDING_CUSTOMER"
    assert len(replied.json()["messages"]) == 3

    mine = (await client.get(f"/api/v1/support/tickets/{ref}")).json()
    assert [m["author"] for m in mine["messages"]] == ["CUSTOMER", "STAFF"]  # no internal note
    again = await client.post(
        f"/api/v1/support/tickets/{ref}/messages", json={"body": "Cảm ơn bạn."}
    )
    assert again.json()["status"] == "OPEN"
    closed = await support_staff.post(
        f"/api/v1/admin/tickets/{ref}/messages", json={"status": "CLOSED"}
    )
    assert closed.json()["status"] == "CLOSED"
    late = await client.post(f"/api/v1/support/tickets/{ref}/messages", json={"body": "Còn nữa"})
    assert late.json()["code"] == "TICKET_CLOSED"

    await drain(app)
    titles = [n["title"] for n in (await client.get("/api/v1/notifications")).json()["items"]]
    assert "Hỗ trợ đã trả lời" in titles
    assert len((await client.get("/api/v1/support/tickets")).json()) == 1


async def test_ticket_creation_is_rate_limited(client: httpx.AsyncClient, app: FastAPI) -> None:
    await customer(client, app)
    statuses = [
        (
            await client.post(
                "/api/v1/support/tickets",
                json={
                    "category": "OTHER",
                    "subject": f"Câu hỏi số {i}",
                    "message": "Nội dung câu hỏi thử nghiệm.",
                },
            )
        ).status_code
        for i in range(6)
    ]
    assert statuses == [201] * 5 + [429]
