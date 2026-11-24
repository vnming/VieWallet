"""Identity HTTP API: /api/v1/auth, /api/v1/me, /api/v1/admin/auth, /api/v1/admin/me."""

from __future__ import annotations

import uuid
from dataclasses import replace

from fastapi import APIRouter, Query, Response, status

from app.core.errors import ValidationFailedError
from app.modules.access.public import permissions_for_user, roles_for_user
from app.modules.identity import account, auth, mfa, pin
from app.modules.identity.http import (
    CustomerPrincipal,
    IdentityDep,
    MetaDep,
    PendingStaffSession,
    Principal,
    StaffPrincipal,
    clear_session_cookie,
    set_session_cookie,
)
from app.modules.identity.models import User
from app.modules.identity.schemas import (
    AcceptedResponse,
    ChangeEmailRequest,
    ChangePasswordRequest,
    ChangePinRequest,
    CodeRequest,
    ConfirmPinResetRequest,
    EmailOnlyRequest,
    LoginHistoryItem,
    LoginRequest,
    LogoutAllResponse,
    MeResponse,
    MfaConfirmResponse,
    MfaEnrollmentResponse,
    MfaVerifyRequest,
    PasswordOnlyRequest,
    PinStatusResponse,
    RegisterRequest,
    ResetPasswordRequest,
    SessionResponse,
    SetPinRequest,
    StaffLoginResponse,
    StaffMeResponse,
    UpdateProfileRequest,
    VerifyEmailRequest,
)

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
me_router = APIRouter(prefix="/api/v1/me", tags=["me"])
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin-auth"])


async def _me(ctx: IdentityDep, principal: Principal) -> MeResponse:
    async with ctx.db.transaction() as session:
        status_ = await pin.pin_status(session, ctx, principal.user_id)
    user = principal.user
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        display_name=user.display_name,
        locale=user.locale,  # type: ignore[arg-type]
        user_type=user.user_type,  # type: ignore[arg-type]
        email_verified=user.email_verified_at is not None,
        created_at=user.created_at,
        permissions=sorted(principal.permissions),
        pin=PinStatusResponse(
            is_set=status_.is_set,
            locked_until=status_.locked_until,
            requires_reset=status_.requires_reset,
        ),
        kyc_tier=user.kyc_tier,
        phone=user.phone,
        theme=user.theme,  # type: ignore[arg-type]
        privacy_mode=user.privacy_mode,
    )


# ----------------------------------------------------------------------------- /auth


@auth_router.post(
    "/register", status_code=status.HTTP_202_ACCEPTED, response_model=AcceptedResponse
)
async def register(body: RegisterRequest, ctx: IdentityDep, meta: MetaDep) -> AcceptedResponse:
    await auth.register(
        ctx,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        locale=body.locale,
        meta=meta,
    )
    return AcceptedResponse()


@auth_router.post("/verify-email", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(body: VerifyEmailRequest, ctx: IdentityDep, meta: MetaDep) -> None:
    await auth.verify_email(ctx, email=body.email, code=body.code, meta=meta)


@auth_router.post(
    "/verify-email/resend", status_code=status.HTTP_202_ACCEPTED, response_model=AcceptedResponse
)
async def resend_verification(
    body: EmailOnlyRequest, ctx: IdentityDep, meta: MetaDep
) -> AcceptedResponse:
    await auth.resend_verification(ctx, email=body.email, meta=meta)
    return AcceptedResponse()


@auth_router.post("/login", response_model=MeResponse)
async def login(
    body: LoginRequest, response: Response, ctx: IdentityDep, meta: MetaDep
) -> MeResponse:
    result = await auth.login(
        ctx, email=body.email, password=body.password, audience="WEB", meta=meta
    )
    set_session_cookie(response, ctx, result.issued)
    async with ctx.db.transaction() as session:
        permissions = await permissions_for_user(session, result.user.id)
    principal = Principal(
        user_id=result.user.id,
        session_id=result.issued.session.id,
        audience="WEB",
        email=result.user.email,
        full_name=result.user.full_name,
        user_type=result.user.user_type,
        locale=result.user.locale,
        permissions=permissions,
        mfa_verified_at=None,
        user=result.user,
        session=result.issued.session,
    )
    return await _me(ctx, principal)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    principal: CustomerPrincipal, response: Response, ctx: IdentityDep, meta: MetaDep
) -> None:
    await auth.logout(ctx, session_id=principal.session_id, user_id=principal.user_id, meta=meta)
    clear_session_cookie(response, ctx, "WEB")


