"""Public interface of the access (RBAC) module."""

from app.modules.access.catalog import (
    PERMISSION_CODES,
    ROLE_CODES,
    SENSITIVE_PERMISSIONS,
    STAFF_ROLE_CODES,
)
from app.modules.access.service import (
    UnknownRoleError,
    assign_role,
    permissions_for_user,
    roles_for_user,
    seed_catalog,
)

__all__ = [
    "PERMISSION_CODES",
    "ROLE_CODES",
    "SENSITIVE_PERMISSIONS",
    "STAFF_ROLE_CODES",
    "UnknownRoleError",
    "assign_role",
    "permissions_for_user",
    "roles_for_user",
    "seed_catalog",
]
