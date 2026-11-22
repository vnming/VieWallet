"""One-time verification codes (6 digits, keyed hash) and reset links (random, SHA-256)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import numeric_code, random_token, sha256_hex
from app.modules.identity.context import EMAIL_CODE_MAX_ATTEMPTS, IdentityServices
from app.modules.identity.errors import TokenInvalidError
from app.modules.identity.models import VerificationToken


async def _invalidate_previous(
    session: AsyncSession, ctx: IdentityServices, user_id: uuid.UUID, purpose: str
) -> None:
    await session.execute(
        update(VerificationToken)
        .where(
            VerificationToken.user_id == user_id,
            VerificationToken.purpose == purpose,
            VerificationToken.consumed_at.is_(None),
        )
        .values(consumed_at=ctx.clock.now())
    )


async def issue_code(
    session: AsyncSession,
    ctx: IdentityServices,
    *,
    user_id: uuid.UUID,
    purpose: str,
    ttl: timedelta,
    target_email: str | None = None,
) -> str:
    await _invalidate_previous(session, ctx, user_id, purpose)
    code = numeric_code(6)
    now = ctx.clock.now()
    session.add(
        VerificationToken(
            id=uuid.uuid7(),
            user_id=user_id,
            purpose=purpose,
            secret_hash=ctx.hash_code(purpose, user_id, code),
            target_email=target_email,
            created_at=now,
            expires_at=now + ttl,
        )
    )
    await session.flush()
    return code


async def consume_code(
    session: AsyncSession, ctx: IdentityServices, *, user_id: uuid.UUID, purpose: str, code: str
) -> VerificationToken | None:
    """Consume the latest live code, or return None.

    Wrong guesses are counted (after 5 the code is burned). The function does not raise on a
    wrong code, so the caller can commit the attempt counter before reporting the error.
    """
    now = ctx.clock.now()
    token = await session.scalar(
        select(VerificationToken)
        .where(
            VerificationToken.user_id == user_id,
            VerificationToken.purpose == purpose,
            VerificationToken.consumed_at.is_(None),
            VerificationToken.expires_at > now,
        )
        .order_by(VerificationToken.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if token is None:
        return None
    if not code.isdigit() or token.secret_hash != ctx.hash_code(purpose, user_id, code):
        token.attempts += 1
        if token.attempts >= EMAIL_CODE_MAX_ATTEMPTS:
            token.consumed_at = now
        return None
    token.consumed_at = now
    return token


async def issue_link(
    session: AsyncSession,
    ctx: IdentityServices,
    *,
    user_id: uuid.UUID,
    purpose: str,
    ttl: timedelta,
) -> str:
    await _invalidate_previous(session, ctx, user_id, purpose)
    secret = random_token(32)
    now = ctx.clock.now()
    session.add(
        VerificationToken(
            id=uuid.uuid7(),
            user_id=user_id,
            purpose=purpose,
            secret_hash=sha256_hex(secret),
            created_at=now,
            expires_at=now + ttl,
        )
    )
    await session.flush()
    return secret


async def consume_link(
    session: AsyncSession, ctx: IdentityServices, *, secret: str, purpose: str
) -> VerificationToken:
    now = ctx.clock.now()
    token = await session.scalar(
        select(VerificationToken)
        .where(
            VerificationToken.secret_hash == sha256_hex(secret),
            VerificationToken.purpose == purpose,
        )
        .with_for_update()
    )
    if token is None or token.consumed_at is not None or token.expires_at <= now:
        raise TokenInvalidError()
    token.consumed_at = now
    return token