@auth_router.post("/logout-all", response_model=LogoutAllResponse)
async def logout_all(
    principal: CustomerPrincipal, response: Response, ctx: IdentityDep, meta: MetaDep
) -> LogoutAllResponse:
    count = await auth.logout_all(ctx, user_id=principal.user_id, meta=meta)
    clear_session_cookie(response, ctx, "WEB")
    return LogoutAllResponse(revoked_sessions=count)


@auth_router.post(
    "/password/forgot", status_code=status.HTTP_202_ACCEPTED, response_model=AcceptedResponse
)
async def forgot_password(
    body: EmailOnlyRequest, ctx: IdentityDep, meta: MetaDep
) -> AcceptedResponse:
    await auth.forgot_password(ctx, email=body.email, meta=meta)
    return AcceptedResponse()


@auth_router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: ResetPasswordRequest, ctx: IdentityDep, meta: MetaDep) -> None:
    await auth.reset_password(ctx, token=body.token, new_password=body.new_password, meta=meta)


# ----------------------------------------------------------------------------- /me


@me_router.get("", response_model=MeResponse)
async def get_me(principal: CustomerPrincipal, ctx: IdentityDep) -> MeResponse:
    return await _me(ctx, principal)


@me_router.patch("", response_model=MeResponse)
async def update_me(
    body: UpdateProfileRequest, principal: CustomerPrincipal, ctx: IdentityDep, meta: MetaDep
) -> MeResponse:
    changes = account.ProfileChanges(**body.model_dump(exclude_unset=True))
    if changes.empty():
        raise ValidationFailedError("nothing to update")
    user = await account.update_profile(ctx, user_id=principal.user_id, changes=changes, meta=meta)
    return await _me(ctx, _with_user(principal, user))


def _with_user(principal: Principal, user: User) -> Principal:
    return replace(
        principal, user=user, email=user.email, full_name=user.full_name, locale=user.locale
    )


@me_router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest, principal: CustomerPrincipal, ctx: IdentityDep, meta: MetaDep
) -> None:
    await account.change_password(
        ctx,
        user_id=principal.user_id,
        current_session_id=principal.session_id,
        current_password=body.current_password,
        new_password=body.new_password,
        meta=meta,
    )


@me_router.post("/email", status_code=status.HTTP_202_ACCEPTED, response_model=AcceptedResponse)
async def change_email(
    body: ChangeEmailRequest, principal: CustomerPrincipal, ctx: IdentityDep, meta: MetaDep
) -> AcceptedResponse:
    await account.request_email_change(
        ctx, user_id=principal.user_id, password=body.password, new_email=body.new_email, meta=meta
    )
    return AcceptedResponse()


@me_router.post("/email/confirm", response_model=MeResponse)
async def confirm_email(
    body: CodeRequest, principal: CustomerPrincipal, ctx: IdentityDep, meta: MetaDep
) -> MeResponse:
    user = await account.confirm_email_change(
        ctx, user_id=principal.user_id, code=body.code, meta=meta
    )
    return await _me(ctx, _with_user(principal, user))


@me_router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(principal: CustomerPrincipal, ctx: IdentityDep) -> list[SessionResponse]:
    rows = await account.list_sessions(ctx, user_id=principal.user_id)
    return [
        SessionResponse(
            id=r.id,
            created_at=r.created_at,
            last_seen_at=r.last_seen_at,
            ip=r.ip,
            user_agent=r.user_agent,
            current=r.id == principal.session_id,
        )
        for r in rows
    ]


@me_router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: uuid.UUID,
    principal: CustomerPrincipal,
    response: Response,
    ctx: IdentityDep,
    meta: MetaDep,
) -> None:
    await account.revoke_session(ctx, user_id=principal.user_id, session_id=session_id, meta=meta)
    if session_id == principal.session_id:
        clear_session_cookie(response, ctx, "WEB")


@me_router.get("/login-history", response_model=list[LoginHistoryItem])
async def login_history(
    principal: CustomerPrincipal, ctx: IdentityDep, limit: int = Query(default=20, ge=1, le=50)
) -> list[LoginHistoryItem]:
    rows = await account.login_history(ctx, user_id=principal.user_id, limit=limit)
    return [
        LoginHistoryItem(at=r.at, result=r.result, ip=r.ip, user_agent=r.user_agent) for r in rows
    ]


@me_router.post("/pin", status_code=status.HTTP_204_NO_CONTENT)
async def set_pin(
    body: SetPinRequest, principal: CustomerPrincipal, ctx: IdentityDep, meta: MetaDep
) -> None:
    await pin.set_pin(
        ctx, user_id=principal.user_id, password=body.password, pin=body.pin, meta=meta
    )


