"""Signed-in account management: profile, password, e-mail, sessions, login history."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.identity import sessions
from app.modules.identity.auth import ensure_password_policy, find_user_by_email, normalize_email
from app.modules.identity.context import (
    CODE_CHECK_PER_IP,
    EMAIL_CODE_TTL,
    IdentityServices,
    RequestMeta,
)
from app.modules.identity.errors import PasswordIncorrectError, VerificationCodeInvalidError
from app.modules.identity.models import Credential, LoginAttempt, User, UserSession
from app.modules.identity.tokens import consume_code, issue_code
from app.modules.notifications.public import request_email


async def _load(session: AsyncSession, user_id: uuid.UUID) -> tuple[User, Credential]:
    user = await session.get(User, user_id, with_for_update=True)
    credential = await session.get(Credential, user_id, with_for_update=True)
    if user is None or credential is None:
        raise NotFoundError()
    return user, credential


async def update_profile(
    ctx: IdentityServices,
    *,
    user_id: uuid.UUID,
    full_name: str | None,
    display_name: str | None,
    locale: str | None,
    meta: RequestMeta,
) -> User:
    async with ctx.db.transaction() as session:
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            raise NotFoundError()
        before = {
            "full_name": user.full_name,
            "display_name": user.display_name,
            "locale": user.locale,
        }
        if full_name is not None:
            user.full_name = " ".join(full_name.split())
        if display_name is not None:
            user.display_name = " ".join(display_name.split()) or None
        if locale is not None:
            user.locale = locale
        user.updated_at = ctx.clock.now()
        after = {
            "full_name": user.full_name,
            "display_name": user.display_name,
            "locale": user.locale,
        }
        await ctx.audit(
            session,
            action="identity.profile_updated",
            actor_type="USER",
            actor_id=user_id,
            target_id=user_id,
            meta=meta,
            details={"before": before, "after": after},
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
