"""Server-side sessions (ADR-0012)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import random_token, sha256_hex
from app.modules.identity.context import MFA_PENDING_TTL, IdentityServices, RequestMeta
from app.modules.identity.models import User, UserSession

TOUCH_INTERVAL = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    session: UserSession


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    session: UserSession
    user: User


def _lifetimes(ctx: IdentityServices, audience: str) -> tuple[timedelta, timedelta]:
    s = ctx.settings
    if audience == "ADMIN":
        return timedelta(minutes=s.admin_session_idle_minutes), timedelta(
            hours=s.admin_session_absolute_hours
        )
    return timedelta(hours=s.web_session_idle_hours), timedelta(hours=s.web_session_absolute_hours)


async def issue(
    session: AsyncSession,
    ctx: IdentityServices,
    *,
    user_id: uuid.UUID,
    audience: str,
    state: str,
    meta: RequestMeta,
) -> IssuedSession:
    now = ctx.clock.now()
    idle, absolute = _lifetimes(ctx, audience)
    if state == "MFA_PENDING":
        idle = absolute = MFA_PENDING_TTL
    token = random_token(32)
    row = UserSession(
        id=uuid.uuid7(),
        user_id=user_id,
        audience=audience,
        state=state,
        token_hash=sha256_hex(token),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + idle,
        absolute_expires_at=now + absolute,
        ip=meta.ip,
        user_agent=(meta.user_agent or "")[:300] or None,
    )
    session.add(row)
    await session.flush()
    return IssuedSession(token=token, session=row)


async def resolve(
    session: AsyncSession, ctx: IdentityServices, *, token: str, audience: str
) -> ResolvedSession | None:
    """Return the live session for ``token`` or None (unknown, revoked, expired, wrong app)."""
    now = ctx.clock.now()
    row = await session.scalar(
        select(UserSession).where(UserSession.token_hash == sha256_hex(token))
    )
    if row is None or row.revoked_at is not None or row.audience != audience:
        return None
    if now >= row.idle_expires_at or now >= row.absolute_expires_at:
        await revoke(session, ctx, row.id, reason="EXPIRED")
        return None
    user = await session.get(User, row.user_id)
    if user is None or user.status != "ACTIVE":
        await revoke(session, ctx, row.id, reason="USER_INACTIVE")
        return None
    if row.state == "ACTIVE" and now - row.last_seen_at >= TOUCH_INTERVAL:
        idle, _ = _lifetimes(ctx, audience)
        row.last_seen_at = now
        row.idle_expires_at = min(now + idle, row.absolute_expires_at)
    return ResolvedSession(session=row, user=user)


async def upgrade_after_mfa(
    session: AsyncSession, ctx: IdentityServices, pending: UserSession, meta: RequestMeta
) -> IssuedSession:
    """Replace an MFA_PENDING session with a fresh ACTIVE one (new token: no fixation)."""
    await revoke(session, ctx, pending.id, reason="MFA_UPGRADED")
    issued = await issue(
        session, ctx, user_id=pending.user_id, audience=pending.audience, state="ACTIVE", meta=meta
    )
    issued.session.mfa_verified_at = ctx.clock.now()
    return issued


async def revoke(
    session: AsyncSession, ctx: IdentityServices, session_id: uuid.UUID, *, reason: str
) -> None:
    await session.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=ctx.clock.now(), revoke_reason=reason)
    )


async def revoke_all(
    session: AsyncSession,
    ctx: IdentityServices,
    user_id: uuid.UUID,
    *,
    reason: str,
    except_session_id: uuid.UUID | None = None,
) -> int:
    conditions = [UserSession.user_id == user_id, UserSession.revoked_at.is_(None)]
    if except_session_id is not None:
        conditions.append(UserSession.id != except_session_id)
    result = await session.execute(
        update(UserSession)
        .where(*conditions)
        .values(revoked_at=ctx.clock.now(), revoke_reason=reason)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def list_active(
    session: AsyncSession, ctx: IdentityServices, user_id: uuid.UUID
) -> list[UserSession]:
    now: datetime = ctx.clock.now()
    rows = await session.scalars(
        select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.state == "ACTIVE",
            UserSession.idle_expires_at > now,
            UserSession.absolute_expires_at > now,
        )
        .order_by(UserSession.last_seen_at.desc())
    )
    return list(rows.all())