@me_router.put("/pin", status_code=status.HTTP_204_NO_CONTENT)
async def change_pin(
    body: ChangePinRequest, principal: CustomerPrincipal, ctx: IdentityDep, meta: MetaDep
) -> None:
    await pin.change_pin(
        ctx,
        user_id=principal.user_id,
        current_pin=body.current_pin,
        new_pin=body.new_pin,
        meta=meta,
    )


@me_router.post(
    "/pin/reset/request", status_code=status.HTTP_202_ACCEPTED, response_model=AcceptedResponse
)
async def request_pin_reset(
    body: PasswordOnlyRequest, principal: CustomerPrincipal, ctx: IdentityDep, meta: MetaDep
) -> AcceptedResponse:
    await pin.request_pin_reset(ctx, user_id=principal.user_id, password=body.password, meta=meta)
    return AcceptedResponse()


@me_router.post("/pin/reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_pin_reset(
    body: ConfirmPinResetRequest, principal: CustomerPrincipal, ctx: IdentityDep, meta: MetaDep
) -> None:
    await pin.confirm_pin_reset(
        ctx, user_id=principal.user_id, code=body.code, new_pin=body.new_pin, meta=meta
    )


# ----------------------------------------------------------------------------- /admin


@admin_router.post("/auth/login", response_model=StaffLoginResponse)
async def staff_login(
    body: LoginRequest, response: Response, ctx: IdentityDep, meta: MetaDep
) -> StaffLoginResponse:
    result = await auth.login(
        ctx, email=body.email, password=body.password, audience="ADMIN", meta=meta
    )
    set_session_cookie(response, ctx, result.issued)
    return StaffLoginResponse(next_step=result.outcome.value)  # type: ignore[arg-type]


@admin_router.post("/auth/mfa/enroll", response_model=MfaEnrollmentResponse)
async def mfa_enroll(pending: PendingStaffSession, ctx: IdentityDep) -> MfaEnrollmentResponse:
    enrollment = await mfa.begin_enrollment(ctx, pending=pending.session, user=pending.user)
    return MfaEnrollmentResponse(secret=enrollment.secret, otpauth_uri=enrollment.otpauth_uri)


@admin_router.post("/auth/mfa/confirm", response_model=MfaConfirmResponse)
async def mfa_confirm(
    body: CodeRequest,
    pending: PendingStaffSession,
    response: Response,
    ctx: IdentityDep,
    meta: MetaDep,
) -> MfaConfirmResponse:
    confirmed = await mfa.confirm_enrollment(
        ctx, pending=pending.session, user=pending.user, code=body.code, meta=meta
    )
    set_session_cookie(response, ctx, confirmed.issued)
    return MfaConfirmResponse(recovery_codes=confirmed.recovery_codes)


@admin_router.post("/auth/mfa/verify", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_verify(
    body: MfaVerifyRequest,
    pending: PendingStaffSession,
    response: Response,
    ctx: IdentityDep,
    meta: MetaDep,
) -> None:
    if (body.code is None) == (body.recovery_code is None):
        raise ValidationFailedError("send exactly one of code or recovery_code")
    issued = await mfa.verify_login(
        ctx,
        pending=pending.session,
        user=pending.user,
        code=body.code,
        recovery_code=body.recovery_code,
        meta=meta,
    )
    set_session_cookie(response, ctx, issued)


@admin_router.post("/auth/step-up", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_step_up(
    body: CodeRequest, principal: StaffPrincipal, ctx: IdentityDep, meta: MetaDep
) -> None:
    await mfa.step_up(ctx, active=principal.session, user=principal.user, code=body.code, meta=meta)


@admin_router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def staff_logout(
    principal: StaffPrincipal, response: Response, ctx: IdentityDep, meta: MetaDep
) -> None:
    await auth.logout(ctx, session_id=principal.session_id, user_id=principal.user_id, meta=meta)
    clear_session_cookie(response, ctx, "ADMIN")


@admin_router.get("/me", response_model=StaffMeResponse)
async def staff_me(principal: StaffPrincipal, ctx: IdentityDep) -> StaffMeResponse:
    async with ctx.db.transaction() as session:
        roles = await roles_for_user(session, principal.user_id)
    return StaffMeResponse(
        id=principal.user_id,
        email=principal.email,
        full_name=principal.full_name,
        locale=principal.locale,  # type: ignore[arg-type]
        roles=sorted(roles),
        permissions=sorted(principal.permissions),
        mfa_verified_at=principal.mfa_verified_at,
        session_expires_at=min(
            principal.session.idle_expires_at, principal.session.absolute_expires_at
        ),
    )
