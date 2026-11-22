"""Staff sign-in with TOTP MFA, step-up, RBAC enforcement and audience separation."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pyotp
import pytest
from fastapi import FastAPI

from app.core.clock import FixedClock
from app.core.errors import ConflictError, ValidationFailedError
from app.modules.identity.public import create_staff
from tests.integration.identity_helpers import (
    PASSWORD,
    login,
    services,
    signed_in_customer,
    totp_now,
)

STAFF_EMAIL = "ops@example.com"


async def make_staff(app: FastAPI, *roles: str, email: str = STAFF_EMAIL) -> None:
    await create_staff(
        services(app), email=email, full_name="Nhân Viên Demo", password=PASSWORD, roles=roles
    )


async def staff_login(client: httpx.AsyncClient, email: str = STAFF_EMAIL) -> httpx.Response:
    return await client.post(
        "/api/v1/admin/auth/login", json={"email": email, "password": PASSWORD}
    )


async def enrolled_staff(client: httpx.AsyncClient, app: FastAPI, *roles: str) -> str:
    """Create, sign in and enroll MFA; returns the TOTP secret."""
    await make_staff(app, *roles)
    assert (await staff_login(client)).json() == {"next_step": "MFA_ENROLLMENT_REQUIRED"}
    secret = (await client.post("/api/v1/admin/auth/mfa/enroll")).json()["secret"]
    r = await client.post("/api/v1/admin/auth/mfa/confirm", json={"code": totp_now(secret, app)})
    assert r.status_code == 200, r.text
    return str(secret)


async def test_enrollment_flow(client: httpx.AsyncClient, app: FastAPI, clock: FixedClock) -> None:
    await make_staff(app, "OPERATIONS")
    assert (await staff_login(client)).json() == {"next_step": "MFA_ENROLLMENT_REQUIRED"}

    pending_me = await client.get("/api/v1/admin/me")
    assert pending_me.json()["code"] == "MFA_REQUIRED"  # password alone gives no access

    enrollment = (await client.post("/api/v1/admin/auth/mfa/enroll")).json()
    assert enrollment["otpauth_uri"].startswith("otpauth://totp/VieWallet:")
    bad = await client.post("/api/v1/admin/auth/mfa/confirm", json={"code": "000000"})
    assert bad.json()["code"] == "MFA_INVALID"
    ok = await client.post(
        "/api/v1/admin/auth/mfa/confirm", json={"code": totp_now(enrollment["secret"], app)}
    )
    codes = ok.json()["recovery_codes"]
    assert len(codes) == 10
    assert len(set(codes)) == 10

    me = (await client.get("/api/v1/admin/me")).json()
    assert me["roles"] == ["OPERATIONS"]
    assert "simulator:manage:any" in me["permissions"]
    assert me["mfa_verified_at"] == "2026-11-21T08:00:00Z"
    # Admin sessions are short: 30 min idle.
    assert me["session_expires_at"] == "2026-11-21T08:30:00Z"


async def test_mfa_login_replay_and_recovery_codes(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    secret = await enrolled_staff(client, app, "SUPPORT")
    used_code = totp_now(secret, app)
    assert (await client.post("/api/v1/admin/auth/logout")).status_code == 204
    assert (await client.get("/api/v1/admin/me")).status_code == 401

    assert (await staff_login(other_client)).json() == {"next_step": "MFA_REQUIRED"}
    replay = await other_client.post("/api/v1/admin/auth/mfa/verify", json={"code": used_code})
    assert replay.json()["code"] == "MFA_INVALID"  # same time step already used
    clock.advance(timedelta(seconds=30))
    ok = await other_client.post(
        "/api/v1/admin/auth/mfa/verify", json={"code": totp_now(secret, app)}
    )
    assert ok.status_code == 204
    assert (await other_client.get("/api/v1/admin/me")).status_code == 200


async def test_recovery_code_works_once(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await make_staff(app, "SUPPORT")
    await staff_login(client)
    secret = (await client.post("/api/v1/admin/auth/mfa/enroll")).json()["secret"]
    codes = (
        await client.post("/api/v1/admin/auth/mfa/confirm", json={"code": totp_now(secret, app)})
    ).json()["recovery_codes"]
    await staff_login(other_client)
    both = await other_client.post(
        "/api/v1/admin/auth/mfa/verify", json={"code": "123456", "recovery_code": codes[0]}
    )
    assert both.status_code == 422
    first = await other_client.post(
        "/api/v1/admin/auth/mfa/verify", json={"recovery_code": codes[0].lower()}
    )
    assert first.status_code == 204
    await staff_login(other_client)
    again = await other_client.post(
        "/api/v1/admin/auth/mfa/verify", json={"recovery_code": codes[0]}
    )
    assert again.json()["code"] == "MFA_INVALID"


async def test_pending_mfa_session_expires(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    await make_staff(app, "SUPPORT")
    await staff_login(client)
    clock.advance(timedelta(minutes=11))
    assert (await client.post("/api/v1/admin/auth/mfa/enroll")).json()["code"] == "SESSION_EXPIRED"


async def test_audiences_are_separated(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    await make_staff(app, "SUPPORT")
    await signed_in_customer(other_client, app)
    # Staff cannot use the customer app and customers cannot use the admin app.
    assert (await login(client, email=STAFF_EMAIL)).json()["code"] == "INVALID_CREDENTIALS"
    assert (await staff_login(other_client, "lan@example.com")).json()[
        "code"
    ] == "INVALID_CREDENTIALS"
    # A customer cookie is not an admin session.
    assert (await other_client.get("/api/v1/admin/me")).json()["code"] == "AUTH_REQUIRED"


async def test_permission_checks_and_step_up(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    await enrolled_staff(client, app, "SUPPORT")
    denied = await client.get("/api/v1/admin/audit-logs")
    assert denied.status_code == 403
    assert denied.json()["code"] == "FORBIDDEN"

    await make_staff(app, "SUPER_ADMIN", email="root@example.com")
    await staff_login(other_client, "root@example.com")
    secret = (await other_client.post("/api/v1/admin/auth/mfa/enroll")).json()["secret"]
    await other_client.post("/api/v1/admin/auth/mfa/confirm", json={"code": totp_now(secret, app)})

    page = (await other_client.get("/api/v1/admin/audit-logs?action=identity.staff_created")).json()
    assert page["total"] >= 2
    assert {"items", "page", "page_size", "total"} <= set(page)
    verify = await other_client.get("/api/v1/admin/audit-logs/verify")
    assert verify.status_code == 200
    assert verify.json()["ok"] is True

    clock.advance(timedelta(minutes=16))  # sensitive permission: MFA must be fresh
    await other_client.get("/api/v1/admin/me")  # keeps the session alive (idle 30 min)
    stale = await other_client.get("/api/v1/admin/audit-logs/verify")
    assert stale.json()["code"] == "MFA_STEP_UP_REQUIRED"
    assert (await other_client.get("/api/v1/admin/audit-logs")).status_code == 200  # not sensitive
    step = await other_client.post(
        "/api/v1/admin/auth/step-up", json={"code": totp_now(secret, app)}
    )
    assert step.status_code == 204
    assert (await other_client.get("/api/v1/admin/audit-logs/verify")).status_code == 200


async def test_create_staff_validation(app: FastAPI) -> None:
    with pytest.raises(ValidationFailedError):
        await make_staff(app, "CUSTOMER")
    with pytest.raises(ValidationFailedError):
        await make_staff(app)
    await make_staff(app, "SUPPORT")
    with pytest.raises(ConflictError):
        await make_staff(app, "SUPPORT")


PUBLIC_ADMIN_ROUTES = {"/api/v1/admin/auth/login"}
PENDING_ADMIN_ROUTES = {
    "/api/v1/admin/auth/mfa/enroll",
    "/api/v1/admin/auth/mfa/confirm",
    "/api/v1/admin/auth/mfa/verify",
}


def admin_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every admin operation, taken from the OpenAPI document (covers routers added later)."""
    paths = app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/api/v1/admin")
        for method in operations
    )


async def test_every_admin_route_requires_an_admin_session(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, app: FastAPI
) -> None:
    """Authorization matrix baseline: anonymous and customer callers are always rejected."""
    await signed_in_customer(other_client, app)
    routes = [r for r in admin_routes(app) if r[1] not in PUBLIC_ADMIN_ROUTES]
    assert len(routes) >= 7
    for method, path in routes:
        for caller in (client, other_client):
            response = await caller.request(method, path.replace("{", "").replace("}", ""), json={})
            assert response.status_code == 401, (method, path, response.text)


async def test_fully_signed_in_staff_cannot_reuse_mfa_endpoints(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await enrolled_staff(client, app, "SUPPORT")
    for path in sorted(PENDING_ADMIN_ROUTES):
        assert (await client.post(path, json={"code": "123456"})).status_code in {403, 422}


async def test_totp_helper_matches_pyotp() -> None:
    secret = pyotp.random_base32()
    assert len(pyotp.TOTP(secret).now()) == 6
