"""HTTP glue: session cookies and authentication/authorization dependencies.

Other modules protect their routes with ``CustomerPrincipal``, ``StaffPrincipal`` or
``require_permission(...)`` from ``identity.public``. The frontend only hides UI; these
dependencies are the authority (security.md §4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Request, Response

from app.core.errors import AuthRequiredError, ForbiddenError
from app.modules.access.public import SENSITIVE_PERMISSIONS, permissions_for_user
from app.modules.identity import sessions
from app.modules.identity.context import STEP_UP_WINDOW, IdentityServices, RequestMeta
from app.modules.identity.errors import (
    MfaRequiredError,
    MfaStepUpRequiredError,
    SessionExpiredError,
)
from app.modules.identity.models import User, UserSession

WEB_COOKIE = "vw_session"
ADMIN_COOKIE = "vw_admin_session"
COOKIE_PATH = "/api"


def cookie_name(audience: str) -> str:
    return ADMIN_COOKIE if audience == "ADMIN" else WEB_COOKIE


def get_identity(request: Request) -> IdentityServices:
    services: IdentityServices = request.app.state.identity
    return services


IdentityDep = Annotated[IdentityServices, Depends(get_identity)]


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


MetaDep = Annotated[RequestMeta, Depends(request_meta)]


def set_session_cookie(
    response: Response, ctx: IdentityServices, issued: sessions.IssuedSession
) -> None:
    row = issued.session
    max_age = int((row.absolute_expires_at - row.created_at).total_seconds())
    response.set_cookie(
        cookie_name(row.audience),
        issued.token,
        max_age=max_age,
        path=COOKIE_PATH,
        secure=ctx.settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response, ctx: IdentityServices, audience: str) -> None:
    response.delete_cookie(
        cookie_name(audience),
        path=COOKIE_PATH,
        secure=ctx.settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    session_id: uuid.UUID
    audience: str
    email: str
    full_name: str
    user_type: str
    locale: str
    permissions: frozenset[str]
    mfa_verified_at: datetime | None
    user: User
    session: UserSession


async def _resolve(
    request: Request, ctx: IdentityServices, audience: str
) -> sessions.ResolvedSession:
    token = request.cookies.get(cookie_name(audience))
    if not token:
        raise AuthRequiredError()
    async with ctx.db.transaction() as db_session:
        resolved = await sessions.resolve(db_session, ctx, token=token, audience=audience)
    if resolved is None:
        raise SessionExpiredError()
    return resolved


async def _principal(request: Request, ctx: IdentityServices, audience: str) -> Principal:
    resolved = await _resolve(request, ctx, audience)
    if resolved.session.state != "ACTIVE":
        raise MfaRequiredError()
    async with ctx.db.transaction() as db_session:
        permissions = await permissions_for_user(db_session, resolved.user.id)
    user = resolved.user
    return Principal(
        user_id=user.id,
        session_id=resolved.session.id,
        audience=audience,
        email=user.email,
        full_name=user.full_name,
        user_type=user.user_type,
        locale=user.locale,
        permissions=permissions,
        mfa_verified_at=resolved.session.mfa_verified_at,
        user=user,
        session=resolved.session,
    )


async def customer_principal(request: Request, ctx: IdentityDep) -> Principal:
    return await _principal(request, ctx, "WEB")


async def staff_principal(request: Request, ctx: IdentityDep) -> Principal:
    return await _principal(request, ctx, "ADMIN")


async def pending_staff_session(request: Request, ctx: IdentityDep) -> sessions.ResolvedSession:
    """An admin session that still needs MFA (enrollment or verification)."""
    resolved = await _resolve(request, ctx, "ADMIN")
    if resolved.session.state != "MFA_PENDING":
        raise ForbiddenError("this session has already completed MFA")
    return resolved


CustomerPrincipal = Annotated[Principal, Depends(customer_principal)]
StaffPrincipal = Annotated[Principal, Depends(staff_principal)]
PendingStaffSession = Annotated[sessions.ResolvedSession, Depends(pending_staff_session)]


def ensure_permission(principal: Principal, code: str, now: datetime) -> None:
    """Check a permission chosen at run time (e.g. depends on the approval's action type)."""
    if code not in principal.permissions:
        raise ForbiddenError()
    if code in SENSITIVE_PERMISSIONS:
        verified = principal.mfa_verified_at
        if verified is None or now - verified > STEP_UP_WINDOW:
            raise MfaStepUpRequiredError()


def require_permission(code: str):  # type: ignore[no-untyped-def]
    """Dependency factory: staff principal holding ``code`` (fresh MFA for sensitive ones)."""

    async def dependency(principal: StaffPrincipal, ctx: IdentityDep) -> Principal:
        ensure_permission(principal, code, ctx.clock.now())
        return principal

    return Depends(dependency)
