"""Public interface of the identity module (ADR-0001)."""

from app.modules.identity.context import IdentityServices, RequestMeta
from app.modules.identity.errors import PinInvalidError, PinLockedError, PinNotSetError
from app.modules.identity.http import (
    CustomerPrincipal,
    IdentityDep,
    MetaDep,
    Principal,
    StaffPrincipal,
    require_permission,
)
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
    "create_staff",
    "require_permission",
    "verify_pin",
]
