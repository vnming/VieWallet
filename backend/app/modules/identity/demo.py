"""Verified customer accounts for development/simulation CLIs (demo merchants).

Customers normally sign up and verify their e-mail themselves; this shortcut exists only for
the ``create_merchant --create-user`` CLI, which refuses to run outside development/simulation.
"""

from __future__ import annotations

import uuid

from app.core.errors import ConflictError
from app.modules.access.public import assign_role
from app.modules.identity.auth import ensure_password_policy, find_user_by_email, normalize_email
from app.modules.identity.context import IdentityServices, RequestMeta
from app.modules.identity.models import Credential, User


async def create_verified_customer(
    ctx: IdentityServices, *, email: str, full_name: str, password: str
) -> uuid.UUID:
    email = normalize_email(email)
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
            user_type="CUSTOMER",
            status="ACTIVE",
            locale="vi",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
        session.add(
            Credential(
                user_id=user.id,
                password_hash=await ctx.hasher.ahash(password),
                password_changed_at=now,
            )
        )
        await session.flush()
        await assign_role(session, ctx.clock, user_id=user.id, role_code="CUSTOMER")
        await ctx.audit(
            session,
            action="identity.demo_customer_created",
            actor_type="SYSTEM",
            actor_id=None,
            target_id=user.id,
            meta=RequestMeta(),
        )
        return user.id
