"""Staff account provisioning (CLI only; there is no public staff sign-up)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.core.errors import ConflictError, ValidationFailedError
from app.modules.access.public import STAFF_ROLE_CODES, assign_role
from app.modules.identity.auth import ensure_password_policy, find_user_by_email, normalize_email
from app.modules.identity.context import IdentityServices, RequestMeta
from app.modules.identity.models import Credential, User


async def create_staff(
    ctx: IdentityServices,
    *,
    email: str,
    full_name: str,
    password: str,
    roles: Sequence[str],
) -> uuid.UUID:
    email = normalize_email(email)
    unknown = set(roles) - STAFF_ROLE_CODES
    if not roles or unknown:
        raise ValidationFailedError(
            f"staff roles must be a non-empty subset of {sorted(STAFF_ROLE_CODES)}"
        )
    ensure_password_policy(password, email=email, full_name=full_name, field="password")
    now = ctx.clock.now()
    async with ctx.db.transaction() as session:
        if await find_user_by_email(session, email) is not None:
            raise ConflictError("a user with this e-mail already exists")
        user = User(
            id=uuid.uuid7(),
            email=email,
            email_verified_at=now,
            full_name=" ".join(full_name.split()),
            user_type="STAFF",
            status="ACTIVE",
            locale="vi",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()  # no ORM relationships: insert the parent row first
        session.add(
            Credential(
                user_id=user.id,
                password_hash=await ctx.hasher.ahash(password),
                password_changed_at=now,
            )
        )
        await session.flush()
        for role in roles:
            await assign_role(session, ctx.clock, user_id=user.id, role_code=role)
        await ctx.audit(
            session,
            action="identity.staff_created",
            actor_type="SYSTEM",
            actor_id=None,
            target_id=user.id,
            meta=RequestMeta(),
            details={"roles": sorted(roles)},
        )
        return user.id
