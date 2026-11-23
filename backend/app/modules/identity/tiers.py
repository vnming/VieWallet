"""KYC tier and recent-credential-change lookups used by limits and risk rules.

Until the KYC module (Phase 6) exists, ``set_kyc_tier`` is reachable only from the
development/simulation CLI ``app.scripts.set_kyc_tier``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import NotFoundError, ValidationFailedError
from app.modules.audit.public import AuditEntry, record
from app.modules.identity.models import Credential, TransactionPin, User

KYC_TIERS = (0, 1)


async def kyc_tier_of(session: AsyncSession, user_id: uuid.UUID) -> int:
    tier = await session.scalar(select(User.kyc_tier).where(User.id == user_id))
    if tier is None:
        raise NotFoundError()
    return int(tier)


async def credentials_changed_at(session: AsyncSession, user_id: uuid.UUID) -> datetime | None:
    """Latest password change, PIN change or PIN reset (risk cooldown, security.md §8)."""
    password = await session.scalar(
        select(Credential.password_changed_at).where(Credential.user_id == user_id)
    )
    pin = await session.scalar(
        select(func.greatest(TransactionPin.changed_at, TransactionPin.reset_at)).where(
            TransactionPin.user_id == user_id
        )
    )
    candidates = [value for value in (password, pin) if value is not None]
    return max(candidates) if candidates else None


async def set_kyc_tier(
    session: AsyncSession, clock: Clock, *, email: str, tier: int, reason: str
) -> uuid.UUID:
    if tier not in KYC_TIERS:
        raise ValidationFailedError("tier must be 0 or 1")
    user = await session.scalar(
        select(User).where(User.email == email, User.user_type == "CUSTOMER").with_for_update()
    )
    if user is None:
        raise NotFoundError()
    before = user.kyc_tier
    user.kyc_tier = tier
    user.updated_at = clock.now()
    await record(
        session,
        clock,
        AuditEntry(
            action="identity.kyc_tier_set",
            actor_type="SYSTEM",
            target_type="user",
            target_id=str(user.id),
            reason=reason,
            before={"kyc_tier": before},
            after={"kyc_tier": tier},
        ),
    )
    return user.id
