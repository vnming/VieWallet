from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.modules.access.catalog import PERMISSIONS, ROLE_CODES, ROLES
from app.modules.access.models import Permission, Role, RolePermission, UserRole


class UnknownRoleError(ValueError):
    pass


async def seed_catalog(session: AsyncSession) -> None:
    """Make the roles/permissions tables match the catalog (idempotent)."""
    for perm in PERMISSIONS:
        stmt = insert(Permission).values(
            id=uuid.uuid7(),
            code=perm.code,
            description=perm.description,
            is_sensitive=perm.sensitive,
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[Permission.code],
                set_={
                    "description": stmt.excluded.description,
                    "is_sensitive": stmt.excluded.is_sensitive,
                },
            )
        )
    for role in ROLES:
        stmt_role = insert(Role).values(
            id=uuid.uuid7(), code=role.code, name=role.name, is_staff=role.is_staff
        )
        await session.execute(
            stmt_role.on_conflict_do_update(
                index_elements=[Role.code],
                set_={"name": stmt_role.excluded.name, "is_staff": stmt_role.excluded.is_staff},
            )
        )
    await session.flush()

    role_ids = {r.code: r.id for r in (await session.scalars(select(Role))).all()}
    perm_ids = {p.code: p.id for p in (await session.scalars(select(Permission))).all()}
    for role in ROLES:
        role_id = role_ids[role.code]
        wanted = {perm_ids[code] for code in role.permissions}
        await session.execute(
            delete(RolePermission).where(
                RolePermission.role_id == role_id, RolePermission.permission_id.not_in(wanted)
            )
        )
        for permission_id in wanted:
            await session.execute(
                insert(RolePermission)
                .values(role_id=role_id, permission_id=permission_id)
                .on_conflict_do_nothing()
            )


async def assign_role(
    session: AsyncSession,
    clock: Clock,
    *,
    user_id: uuid.UUID,
    role_code: str,
    granted_by: uuid.UUID | None = None,
) -> None:
    if role_code not in ROLE_CODES:
        raise UnknownRoleError(role_code)
    role_id = await session.scalar(select(Role.id).where(Role.code == role_code))
    if role_id is None:
        raise UnknownRoleError(f"{role_code} is not seeded")
    await session.execute(
        insert(UserRole)
        .values(user_id=user_id, role_id=role_id, granted_by=granted_by, granted_at=clock.now())
        .on_conflict_do_nothing()
    )


async def roles_for_user(session: AsyncSession, user_id: uuid.UUID) -> frozenset[str]:
    rows = await session.scalars(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    return frozenset(rows.all())


async def permissions_for_user(session: AsyncSession, user_id: uuid.UUID) -> frozenset[str]:
    rows = await session.scalars(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
        .distinct()
    )
    return frozenset(rows.all())
