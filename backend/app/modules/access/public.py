"""Public interface of the access (RBAC) module."""

from app.modules.access.catalog import (
    PERMISSION_CODES,
    PERMISSIONS,
    ROLE_CODES,
    ROLES,
    SENSITIVE_PERMISSIONS,
    STAFF_ROLE_CODES,
)
from app.modules.access.service import (
    UnknownRoleError,
    assign_role,
    holders_of,
    permissions_for_user,
    revoke_role,
    roles_for_user,
    seed_catalog,
)

__all__ = [
    "PERMISSIONS",
    "PERMISSION_CODES",
    "ROLES",
    "ROLE_CODES",
    "SENSITIVE_PERMISSIONS",
    "STAFF_ROLE_CODES",
    "UnknownRoleError",
    "assign_role",
    "holders_of",
    "permissions_for_user",
    "revoke_role",
    "roles_for_user",
    "seed_catalog",
]
