"""Customer registration, verification, sign-in, sessions and password management (API level)."""

from __future__ import annotations

import contextlib
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.core.clock import FixedClock
from app.core.outbox import OutboxEvent
from app.modules.identity import auth
from app.modules.identity.context import RequestMeta
from app.modules.identity.errors import AccountLockedError, InvalidCredentialsError
from app.modules.identity.models import Credential, LoginAttempt, User
from tests.integration.identity_helpers import (
    NEW_PASSWORD,
    PASSWORD,
    code_in,
    deliver_emails,
    last_to,
    link_token_in,
    login,
    register_and_verify,
    services,
    signed_in_customer,
)


async def test_full_sign_up_and_sign_in_flow(client: httpx.AsyncClient, app: FastAPI) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "Lan@Example.com", "password": PASSWORD, "full_name": "  Trần   Thị Lan "},
    )
    assert response.status_code == 202
    email = last_to(await deliver_emails(app), "lan@example.com")
    assert "Mã xác minh" in email.subject

    # Not verified yet: correct password, but sign-in is refused.
    response = await login(client)
    assert response.status_code == 403
    assert response.json()["code"] == "EMAIL_NOT_VERIFIED"

    wrong = await client.post(
        "/api/v1/auth/verify-email", json={"email": "lan@example.com", "code": "000000"}
    )
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "VERIFICATION_CODE_INVALID"
    ok = await client.post(
        "/api/v1/auth/verify-email", json={"email": "lan@example.com", "code": code_in(email)}
    )
    assert ok.status_code == 204

    response = await login(client, email="LAN@example.com")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "lan@example.com"
    assert body["full_name"] == "Trần Thị Lan"
    assert body["pin"] == {"is_set": False, "locked_until": None, "requires_reset": False}
    assert "wallet:transact:own" in body["permissions"]
    cookie = response.headers["set-cookie"]
    for attribute in ("vw_session=", "HttpOnly", "Path=/api", "SameSite=strict"):
        assert attribute.lower() in cookie.lower()

    me = await client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["id"] == body["id"]

    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    after = await client.get("/api/v1/me")
    assert after.status_code == 401


async def test_register_existing_email_looks_identical(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await register_and_verify(client, app)
    again = await client.post(
        "/api/v1/auth/register",
        json={"email": "lan@example.com", "password": NEW_PASSWORD, "full_name": "Kẻ Giả Mạo"},
    )
    assert again.status_code == 202
    assert again.json() == {"status": "accepted"}
    notice = last_to(await deliver_emails(app), "lan@example.com")
    assert "đăng ký bằng email của bạn" in notice.subject
    async with services(app).db.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1


async def test_password_policy_errors_are_field_level(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "lan@example.com", "password": "password123", "full_name": "Lan"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "PASSWORD_POLICY"
    assert {e["code"] for e in body["errors"]} == {"TOO_COMMON"}
    assert {e["field"] for e in body["errors"]} == {"password"}


async def test_verification_secret_is_encrypted_in_the_outbox(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "lan@example.com", "password": PASSWORD, "full_name": "Lan"},
    )
    async with services(app).db.transaction() as session:
        event = await session.scalar(select(OutboxEvent))
    assert event is not None
    assert "secret" in event.payload
    code = code_in(last_to(await deliver_emails(app), "lan@example.com"))
    assert code not in str(event.payload)


async def test_verification_code_is_burned_after_five_wrong_guesses(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "lan@example.com", "password": PASSWORD, "full_name": "Lan"},
    )
    code = code_in(last_to(await deliver_emails(app), "lan@example.com"))
    wrong = "111111" if code != "111111" else "222222"
    for _ in range(5):
        r = await client.post(
            "/api/v1/auth/verify-email", json={"email": "lan@example.com", "code": wrong}
        )
        assert r.status_code == 422
    r = await client.post(
        "/api/v1/auth/verify-email", json={"email": "lan@example.com", "code": code}
    )
    assert r.status_code == 422  # the real code no longer works


