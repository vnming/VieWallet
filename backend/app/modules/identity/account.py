"""Signed-in account management: profile, password, e-mail, sessions, login history."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FieldError, NotFoundError, ValidationFailedError
from app.modules.identity import sessions
from app.modules.identity.auth import ensure_password_policy, find_user_by_email, normalize_email
from app.modules.identity.context import (
    CODE_CHECK_PER_IP,
    EMAIL_CODE_TTL,
    IdentityServices,
    RequestMeta,
)
from app.modules.identity.errors import (
    PasswordIncorrectError,
    PhoneTakenError,
    ProfileNameLockedError,
    VerificationCodeInvalidError,
)
from app.modules.identity.models import Credential, LoginAttempt, User, UserSession
from app.modules.identity.tokens import consume_code, issue_code
from app.modules.notifications.public import request_email


async def _load(session: AsyncSession, user_id: uuid.UUID) -> tuple[User, Credential]:
    user = await session.get(User, user_id, with_for_update=True)
    credential = await session.get(Credential, user_id, with_for_update=True)
    if user is None or credential is None:
        raise NotFoundError()
    return user, credential


@dataclass(frozen=True, slots=True)
class ProfileChanges:
    full_name: str | None = None
    display_name: str | None = None
    locale: str | None = None
    phone: str | None = None  # "" removes the number
    theme: str | None = None
    privacy_mode: bool | None = None

    def empty(self) -> bool:
        return all(getattr(self, f) is None for f in self.__dataclass_fields__)


_PHONE = re.compile(r"^(?:\+84|0)([35789][0-9]{8})$")


def normalize_phone(raw: str) -> str:
    """Vietnamese mobile numbers only (simulated), stored as +84XXXXXXXXX."""
    match = _PHONE.match(raw.replace(" ", "").replace(".", "").replace("-", ""))
    if match is None:
        raise ValidationFailedError(
            errors=[FieldError("phone", "PHONE_INVALID", "Vietnamese mobile number")]
        )
    return f"+84{match.group(1)}"


async def update_profile(
    ctx: IdentityServices, *, user_id: uuid.UUID, changes: ProfileChanges, meta: RequestMeta
) -> User:
    async with ctx.db.transaction() as session:
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            raise NotFoundError()
        fields = ("full_name", "display_name", "locale", "phone", "theme", "privacy_mode")
        before = {f: getattr(user, f) for f in fields}
        if changes.full_name is not None:
            name = " ".join(changes.full_name.split())
            if name != user.full_name and user.kyc_tier >= 1:
                # The verified identity is the KYC name; changing it needs a new KYC.
                raise ProfileNameLockedError()
            user.full_name = name
        if changes.display_name is not None:
            user.display_name = " ".join(changes.display_name.split()) or None
        if changes.locale is not None:
            user.locale = changes.locale
        if changes.phone is not None:
            phone = normalize_phone(changes.phone) if changes.phone.strip() else None
            if phone is not None and phone != user.phone:
                taken = await session.scalar(select(User.id).where(User.phone == phone))
                if taken is not None:
                    raise PhoneTakenError()
            user.phone = phone
        if changes.theme is not None:
            user.theme = changes.theme
        if changes.privacy_mode is not None:
            user.privacy_mode = changes.privacy_mode
        user.updated_at = ctx.clock.now()
        after = {f: getattr(user, f) for f in fields}
        await ctx.audit(
            session,
            action="identity.profile_updated",
            actor_type="USER",
            actor_id=user_id,
            target_id=user_id,
            meta=meta,
            details={
                "changed": sorted(f for f in fields if before[f] != after[f]),
            },
        )
        return user


async def change_password(
    ctx: IdentityServices,
    *,
    user_id: uuid.UUID,
    current_session_id: uuid.UUID,
    current_password: str,
    new_password: str,
    meta: RequestMeta,
) -> None:
    await ctx.rate_limiter.enforce(CODE_CHECK_PER_IP, meta.ip or "unknown")
    async with ctx.db.transaction() as session:
        user, credential = await _load(session, user_id)
        if not await ctx.hasher.averify(credential.password_hash, current_password):
            raise PasswordIncorrectError()
        ensure_password_policy(
            new_password, email=user.email, full_name=user.full_name, field="new_password"
        )
        if await ctx.hasher.averify(credential.password_hash, new_password):
            raise PasswordIncorrectError("new password must differ from the current one")
        credential.password_hash = await ctx.hasher.ahash(new_password)
        credential.password_changed_at = ctx.clock.now()
        revoked = await sessions.revoke_all(
            session, ctx, user_id, reason="PASSWORD_CHANGED", except_session_id=current_session_id
        )
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
            action="identity.password_changed",
            actor_type="USER",
            actor_id=user_id,
            target_id=user_id,
            meta=meta,
            details={"other_sessions_revoked": revoked},
        )


async def request_email_change(
    ctx: IdentityServices, *, user_id: uuid.UUID, password: str, new_email: str, meta: RequestMeta
) -> None:
    """Send a confirmation code to the new address. A taken address gets no code (and the
    response is identical), so the endpoint cannot be used to discover accounts."""
    await ctx.rate_limiter.enforce(CODE_CHECK_PER_IP, meta.ip or "unknown")
    new_email = normalize_email(new_email)
    async with ctx.db.transaction() as session:
        user, credential = await _load(session, user_id)
        if not await ctx.hasher.averify(credential.password_hash, password):
            raise PasswordIncorrectError()
        if new_email == user.email or await find_user_by_email(session, new_email) is not None:
            return
        code = await issue_code(
            session,
            ctx,
            user_id=user_id,
            purpose="CHANGE_EMAIL",
            ttl=EMAIL_CODE_TTL,
            target_email=new_email,
        )
        await request_email(
            session,
            ctx.clock,
            ctx.cipher,
            to=new_email,
            template="email_change_code",
            locale=user.locale,
            params={
                "name": user.full_name,
                "minutes": str(int(EMAIL_CODE_TTL.total_seconds() // 60)),
            },
            secret_params={"code": code},
            aggregate_id=str(user.id),
        )


async def confirm_email_change(
    ctx: IdentityServices, *, user_id: uuid.UUID, code: str, meta: RequestMeta
) -> User:
    await ctx.rate_limiter.enforce(CODE_CHECK_PER_IP, meta.ip or "unknown")
    changed: User | None = None
    async with ctx.db.transaction() as session:
        token = await consume_code(session, ctx, user_id=user_id, purpose="CHANGE_EMAIL", code=code)
        if (
            token is not None
            and token.target_email is not None
            and await find_user_by_email(session, token.target_email) is None
        ):
            user = await session.get(User, user_id, with_for_update=True)
            assert user is not None  # noqa: S101
            old_email = user.email
            user.email = token.target_email
            user.updated_at = ctx.clock.now()
            await request_email(
                session,
                ctx.clock,
                ctx.cipher,
                to=old_email,
                template="email_changed",
                locale=user.locale,
                params={"name": user.full_name, "new_email": user.email},
                aggregate_id=str(user.id),
            )
            await ctx.audit(
                session,
                action="identity.email_changed",
                actor_type="USER",
                actor_id=user_id,
                target_id=user_id,
                meta=meta,
            )
            changed = user
    if changed is None:
        raise VerificationCodeInvalidError()
    return changed


async def list_sessions(ctx: IdentityServices, *, user_id: uuid.UUID) -> list[UserSession]:
    async with ctx.db.transaction() as session:
        return await sessions.list_active(session, ctx, user_id)


async def revoke_session(
    ctx: IdentityServices, *, user_id: uuid.UUID, session_id: uuid.UUID, meta: RequestMeta
) -> None:
    async with ctx.db.transaction() as session:
        row = await session.get(UserSession, session_id)
        # Someone else's session is reported as "not found" (no existence leak).
        if row is None or row.user_id != user_id or row.revoked_at is not None:
            raise NotFoundError()
        await sessions.revoke(session, ctx, session_id, reason="REVOKED_BY_USER")
        await ctx.audit(
            session,
            action="identity.session_revoked",
            actor_type="USER",
            actor_id=user_id,
            target_id=user_id,
            meta=meta,
            details={"session_id": str(session_id)},
        )


async def login_history(
    ctx: IdentityServices, *, user_id: uuid.UUID, limit: int = 20
) -> list[LoginAttempt]:
    async with ctx.db.transaction() as session:
        rows = await session.scalars(
            select(LoginAttempt)
            .where(LoginAttempt.user_id == user_id)
            .order_by(LoginAttempt.at.desc(), LoginAttempt.id.desc())
            .limit(limit)
        )
        return list(rows.all())
