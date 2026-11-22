"""Registration, e-mail verification, sign-in/out and password reset.

Failure counters (wrong password, wrong code) are committed before the error is raised:
raising inside the transaction would roll the counter back and defeat the lockout.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import sha256_hex
from app.modules.access.public import assign_role
from app.modules.identity import sessions
from app.modules.identity.context import (
    ACCOUNT_LOCK_BASE,
    ACCOUNT_LOCK_MAX,
    ACCOUNT_LOCK_THRESHOLD,
    CODE_CHECK_PER_IP,
    EMAIL_CODE_TTL,
    EMAIL_SEND_PER_ADDRESS,
    EMAIL_SEND_PER_IP,
    LOGIN_PER_ACCOUNT_IP,
    LOGIN_PER_IP,
    REGISTER_PER_IP,
    RESET_LINK_TTL,
    IdentityServices,
    RequestMeta,
)
from app.modules.identity.domain.password_policy import check_password
from app.modules.identity.errors import (
    AccountDisabledError,
    AccountLockedError,
    EmailNotVerifiedError,
    InvalidCredentialsError,
    PasswordPolicyError,
    VerificationCodeInvalidError,
)
from app.modules.identity.models import Credential, LoginAttempt, MfaFactor, User
from app.modules.identity.sessions import IssuedSession
from app.modules.identity.tokens import consume_code, consume_link, issue_code, issue_link
from app.modules.notifications.public import request_email


def normalize_email(email: str) -> str:
    return email.strip().lower()


def ensure_password_policy(password: str, *, email: str, full_name: str, field: str) -> None:
    violations = check_password(password, email=email, full_name=full_name)
    if violations:
        raise PasswordPolicyError([(v.code, v.message) for v in violations], field=field)


async def find_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == normalize_email(email)))


def _subject_ip(meta: RequestMeta) -> str:
    return meta.ip or "unknown"


async def _email_send_allowed(ctx: IdentityServices, email: str, meta: RequestMeta) -> None:
    """Raise 429 when too many e-mails were requested for this address or from this IP.

    Applied whether the address exists or not, so the limit does not reveal accounts.
    """
    await ctx.rate_limiter.enforce(EMAIL_SEND_PER_IP, _subject_ip(meta))
    await ctx.rate_limiter.enforce(EMAIL_SEND_PER_ADDRESS, sha256_hex(normalize_email(email)))


# ------------------------------------------------------------------ registration


async def register(
    ctx: IdentityServices,
    *,
    email: str,
    password: str,
    full_name: str,
    locale: str,
    meta: RequestMeta,
) -> None:
    """Create a customer account and send a verification code. Always looks the same to the
    caller whether or not the e-mail already exists (no account enumeration)."""
    email = normalize_email(email)
    full_name = " ".join(full_name.split())
    await ctx.rate_limiter.enforce(REGISTER_PER_IP, _subject_ip(meta))
    ensure_password_policy(password, email=email, full_name=full_name, field="password")
    password_hash = await ctx.hasher.ahash(
        password
    )  # hash before the lookup: same timing both ways
    now = ctx.clock.now()
    async with ctx.db.transaction() as session:
        existing = await find_user_by_email(session, email)
        if existing is not None:
            await request_email(
                session,
                ctx.clock,
                ctx.cipher,
                to=existing.email,
                template="account_exists",
                locale=existing.locale,
                aggregate_id=str(existing.id),
            )
            return
        user = User(
            id=uuid.uuid7(),
            email=email,
            full_name=full_name,
            user_type="CUSTOMER",
            status="PENDING_VERIFICATION",
            locale=locale,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()  # no ORM relationships: insert the parent row first
        session.add(
            Credential(user_id=user.id, password_hash=password_hash, password_changed_at=now)
        )
        await session.flush()
        await assign_role(session, ctx.clock, user_id=user.id, role_code="CUSTOMER")
        code = await issue_code(
            session, ctx, user_id=user.id, purpose="VERIFY_EMAIL", ttl=EMAIL_CODE_TTL
        )
        await request_email(
            session,
            ctx.clock,
            ctx.cipher,
            to=email,
            template="verify_email",
            locale=locale,
            params={"name": full_name, "minutes": str(int(EMAIL_CODE_TTL.total_seconds() // 60))},
            secret_params={"code": code},
            aggregate_id=str(user.id),
        )
        await ctx.audit(
            session,
            action="identity.registered",
            actor_type="USER",
            actor_id=user.id,
            target_id=user.id,
            meta=meta,
        )


async def resend_verification(ctx: IdentityServices, *, email: str, meta: RequestMeta) -> None:
    await _email_send_allowed(ctx, email, meta)
    async with ctx.db.transaction() as session:
        user = await find_user_by_email(session, email)
        if user is None or user.status != "PENDING_VERIFICATION":
            return
        code = await issue_code(
            session, ctx, user_id=user.id, purpose="VERIFY_EMAIL", ttl=EMAIL_CODE_TTL
        )
        await request_email(
            session,
            ctx.clock,
            ctx.cipher,
            to=user.email,
            template="verify_email",
            locale=user.locale,
            params={
                "name": user.full_name,
                "minutes": str(int(EMAIL_CODE_TTL.total_seconds() // 60)),
            },
            secret_params={"code": code},
            aggregate_id=str(user.id),
        )


async def verify_email(ctx: IdentityServices, *, email: str, code: str, meta: RequestMeta) -> None:
    await ctx.rate_limiter.enforce(CODE_CHECK_PER_IP, _subject_ip(meta))
    verified = False
    async with ctx.db.transaction() as session:
        user = await find_user_by_email(session, email)
        if user is not None and user.status == "PENDING_VERIFICATION":
            token = await consume_code(
                session, ctx, user_id=user.id, purpose="VERIFY_EMAIL", code=code
            )
            if token is not None:
                now = ctx.clock.now()
                user.email_verified_at = now
                user.status = "ACTIVE"
                user.updated_at = now
                await ctx.audit(
                    session,
                    action="identity.email_verified",
                    actor_type="USER",
                    actor_id=user.id,
                    target_id=user.id,
                    meta=meta,
                )
                verified = True
    if not verified:
        raise VerificationCodeInvalidError()


# ------------------------------------------------------------------ sign-in


class LoginOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    MFA_REQUIRED = "MFA_REQUIRED"
    MFA_ENROLLMENT_REQUIRED = "MFA_ENROLLMENT_REQUIRED"
    BAD_CREDENTIALS = "BAD_CREDENTIALS"
    LOCKED = "LOCKED"
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
    DISABLED = "DISABLED"


@dataclass(frozen=True, slots=True)
class LoginResult:
    outcome: LoginOutcome
    issued: IssuedSession
    user: User


async def login(  # noqa: PLR0912, PLR0915 - one linear decision table, reviewed as a unit
    ctx: IdentityServices, *, email: str, password: str, audience: str, meta: RequestMeta
) -> LoginResult:
    email = normalize_email(email)
    email_hash = sha256_hex(email)
    ip = _subject_ip(meta)
    await ctx.rate_limiter.enforce(LOGIN_PER_IP, ip)
    await ctx.rate_limiter.enforce(LOGIN_PER_ACCOUNT_IP, f"{email_hash}:{ip}")

    now = ctx.clock.now()
    outcome = LoginOutcome.BAD_CREDENTIALS
    result: LoginResult | None = None
    locked_until = None
    expected_type = "STAFF" if audience == "ADMIN" else "CUSTOMER"

    async with ctx.db.transaction() as session:
        user = await find_user_by_email(session, email)
        credential = await session.get(Credential, user.id, with_for_update=True) if user else None

        if user is None or credential is None:
            await ctx.hasher.adummy_verify(password)
        elif credential.locked_until is not None and credential.locked_until > now:
            outcome, locked_until = LoginOutcome.LOCKED, credential.locked_until
        elif not await ctx.hasher.averify(credential.password_hash, password):
            credential.failed_attempts += 1
            credential.last_failed_at = now
            if credential.failed_attempts >= ACCOUNT_LOCK_THRESHOLD:
                duration = min(ACCOUNT_LOCK_BASE * (2**credential.lock_count), ACCOUNT_LOCK_MAX)
                credential.locked_until = now + duration
                credential.lock_count += 1
                credential.failed_attempts = 0
                await request_email(
                    session,
                    ctx.clock,
                    ctx.cipher,
                    to=user.email,
                    template="account_locked",
                    locale=user.locale,
                    params={
                        "name": user.full_name,
                        "until": (credential.locked_until).strftime("%H:%M %d/%m/%Y UTC"),
                    },
                    aggregate_id=str(user.id),
                )
                await ctx.audit(
                    session,
                    action="identity.account_locked",
                    actor_type="SYSTEM",
                    actor_id=None,
                    target_id=user.id,
                    meta=meta,
                    details={"until": credential.locked_until.isoformat()},
                )
        else:
            credential.failed_attempts = 0
            credential.locked_until = None
            if ctx.hasher.needs_rehash(credential.password_hash):
                credential.password_hash = await ctx.hasher.ahash(password)
            if user.user_type != expected_type:
                outcome = LoginOutcome.BAD_CREDENTIALS  # do not reveal staff/customer accounts
            elif user.status == "PENDING_VERIFICATION":
                outcome = LoginOutcome.EMAIL_NOT_VERIFIED
            elif user.status != "ACTIVE":
                outcome = LoginOutcome.DISABLED
            else:
                state = "ACTIVE"
                outcome = LoginOutcome.SUCCESS
                if audience == "ADMIN":
                    state = "MFA_PENDING"
                    factor = await session.get(MfaFactor, user.id)
                    outcome = (
                        LoginOutcome.MFA_REQUIRED
                        if factor is not None and factor.confirmed_at is not None
                        else LoginOutcome.MFA_ENROLLMENT_REQUIRED
                    )
                else:
                    user.last_login_at = now
                issued = await sessions.issue(
                    session, ctx, user_id=user.id, audience=audience, state=state, meta=meta
                )
                result = LoginResult(outcome=outcome, issued=issued, user=user)
                await ctx.audit(
                    session,
                    action="identity.login",
                    actor_type="STAFF" if audience == "ADMIN" else "USER",
                    actor_id=user.id,
                    target_id=user.id,
                    meta=meta,
                    details={"audience": audience, "outcome": outcome.value},
                )

        session.add(
            LoginAttempt(
                id=uuid.uuid7(),
                user_id=user.id if user else None,
                email_hash=email_hash,
                audience=audience,
                result=outcome.value,
                ip=meta.ip,
                user_agent=(meta.user_agent or "")[:300] or None,
                at=now,
            )
        )

    if result is not None:
        await ctx.rate_limiter.reset(LOGIN_PER_ACCOUNT_IP, f"{email_hash}:{ip}")
        return result
    if outcome is LoginOutcome.LOCKED and locked_until is not None:
        retry = max(int((locked_until - now).total_seconds()), 1)
        raise AccountLockedError(headers={"Retry-After": str(retry)})
    if outcome is LoginOutcome.EMAIL_NOT_VERIFIED:
        raise EmailNotVerifiedError()
    if outcome is LoginOutcome.DISABLED:
        raise AccountDisabledError()
    raise InvalidCredentialsError()


async def logout(
    ctx: IdentityServices, *, session_id: uuid.UUID, user_id: uuid.UUID, meta: RequestMeta
) -> None:
    async with ctx.db.transaction() as session:
        await sessions.revoke(session, ctx, session_id, reason="LOGOUT")
        await ctx.audit(
            session,
            action="identity.logout",
            actor_type="USER",
            actor_id=user_id,
            target_id=user_id,
            meta=meta,
        )


async def logout_all(ctx: IdentityServices, *, user_id: uuid.UUID, meta: RequestMeta) -> int:
    async with ctx.db.transaction() as session:
        count = await sessions.revoke_all(session, ctx, user_id, reason="LOGOUT_ALL")
        await ctx.audit(
            session,
            action="identity.logout_all",
            actor_type="USER",
            actor_id=user_id,
            target_id=user_id,
            meta=meta,
            details={"revoked": count},
        )
    return count


# ------------------------------------------------------------------ password reset


async def forgot_password(ctx: IdentityServices, *, email: str, meta: RequestMeta) -> None:
    await _email_send_allowed(ctx, email, meta)
    async with ctx.db.transaction() as session:
        user = await find_user_by_email(session, email)
        if user is None or user.status != "ACTIVE":
            return
        secret = await issue_link(
            session, ctx, user_id=user.id, purpose="RESET_PASSWORD", ttl=RESET_LINK_TTL
        )
        base = (
            ctx.settings.admin_base_url if user.user_type == "STAFF" else ctx.settings.web_base_url
        )
        await request_email(
            session,
            ctx.clock,
            ctx.cipher,
            to=user.email,
            template="password_reset",
            locale=user.locale,
            params={
                "name": user.full_name,
                "minutes": str(int(RESET_LINK_TTL.total_seconds() // 60)),
            },
            secret_params={"link": f"{base.rstrip('/')}/reset-password?token={secret}"},
            aggregate_id=str(user.id),
        )
        await ctx.audit(
            session,
            action="identity.password_reset_requested",
            actor_type="ANONYMOUS",
            actor_id=None,
            target_id=user.id,
            meta=meta,
        )


async def reset_password(
    ctx: IdentityServices, *, token: str, new_password: str, meta: RequestMeta
) -> None:
    await ctx.rate_limiter.enforce(CODE_CHECK_PER_IP, _subject_ip(meta))
    async with ctx.db.transaction() as session:
        consumed = await consume_link(session, ctx, secret=token, purpose="RESET_PASSWORD")
        user = await session.get(User, consumed.user_id)
        credential = await session.get(Credential, consumed.user_id, with_for_update=True)
        assert user is not None  # noqa: S101 - FK guarantees
        assert credential is not None  # noqa: S101
        ensure_password_policy(
            new_password, email=user.email, full_name=user.full_name, field="new_password"
        )
        now = ctx.clock.now()
        credential.password_hash = await ctx.hasher.ahash(new_password)
        credential.password_changed_at = now
        credential.failed_attempts = 0
        credential.locked_until = None
        await sessions.revoke_all(session, ctx, user.id, reason="PASSWORD_RESET")
        await request_email(
            session,
            ctx.clock,
            ctx.cipher,
            to=user.email,
            template="password_changed",
            locale=user.locale,
            params={"name": user.full_name},
            aggregate_id=str(user.id),
        )
        await ctx.audit(
            session,
            action="identity.password_reset",
            actor_type="USER",
            actor_id=user.id,
            target_id=user.id,
            meta=meta,
        )
