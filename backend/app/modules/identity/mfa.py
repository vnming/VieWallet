"""Staff TOTP MFA (RFC 6238) with replay protection, recovery codes and step-up."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import constant_time_equals, sha256_hex
from app.modules.identity import sessions
from app.modules.identity.context import MFA_PER_SESSION, IdentityServices, RequestMeta
from app.modules.identity.errors import (
    MfaAlreadyEnrolledError,
    MfaEnrollmentRequiredError,
    MfaInvalidError,
)
from app.modules.identity.models import MfaFactor, MfaRecoveryCode, User, UserSession
from app.modules.identity.sessions import IssuedSession
from app.modules.notifications.public import request_email

ISSUER = "VieWallet"
STEP_SECONDS = 30
RECOVERY_CODE_COUNT = 10
_AAD = "identity.mfa_secret"
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(frozen=True, slots=True)
class Enrollment:
    secret: str
    otpauth_uri: str


@dataclass(frozen=True, slots=True)
class EnrollmentConfirmed:
    issued: IssuedSession
    recovery_codes: list[str]


def _recovery_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def _normalize_recovery(code: str) -> str:
    return code.strip().upper().replace(" ", "")


def _matching_step(
    ctx: IdentityServices, secret: str, code: str, last_used_step: int | None
) -> int | None:
    """Return the time step the code belongs to (±1 step drift), refusing already used steps."""
    if not (code.isdigit() and len(code) == 6):  # noqa: PLR2004
        return None
    totp = pyotp.TOTP(secret, interval=STEP_SECONDS)
    current = int(ctx.clock.now().timestamp()) // STEP_SECONDS
    for step in (current - 1, current, current + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if constant_time_equals(totp.at(step * STEP_SECONDS), code):
            return step
    return None


async def begin_enrollment(
    ctx: IdentityServices, *, pending: UserSession, user: User
) -> Enrollment:
    async with ctx.db.transaction() as session:
        factor = await session.get(MfaFactor, user.id, with_for_update=True)
        if factor is not None and factor.confirmed_at is not None:
            raise MfaAlreadyEnrolledError()
        secret = pyotp.random_base32()
        encrypted = ctx.cipher.encrypt(secret, associated_data=f"{_AAD}:{user.id}")
        if factor is None:
            session.add(
                MfaFactor(user_id=user.id, secret_encrypted=encrypted, created_at=ctx.clock.now())
            )
        else:
            factor.secret_encrypted = encrypted
            factor.created_at = ctx.clock.now()
    uri = pyotp.TOTP(secret, interval=STEP_SECONDS).provisioning_uri(
        name=user.email, issuer_name=ISSUER
    )
    return Enrollment(secret=secret, otpauth_uri=uri)


async def _decrypt(ctx: IdentityServices, factor: MfaFactor) -> str:
    return ctx.cipher.decrypt(factor.secret_encrypted, associated_data=f"{_AAD}:{factor.user_id}")


async def confirm_enrollment(
    ctx: IdentityServices, *, pending: UserSession, user: User, code: str, meta: RequestMeta
) -> EnrollmentConfirmed:
    await ctx.rate_limiter.enforce(MFA_PER_SESSION, str(pending.id))
    result: EnrollmentConfirmed | None = None
    async with ctx.db.transaction() as session:
        factor = await session.get(MfaFactor, user.id, with_for_update=True)
        if factor is None:
            raise MfaEnrollmentRequiredError()
        if factor.confirmed_at is not None:
            raise MfaAlreadyEnrolledError()
        step = _matching_step(ctx, await _decrypt(ctx, factor), code, factor.last_used_step)
        if step is not None:
            factor.confirmed_at = ctx.clock.now()
            factor.last_used_step = step
            codes = [_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
            for plain in codes:
                session.add(
                    MfaRecoveryCode(
                        id=uuid.uuid7(),
                        user_id=user.id,
                        code_hash=sha256_hex(_normalize_recovery(plain)),
                    )
                )
            issued = await sessions.upgrade_after_mfa(session, ctx, pending, meta)
            await request_email(
                session,
                ctx.clock,
                ctx.cipher,
                to=user.email,
                template="mfa_enabled",
                locale=user.locale,
                params={"name": user.full_name},
                aggregate_id=str(user.id),
            )
            await ctx.audit(
                session,
                action="identity.mfa_enrolled",
                actor_type="STAFF",
                actor_id=user.id,
                target_id=user.id,
                meta=meta,
            )
            result = EnrollmentConfirmed(issued=issued, recovery_codes=codes)
    if result is None:
        raise MfaInvalidError()
    return result


async def _consume_recovery_code(
    session: AsyncSession, ctx: IdentityServices, user_id: uuid.UUID, code: str
) -> bool:
    row = await session.scalar(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == user_id,
            MfaRecoveryCode.code_hash == sha256_hex(_normalize_recovery(code)),
            MfaRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if row is None:
        return False
    row.used_at = ctx.clock.now()
    return True


async def verify_login(
    ctx: IdentityServices,
    *,
    pending: UserSession,
    user: User,
    code: str | None,
    recovery_code: str | None,
    meta: RequestMeta,
) -> IssuedSession:
    await ctx.rate_limiter.enforce(MFA_PER_SESSION, str(pending.id))
    issued: IssuedSession | None = None
    async with ctx.db.transaction() as session:
        factor = await session.get(MfaFactor, user.id, with_for_update=True)
        if factor is None or factor.confirmed_at is None:
            raise MfaEnrollmentRequiredError()
        ok = False
        method = "totp"
        if code:
            step = _matching_step(ctx, await _decrypt(ctx, factor), code, factor.last_used_step)
            if step is not None:
                factor.last_used_step = step
                ok = True
        elif recovery_code:
            method = "recovery_code"
            ok = await _consume_recovery_code(session, ctx, user.id, recovery_code)
        if ok:
            issued = await sessions.upgrade_after_mfa(session, ctx, pending, meta)
            await ctx.audit(
                session,
                action="identity.mfa_verified",
                actor_type="STAFF",
                actor_id=user.id,
                target_id=user.id,
                meta=meta,
                details={"method": method},
            )
        else:
            row = await session.get(UserSession, pending.id, with_for_update=True)
            if row is not None:
                row.mfa_failed_attempts += 1
            await ctx.audit(
                session,
                action="identity.mfa_verified",
                actor_type="STAFF",
                actor_id=user.id,
                target_id=user.id,
                meta=meta,
                result="FAILED",
                details={"method": method},
            )
    if issued is None:
        raise MfaInvalidError()
    return issued


async def step_up(
    ctx: IdentityServices, *, active: UserSession, user: User, code: str, meta: RequestMeta
) -> None:
    """Refresh ``mfa_verified_at`` so sensitive permissions are usable for 15 minutes."""
    await ctx.rate_limiter.enforce(MFA_PER_SESSION, str(active.id))
    ok = False
    async with ctx.db.transaction() as session:
        factor = await session.get(MfaFactor, user.id, with_for_update=True)
        if factor is None or factor.confirmed_at is None:
            raise MfaEnrollmentRequiredError()
        step = _matching_step(ctx, await _decrypt(ctx, factor), code, factor.last_used_step)
        if step is not None:
            factor.last_used_step = step
            row = await session.get(UserSession, active.id, with_for_update=True)
            assert row is not None  # noqa: S101
            row.mfa_verified_at = ctx.clock.now()
            ok = True
        await ctx.audit(
            session,
            action="identity.mfa_step_up",
            actor_type="STAFF",
            actor_id=user.id,
            target_id=user.id,
            meta=meta,
            result="SUCCESS" if ok else "FAILED",
        )
    if not ok:
        raise MfaInvalidError()