async def test_unknown_email_and_wrong_password_look_the_same(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await register_and_verify(client, app)
    unknown = await login(client, email="nobody@example.com")
    wrong = await login(client, password="sai-mat-khau-hoan-toan")
    assert unknown.status_code == wrong.status_code == 401

    def strip(r: httpx.Response) -> dict[str, object]:
        return {k: v for k, v in r.json().items() if k != "correlation_id"}

    assert strip(unknown) == strip(wrong)
    async with services(app).db.transaction() as session:
        results = (await session.scalars(select(LoginAttempt.result))).all()
    assert "BAD_CREDENTIALS" in results


async def test_login_rate_limit_per_account_and_ip(client: httpx.AsyncClient, app: FastAPI) -> None:
    await register_and_verify(client, app)
    statuses = [(await login(client, password=f"wrong-password-{i}")).status_code for i in range(6)]
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
    limited = await login(client)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0


async def test_account_lockout_after_ten_failures_from_many_ips(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    await register_and_verify(client, app)
    ctx = services(app)
    for i in range(10):
        meta = RequestMeta(ip=f"10.0.0.{i}")
        with contextlib.suppress(InvalidCredentialsError):
            await auth.login(
                ctx, email="lan@example.com", password="sai", audience="WEB", meta=meta
            )
    with pytest.raises(AccountLockedError) as exc_info:
        await auth.login(
            ctx,
            email="lan@example.com",
            password=PASSWORD,
            audience="WEB",
            meta=RequestMeta(ip="10.0.1.1"),
        )
    assert int(exc_info.value.headers["Retry-After"]) == 15 * 60
    assert "bị tạm khóa" in last_to(await deliver_emails(app), "lan@example.com").subject

    clock.advance(timedelta(minutes=16))
    result = await auth.login(
        ctx,
        email="lan@example.com",
        password=PASSWORD,
        audience="WEB",
        meta=RequestMeta(ip="10.0.1.2"),
    )
    assert result.user.email == "lan@example.com"
    async with ctx.db.transaction() as session:
        credential = await session.scalar(select(Credential))
    assert credential is not None
    assert credential.failed_attempts == 0
    assert credential.lock_count == 1


async def test_session_idle_and_absolute_expiry(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    await signed_in_customer(client, app)
    clock.advance(timedelta(days=6))
    assert (await client.get("/api/v1/me")).status_code == 200  # activity slides the idle window
    clock.advance(timedelta(days=6))
    assert (await client.get("/api/v1/me")).status_code == 200
    clock.advance(timedelta(days=8))  # idle > 7 days
    expired = await client.get("/api/v1/me")
    assert expired.status_code == 401
    assert expired.json()["code"] == "SESSION_EXPIRED"


async def test_absolute_lifetime_cannot_be_extended(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    await signed_in_customer(client, app)
    for _ in range(6):
        clock.advance(timedelta(days=5))
        await client.get("/api/v1/me")
    assert (await client.get("/api/v1/me")).status_code == 401  # 30 days absolute


async def test_tampered_or_missing_cookie(client: httpx.AsyncClient, app: FastAPI) -> None:
    assert (await client.get("/api/v1/me")).json()["code"] == "AUTH_REQUIRED"
    client.cookies.set("vw_session", "forged-token", path="/api")
    assert (await client.get("/api/v1/me")).json()["code"] == "SESSION_EXPIRED"


async def test_logout_all_and_session_list(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await signed_in_customer(client, app)
    assert (await login(other_client)).status_code == 200
    sessions = (await client.get("/api/v1/me/sessions")).json()
    assert len(sessions) == 2
    assert sum(s["current"] for s in sessions) == 1

    result = await other_client.post("/api/v1/auth/logout-all")
    assert result.json() == {"revoked_sessions": 2}
    assert (await client.get("/api/v1/me")).status_code == 401
    assert (await other_client.get("/api/v1/me")).status_code == 401


async def test_revoke_one_session_and_cannot_touch_others(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await signed_in_customer(client, app)
    await signed_in_customer(other_client, app, email="minh@example.com")
    foreign = (await other_client.get("/api/v1/me/sessions")).json()[0]["id"]
    response = await client.delete(f"/api/v1/me/sessions/{foreign}")
    assert response.status_code == 404  # someone else's session: not found, not forbidden
    assert (await other_client.get("/api/v1/me")).status_code == 200


async def test_password_reset_flow(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await signed_in_customer(client, app)
    assert (
        await client.post("/api/v1/auth/password/forgot", json={"email": "lan@example.com"})
    ).status_code == 202
    assert (
        await client.post("/api/v1/auth/password/forgot", json={"email": "ghost@example.com"})
    ).status_code == 202
    emails = await deliver_emails(app)
    assert not [e for e in emails if e.to == "ghost@example.com"]
    token = link_token_in(last_to(emails, "lan@example.com"))
    assert "http://127.0.0.1:8080/reset-password?token=" in last_to(emails, "lan@example.com").body

    weak = await other_client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": "12345"}
    )
    assert weak.status_code == 422
    ok = await other_client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD}
    )
    assert ok.status_code == 204  # the failed attempt above did not consume the token
    reused = await other_client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD}
    )
    assert reused.json()["code"] == "TOKEN_INVALID"

    assert (await client.get("/api/v1/me")).status_code == 401  # all sessions revoked
    assert (await login(other_client)).status_code == 401
    assert (await login(other_client, password=NEW_PASSWORD)).status_code == 200


async def test_reset_link_expires(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    await register_and_verify(client, app)
    await client.post("/api/v1/auth/password/forgot", json={"email": "lan@example.com"})
    token = link_token_in(last_to(await deliver_emails(app), "lan@example.com"))
    clock.advance(timedelta(minutes=31))
    r = await client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD}
    )
    assert r.json()["code"] == "TOKEN_INVALID"


async def test_change_password_keeps_current_session_only(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await signed_in_customer(client, app)
    await login(other_client)
    bad = await client.post(
        "/api/v1/me/password", json={"current_password": "sai", "new_password": NEW_PASSWORD}
    )
    assert bad.json()["code"] == "PASSWORD_INCORRECT"
    ok = await client.post(
        "/api/v1/me/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert ok.status_code == 204
    assert (await client.get("/api/v1/me")).status_code == 200
    assert (await other_client.get("/api/v1/me")).status_code == 401
    assert "đã được thay đổi" in last_to(await deliver_emails(app), "lan@example.com").subject


async def test_change_email_flow(client: httpx.AsyncClient, app: FastAPI) -> None:
    await signed_in_customer(client, app)
    wrong = await client.post(
        "/api/v1/me/email", json={"password": "sai", "new_email": "lan.new@example.com"}
    )
    assert wrong.json()["code"] == "PASSWORD_INCORRECT"
    await client.post(
        "/api/v1/me/email", json={"password": PASSWORD, "new_email": "lan.new@example.com"}
    )
    emails = await deliver_emails(app)
    code = code_in(last_to(emails, "lan.new@example.com"))
    confirmed = await client.post("/api/v1/me/email/confirm", json={"code": code})
    assert confirmed.status_code == 200
    assert confirmed.json()["email"] == "lan.new@example.com"
    notice = last_to(await deliver_emails(app), "lan@example.com")
    assert "lan.new@example.com" in notice.body
    await client.post("/api/v1/auth/logout")
    assert (await login(client, email="lan.new@example.com")).status_code == 200


async def test_change_email_to_taken_address_sends_nothing(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await signed_in_customer(other_client, app, email="minh@example.com")
    await signed_in_customer(client, app)
    r = await client.post(
        "/api/v1/me/email", json={"password": PASSWORD, "new_email": "minh@example.com"}
    )
    assert r.status_code == 202
    emails = await deliver_emails(app)
    assert not [e for e in emails if e.to == "minh@example.com" and "email mới" in e.subject]


async def test_profile_update_and_login_history(client: httpx.AsyncClient, app: FastAPI) -> None:
    await signed_in_customer(client, app)
    r = await client.patch("/api/v1/me", json={"display_name": "Lan", "locale": "en"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "Lan"
    assert r.json()["locale"] == "en"
    empty = await client.patch("/api/v1/me", json={})
    assert empty.status_code == 422
    history = (await client.get("/api/v1/me/login-history")).json()
    assert history[0]["result"] == "SUCCESS"
    assert {"at", "result", "ip", "user_agent"} <= set(history[0])


async def test_resend_verification_is_rate_limited(client: httpx.AsyncClient) -> None:
    statuses = [
        (
            await client.post("/api/v1/auth/verify-email/resend", json={"email": "x@example.com"})
        ).status_code
        for _ in range(4)
    ]
    assert statuses == [202, 202, 202, 429]


async def test_mutations_require_csrf_header(app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://viewallet.test"
    ) as raw:
        r = await raw.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "x"})
        assert r.status_code == 403
        assert r.json()["code"] == "CSRF_FAILED"
        r = await raw.post(
            "/api/v1/auth/login",
            json={"email": "a@example.com", "password": "x"},
            headers={"X-VW-CSRF": "1", "Origin": "https://evil.example"},
        )
        assert r.status_code == 403
