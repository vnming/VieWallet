"""KYC lifecycle (security.md §9): draft, documents, submit, claim, reject, resubmit, approve,
signed document links, duplicates, limits on attempts, expiry."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select

from app.core.clock import FixedClock
from app.modules.audit.public import AuditLog
from app.modules.identity.public import kyc_tier_of, user_id_by_email
from app.modules.kyc.public import expire_approvals, profile_of
from tests.integration.finance_helpers import sign_in_staff
from tests.integration.money_helpers import customer, drain, finance, marker
from tests.integration.spend_helpers import OTHER

DRAFT = {
    "full_name": "Trần Thị Lan",
    "date_of_birth": "1995-04-12",
    "document_type": "NATIONAL_ID",
    "document_number": "079195001234",
    "address": "12 Đường Hư Cấu, Quận Mô Phỏng",
}


def png(color: tuple[int, int, int] = (20, 120, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(out, format="PNG")
    return out.getvalue()


async def upload(client: httpx.AsyncClient, kind: str, data: bytes | None = None) -> httpx.Response:
    return await client.put(
        f"/api/v1/kyc/draft/documents/{kind}",
        content=data if data is not None else png(),
        headers={"Content-Type": "image/png"},
    )


async def ready_submission(client: httpx.AsyncClient, draft: dict[str, str] | None = None) -> None:
    r = await client.put("/api/v1/kyc/draft", json=draft or DRAFT)
    assert r.status_code == 200, r.text
    for kind in ("FRONT", "BACK", "SELFIE"):
        assert (await upload(client, kind)).status_code == 201
    r = await client.post("/api/v1/kyc/submit")
    assert r.status_code == 200, r.text


@pytest.fixture
async def reviewer(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://viewallet.test", headers={"X-VW-CSRF": "1"}
    ) as http:
        await sign_in_staff(http, app, "kyc@example.com", "KYC_REVIEWER")
        yield http


async def queue(reviewer: httpx.AsyncClient) -> list[dict[str, Any]]:
    r = await reviewer.get("/api/v1/admin/kyc/submissions")
    assert r.status_code == 200, r.text
    items: list[dict[str, Any]] = r.json()["items"]
    return items


async def decide(
    reviewer: httpx.AsyncClient, submission_id: str, decision: str, reason: str | None = None
) -> httpx.Response:
    return await reviewer.post(
        f"/api/v1/admin/kyc/submissions/{submission_id}/decision",
        json={"decision": decision, "reason_code": reason, "note": "kiểm tra mô phỏng"},
    )


async def test_full_lifecycle_reject_then_approve(  # noqa: PLR0915 - one journey
    client: httpx.AsyncClient, reviewer: httpx.AsyncClient, app: FastAPI
) -> None:
    await customer(client, app)
    status = (await client.get("/api/v1/kyc")).json()
    assert (status["status"], status["tier"], status["submission"]) == ("NOT_STARTED", 0, None)

    # Validation, then a draft, documents and a premature submit.
    young = await client.put("/api/v1/kyc/draft", json={**DRAFT, "date_of_birth": "2015-01-01"})
    assert young.status_code == 422
    assert (await client.put("/api/v1/kyc/draft", json=DRAFT)).status_code == 200
    assert (await upload(client, "FRONT")).status_code == 201
    early = await client.post("/api/v1/kyc/submit")
    assert early.json()["code"] == "KYC_DOCUMENTS_MISSING"
    assert (await upload(client, "BACK")).status_code == 201
    assert (await upload(client, "SELFIE", png((0, 0, 0)))).status_code == 201
    submitted = (await client.post("/api/v1/kyc/submit")).json()
    assert submitted["status"] == "PENDING"
    assert submitted["submission"]["document_number_masked"] == "••••••••1234"
    assert {d["kind"] for d in submitted["submission"]["documents"]} == {"FRONT", "BACK", "SELFIE"}
    # Nothing can change while waiting for review.
    assert (await client.put("/api/v1/kyc/draft", json=DRAFT)).json()["code"] == "KYC_IN_REVIEW"
    assert (await upload(client, "FRONT")).json()["code"] == "KYC_IN_REVIEW"
    # Customers never reach the reviewer API.
    assert (await client.get("/api/v1/admin/kyc/submissions")).status_code == 401

    [item] = await queue(reviewer)
    sid = item["id"]
    detail = (await reviewer.get(f"/api/v1/admin/kyc/submissions/{sid}")).json()
    assert detail["can_decide"] is False  # claim first
    assert (await decide(reviewer, sid, "APPROVE")).json()["code"] == "KYC_INVALID_STATE"
    claimed = (await reviewer.post(f"/api/v1/admin/kyc/submissions/{sid}/claim")).json()
    assert (claimed["status"], claimed["can_decide"]) == ("IN_REVIEW", True)
    assert (await client.get("/api/v1/kyc")).json()["status"] == "IN_REVIEW"

    # Documents: signed, viewer-bound, short-lived, audited.
    since = marker()
    url = claimed["documents"][0]["url"]
    image = await reviewer.get(url)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "no-store"
    assert image.content.startswith(b"\x89PNG")
    tampered = url[:-1] + ("0" if url[-1] != "0" else "1")
    assert (await reviewer.get(tampered)).status_code == 403
    clock: FixedClock = finance(app).clock  # type: ignore[assignment]
    clock.advance(timedelta(minutes=6))
    assert (await reviewer.get(url)).json()["code"] == "KYC_LINK_EXPIRED"
    async with finance(app).db.transaction() as session:
        viewed = (
            await session.scalars(
                select(AuditLog.action).where(
                    AuditLog.id > since, AuditLog.action == "kyc.document_viewed"
                )
            )
        ).all()
    assert len(viewed) == 1

    no_reason = await decide(reviewer, sid, "REJECT")
    assert no_reason.status_code == 422
    rejected = await decide(reviewer, sid, "REJECT", "BLURRY_IMAGE")
    assert rejected.json()["status"] == "REJECTED"
    mine = (await client.get("/api/v1/kyc")).json()
    assert (mine["status"], mine["submission"]["reject_reason"]) == ("REJECTED", "BLURRY_IMAGE")

    # Second attempt, approved: tier 1 everywhere.
    await ready_submission(client)
    [again] = await queue(reviewer)
    await reviewer.post(f"/api/v1/admin/kyc/submissions/{again['id']}/claim")
    approved = await decide(reviewer, again["id"], "APPROVE")
    assert approved.json()["status"] == "APPROVED"
    status = (await client.get("/api/v1/kyc")).json()
    assert (status["status"], status["tier"]) == ("APPROVED", 1)
    assert (await client.get("/api/v1/me")).json()["kyc_tier"] == 1
    assert (await client.put("/api/v1/kyc/draft", json=DRAFT)).json()["code"] == (
        "KYC_ALREADY_VERIFIED"
    )
    limits = (await client.get("/api/v1/wallet/limits")).json()
    withdrawal = next(t for t in limits["types"] if t["type"] == "WITHDRAWAL")
    assert (limits["kyc_tier"], withdrawal["allowed"]) == (1, True)

    await drain(app)
    titles = [n["title"] for n in (await client.get("/api/v1/notifications")).json()["items"]]
    assert titles[:3] == [
        "Xác minh danh tính thành công",
        "Đã nhận hồ sơ xác minh",
        "Hồ sơ xác minh chưa được duyệt",
    ]


async def test_two_reviewers_and_bad_files(
    client: httpx.AsyncClient, reviewer: httpx.AsyncClient, app: FastAPI
) -> None:
    await customer(client, app)
    assert (await upload(client, "FRONT")).json()["code"] == "KYC_NOT_FOUND"  # no draft yet
    await client.put("/api/v1/kyc/draft", json=DRAFT)
    fake = await upload(client, "FRONT", b"\x89PNG\r\n\x1a\nnot an image at all")
    assert fake.json()["code"] == "KYC_FILE_INVALID"
    huge = await upload(client, "FRONT", b"\x00" * (5 * 1024 * 1024 + 1))
    assert huge.json()["code"] == "KYC_FILE_TOO_LARGE"
    for kind in ("FRONT", "BACK", "SELFIE"):
        await upload(client, kind)
    await client.post("/api/v1/kyc/submit")
    [item] = await queue(reviewer)
    await reviewer.post(f"/api/v1/admin/kyc/submissions/{item['id']}/claim")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://viewallet.test", headers={"X-VW-CSRF": "1"}
    ) as second:
        await sign_in_staff(second, app, "kyc2@example.com", "KYC_REVIEWER")
        taken = await second.post(f"/api/v1/admin/kyc/submissions/{item['id']}/claim")
        assert taken.json()["code"] == "KYC_CLAIMED_BY_OTHER"
        assert (await decide(second, item["id"], "APPROVE")).json()["code"] == (
            "KYC_CLAIMED_BY_OTHER"
        )
        # A link issued to the first reviewer does not open for the second.
        detail = (await reviewer.get(f"/api/v1/admin/kyc/submissions/{item['id']}")).json()
        assert (await second.get(detail["documents"][0]["url"])).status_code == 403


async def test_duplicate_documents_attempt_limit_and_expiry(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    reviewer: httpx.AsyncClient,
    app: FastAPI,
    clock: FixedClock,
) -> None:
    await customer(client, app)
    await customer(other_client, app, email=OTHER)
    await ready_submission(client)
    await ready_submission(other_client, {**DRAFT, "full_name": "Lê Văn Bình"})
    items = await queue(reviewer)
    minh_item = next(i for i in items if i["full_name"] == "Lê Văn Bình")
    detail = (await reviewer.get(f"/api/v1/admin/kyc/submissions/{minh_item['id']}")).json()
    assert detail["duplicate_document_accounts"] == 1

    # Lan: rejected again and again; the sixth submission within 30 days is refused.
    lan_id = next(i for i in items if i["full_name"] == DRAFT["full_name"])["id"]
    for attempt in range(5):
        await reviewer.post(f"/api/v1/admin/kyc/submissions/{lan_id}/claim")
        await decide(reviewer, lan_id, "REJECT", "MISMATCH")
        await client.put("/api/v1/kyc/draft", json=DRAFT)
        for kind in ("FRONT", "BACK", "SELFIE"):
            await upload(client, kind)
        r = await client.post("/api/v1/kyc/submit")
        if attempt < 4:
            assert r.status_code == 200, r.text
            lan_id = r.json()["submission"]["id"]
        else:
            assert r.status_code == 429
            assert r.json()["code"] == "KYC_TOO_MANY_ATTEMPTS"
            break

    # Minh approved, then the approval expires after two years.
    await reviewer.post(f"/api/v1/admin/kyc/submissions/{minh_item['id']}/claim")
    await decide(reviewer, minh_item["id"], "APPROVE")
    assert (await other_client.get("/api/v1/me")).json()["kyc_tier"] == 1
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        assert await expire_approvals(session, ctx.clock) == 0
    clock.advance(timedelta(days=731))
    async with ctx.db.transaction() as session:
        assert await expire_approvals(session, ctx.clock) == 1
    # Two years later the session is long gone: check the records directly.
    async with ctx.db.transaction() as session:
        user_id = await user_id_by_email(session, OTHER)
        profile = await profile_of(session, user_id)
        assert profile is not None
        assert (profile.status, profile.tier) == ("EXPIRED", 0)
        assert await kyc_tier_of(session, user_id) == 0
