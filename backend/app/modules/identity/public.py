"""Public interface of the identity module (ADR-0001)."""

from app.modules.identity.context import IdentityServices, RequestMeta
from app.modules.identity.demo import create_verified_customer
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
from app.modules.identity.tiers import (
    credentials_changed_at,
    kyc_tier_of,
    set_kyc_tier,
    set_tier_for_user,
    user_id_by_email,
)

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
    "create_verified_customer",
    "credentials_changed_at",
    "ensure_permission",
    "kyc_tier_of",
    "require_permission",
    "set_kyc_tier",
    "set_tier_for_user",
    "user_id_by_email",
    "verify_pin",
]
