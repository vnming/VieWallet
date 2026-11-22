"""Role and permission catalog (docs/architecture/security.md §4).

The catalog is seeded into the database; roles stay data (adjustable later through the
approval workflow). Permission codes follow ``resource:action:scope`` with scope ``own`` or
``any``. Sensitive permissions require a fresh MFA verification (step-up) for staff.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PermissionDef:
    code: str
    description: str
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class RoleDef:
    code: str
    name: str
    is_staff: bool
    permissions: tuple[str, ...]


PERMISSIONS: tuple[PermissionDef, ...] = (
    PermissionDef("profile:manage:own", "Manage own profile, password, PIN and sessions"),
    PermissionDef("wallet:read:own", "Read own wallet and transactions"),
    PermissionDef("wallet:transact:own", "Move money from own wallet"),
    PermissionDef("user:read:any", "Read any user (masked PII)"),
    PermissionDef("user:pii_reveal:any", "Reveal unmasked PII", sensitive=True),
    PermissionDef("transaction:read:any", "Read any transaction"),
    PermissionDef("transaction:inquire:any", "Run provider status inquiries"),
    PermissionDef("refund:propose:any", "Propose refunds"),
    PermissionDef("refund:approve:any", "Approve refunds", sensitive=True),
    PermissionDef("reversal:propose:any", "Propose reversals"),
    PermissionDef("reversal:approve:any", "Approve reversals", sensitive=True),
    PermissionDef("adjustment:propose:any", "Propose ledger adjustments", sensitive=True),
    PermissionDef("adjustment:approve:any", "Approve ledger adjustments", sensitive=True),
    PermissionDef("wallet:freeze:any", "Freeze wallets"),
    PermissionDef("wallet:unfreeze:any", "Unfreeze wallets (through approval)", sensitive=True),
    PermissionDef("risk_alert:manage:any", "Work risk alerts"),
    PermissionDef("risk_hold:decide:any", "Approve or reject risk holds", sensitive=True),
    PermissionDef("kyc:review:any", "Review KYC submissions"),
    PermissionDef("ticket:manage:any", "Manage support tickets"),
    PermissionDef("reconciliation:manage:any", "Run and resolve reconciliation"),
    PermissionDef("config:propose:any", "Propose configuration changes", sensitive=True),
    PermissionDef("config:approve:any", "Approve configuration changes", sensitive=True),
    PermissionDef("role:assign:any", "Assign roles to users", sensitive=True),
    PermissionDef("audit:read:any", "Read the audit log"),
    PermissionDef("audit:verify:any", "Verify the audit hash chain", sensitive=True),
    PermissionDef("report:read:any", "Read platform reports"),
    PermissionDef("simulator:manage:any", "Control the payment provider simulator"),
    PermissionDef("ai_admin:manage:any", "Manage AI models, prompts and quotas", sensitive=True),
)

_CUSTOMER = ("profile:manage:own", "wallet:read:own", "wallet:transact:own")
_STAFF_BASE = ("profile:manage:own",)

ROLES: tuple[RoleDef, ...] = (
    RoleDef("CUSTOMER", "Khách hàng", is_staff=False, permissions=_CUSTOMER),
    RoleDef("MERCHANT", "Merchant (mô phỏng)", is_staff=False, permissions=_CUSTOMER),
    RoleDef(
        "SUPPORT",
        "Hỗ trợ khách hàng",
        is_staff=True,
        permissions=(*_STAFF_BASE, "user:read:any", "transaction:read:any", "ticket:manage:any"),
    ),
    RoleDef(
        "OPERATIONS",
        "Vận hành",
        is_staff=True,
        permissions=(
            *_STAFF_BASE,
            "user:read:any",
            "transaction:read:any",
            "transaction:inquire:any",
            "refund:propose:any",
            "reversal:propose:any",
            "ticket:manage:any",
            "simulator:manage:any",
        ),
    ),
    RoleDef(
        "FINANCE",
        "Tài chính",
        is_staff=True,
        permissions=(
            *_STAFF_BASE,
            "user:read:any",
            "transaction:read:any",
            "transaction:inquire:any",
            "refund:propose:any",
            "refund:approve:any",
            "reversal:propose:any",
            "reversal:approve:any",
            "adjustment:propose:any",
            "adjustment:approve:any",
            "reconciliation:manage:any",
            "audit:read:any",
            "report:read:any",
        ),
    ),
    RoleDef(
        "RISK_ANALYST",
        "Phân tích rủi ro",
        is_staff=True,
        permissions=(
            *_STAFF_BASE,
            "user:read:any",
            "user:pii_reveal:any",
            "transaction:read:any",
            "wallet:freeze:any",
            "wallet:unfreeze:any",
            "risk_alert:manage:any",
            "risk_hold:decide:any",
            "audit:read:any",
        ),
    ),
    RoleDef(
        "KYC_REVIEWER",
        "Duyệt KYC",
        is_staff=True,
        permissions=(*_STAFF_BASE, "user:read:any", "user:pii_reveal:any", "kyc:review:any"),
    ),
    RoleDef(
        "ADMIN",
        "Quản trị",
        is_staff=True,
        permissions=(
            *_STAFF_BASE,
            "user:read:any",
            "user:pii_reveal:any",
            "transaction:read:any",
            "transaction:inquire:any",
            "refund:propose:any",
            "refund:approve:any",
            "reversal:propose:any",
            "reversal:approve:any",
            "wallet:freeze:any",
            "wallet:unfreeze:any",
            "ticket:manage:any",
            "config:propose:any",
            "role:assign:any",
            "audit:read:any",
            "report:read:any",
            "simulator:manage:any",
            "ai_admin:manage:any",
        ),
    ),
    RoleDef(
        "SUPER_ADMIN",
        "Quản trị cấp cao",
        is_staff=True,
        permissions=tuple(p.code for p in PERMISSIONS),
    ),
)

PERMISSION_CODES = frozenset(p.code for p in PERMISSIONS)
SENSITIVE_PERMISSIONS = frozenset(p.code for p in PERMISSIONS if p.sensitive)
ROLE_CODES = frozenset(r.code for r in ROLES)
STAFF_ROLE_CODES = frozenset(r.code for r in ROLES if r.is_staff)


def _validate() -> None:
    for role in ROLES:
        unknown = set(role.permissions) - PERMISSION_CODES
        if unknown:
            raise ValueError(f"role {role.code} references unknown permissions: {unknown}")
        if not role.is_staff and any(p.endswith(":any") for p in role.permissions):
            raise ValueError(f"customer role {role.code} must not hold ':any' permissions")


_validate()
