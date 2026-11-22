"""Public interface of the identity module (ADR-0001)."""

from app.modules.identity.context import IdentityServices, RequestMeta
from app.modules.identity.errors import PinInvalidError, PinLockedError, PinNotSetError
from app.modules.identity.http import (
    CustomerPrincipal,
    IdentityDep,
    MetaDep,
    Principal,
    StaffPrincipal,
    ensure_permission,
    require_permission,
)
from app.modules.identity.models import User
from app.modules.identity.pin import verify_pin
from app.modules.identity.staff import create_staff

__all__ = [
    "CustomerPrincipal",
    "IdentityDep",
    "IdentityServices",
    "MetaDep",
    "PinInvalidError",
    "PinLockedError",
    "PinNotSetError",
    "Principal",
    "RequestMeta",
    "StaffPrincipal",
    "User",
    "create_staff",
    "ensure_permission",
    "require_permission",
    "verify_pin",
]
