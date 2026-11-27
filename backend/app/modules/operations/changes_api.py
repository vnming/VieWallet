"""Admin API: staff refunds, fee/limit versions and role changes, all through maker-checker.

Owner decisions (Phase 8): a staff refund always needs a second person (Finance); fee and limit
changes are new rule versions activated on approval; role changes are proposed and approved by
different people, nobody approves a change to their own roles and only a Super Admin decides on
the Super Admin role. Role permissions stay fixed in the catalog (seed).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.errors import FieldError, ForbiddenError, NotFoundError, ValidationFailedError
from app.core.idempotency import IdempotencyDep, IdempotencyKey, OperationResult
from app.core.money import Money, MoneyError
from app.modules.access.public import PERMISSIONS, ROLES, STAFF_ROLE_CODES, holders_of
from app.modules.identity.public import (
    IdentityDep,
    MetaDep,
    Principal,
    StaffPrincipal,
    User,
    require_permission,
)
from app.modules.operations import approvals
from app.modules.operations.api import ApprovalOut, approval_out, staff_actor
from app.modules.operations.models import ActionType
from app.modules.pricing.public import (
    FeeRule,
    FeeValues,
    LimitRule,
    LimitValues,
    fee_values,
    limit_values,
    list_fee_rules,
    list_limit_rules,
    propose_fee,
    propose_limit,
)
from app.modules.transactions.public import staff_refund

router = APIRouter(prefix="/api/v1/admin", tags=["admin-changes"])

CAN_PROPOSE_REFUND = require_permission("refund:propose:any")
CAN_PROPOSE_CONFIG = require_permission("config:propose:any")
CAN_ASSIGN_ROLES = require_permission("role:assign:any")
CONFIG_READERS = frozenset({"config:propose:any", "config:approve:any"})


async def config_reader(principal: StaffPrincipal) -> Principal:
    if not principal.permissions & CONFIG_READERS:
        raise ForbiddenError()
    return principal


CAN_READ_CONFIG = Depends(config_reader)


# ---------------------------------------------------------------- staff refunds


class RefundProposalIn(BaseModel):
    amount: str = Field(description="Integer minor units as a string", max_length=19)
    reason: str = Field(min_length=10, max_length=500)


class RefundProposedOut(BaseModel):
    approval: ApprovalOut
    refund_reference: str


@router.post(
    "/transactions/{reference}/refund-requests", response_model=RefundProposedOut, status_code=201
)
async def propose_refund(
    *,
    principal: Principal = CAN_PROPOSE_REFUND,
    reference: str,
    body: RefundProposalIn,
    response: Response,
    ctx: IdentityDep,
    store: IdempotencyDep,
    key: IdempotencyKey,
    meta: MetaDep,
) -> dict[str, Any]:
    try:
        amount = Money.from_api(body.amount, "VND")
    except MoneyError as exc:
        raise ValidationFailedError(
            errors=[FieldError("amount", "INVALID_AMOUNT", str(exc))]
        ) from exc
    if amount.amount_minor <= 0:
        raise ValidationFailedError(errors=[FieldError("amount", "NOT_POSITIVE", "must be > 0")])

    async def operation(session: Any) -> OperationResult:
        original, refund = await staff_refund.propose(
            session,
            ctx.clock,
            reference=reference,
            amount=amount,
            reason=body.reason,
            maker_id=principal.user_id,
            idempotency_key=key,
        )
        request = await approvals.propose(
            session,
            ctx.clock,
            action_type=ActionType.REFUND,
            target_type="transaction",
            target_id=str(refund.id),
            payload={
                "original_reference": original.reference,
                "refund_reference": refund.reference,
                "amount": str(refund.amount_minor),
                "currency": refund.currency,
                "payment_amount": str(original.amount_minor),
                "already_refunded": str(original.refunded_amount_minor),
                "merchant": (original.counterparty_snapshot or {}).get("name"),
            },
            maker=staff_actor(principal, meta.ip, meta.user_agent),
            reason=body.reason,
        )
        out = RefundProposedOut(
            approval=approval_out(request, principal), refund_reference=refund.reference
        )
        return OperationResult(201, out.model_dump(mode="json"), str(request.id))

    outcome = await store.execute(
        ctx.db,
        principal_id=str(principal.user_id),
        scope="admin.refunds.create",
        key=key,
        payload={"reference": reference, **body.model_dump(mode="json")},
        operation=operation,
    )
    response.status_code = outcome.status_code
    if outcome.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return outcome.body


# ---------------------------------------------------------------- fees and limits


class FeeRuleOut(BaseModel):
    id: uuid.UUID
    code: str
    txn_type: str
    currency: str
    scope: str
    scope_value: str | None
    fixed: str
    bps: int
    min_fee: str
    max_fee: str | None
    version: int
    status: str
    effective_from: datetime


class LimitRuleOut(BaseModel):
    id: uuid.UUID
    code: str
    txn_type: str
    direction: str
    scope: str
    scope_value: str | None
    period: str
    currency: str
    max_amount: str | None
    max_count: int | None
    min_amount: str | None
    version: int
    status: str


class FeeVersionIn(BaseModel):
    fixed: str = Field(pattern=r"^[0-9]{1,15}$")
    bps: int = Field(ge=0, le=10_000)
    min_fee: str = Field(pattern=r"^[0-9]{1,15}$")
    max_fee: str | None = Field(default=None, pattern=r"^[0-9]{1,15}$")
    reason: str = Field(min_length=10, max_length=500)


class LimitVersionIn(BaseModel):
    max_amount: str | None = Field(default=None, pattern=r"^[0-9]{1,15}$")
    max_count: int | None = Field(default=None, ge=0, le=1_000_000)
    min_amount: str | None = Field(default=None, pattern=r"^[0-9]{1,15}$")
    reason: str = Field(min_length=10, max_length=500)


def _opt(value: int | None) -> str | None:
    return None if value is None else str(value)


def _int(value: str | None) -> int | None:
    return None if value is None else int(value)


def _fee_out(rule: FeeRule) -> FeeRuleOut:
    return FeeRuleOut(
        id=rule.id,
        code=rule.code,
        txn_type=rule.txn_type,
        currency=rule.currency,
        scope=rule.scope,
        scope_value=rule.scope_value,
        fixed=str(rule.fixed_minor),
        bps=rule.bps,
        min_fee=str(rule.min_fee_minor),
        max_fee=_opt(rule.max_fee_minor),
        version=rule.version,
        status=rule.status,
        effective_from=rule.effective_from,
    )


def _limit_out(rule: LimitRule) -> LimitRuleOut:
    return LimitRuleOut(
        id=rule.id,
        code=rule.code,
        txn_type=rule.txn_type,
        direction=rule.direction,
        scope=rule.scope,
        scope_value=rule.scope_value,
        period=rule.period,
        currency=rule.currency,
        max_amount=_opt(rule.max_amount_minor),
        max_count=rule.max_count,
        min_amount=_opt(rule.min_amount_minor),
        version=rule.version,
        status=rule.status,
    )


def _stringify(values: dict[str, int | None]) -> dict[str, str | None]:
    return {k: _opt(v) for k, v in values.items()}


@router.get("/config/fees", response_model=list[FeeRuleOut])
async def get_fee_rules(
    ctx: IdentityDep, _principal: Principal = CAN_READ_CONFIG
) -> list[FeeRuleOut]:
    async with ctx.db.transaction() as session:
        return [_fee_out(r) for r in await list_fee_rules(session)]


@router.get("/config/limits", response_model=list[LimitRuleOut])
async def get_limit_rules(
    ctx: IdentityDep, _principal: Principal = CAN_READ_CONFIG
) -> list[LimitRuleOut]:
    async with ctx.db.transaction() as session:
        return [_limit_out(r) for r in await list_limit_rules(session)]


@router.post("/config/fees/{code}/versions", response_model=ApprovalOut, status_code=201)
async def propose_fee_version(
    code: str,
    body: FeeVersionIn,
    ctx: IdentityDep,
    meta: MetaDep,
    principal: Principal = CAN_PROPOSE_CONFIG,
) -> ApprovalOut:
    values = FeeValues(int(body.fixed), body.bps, int(body.min_fee), _int(body.max_fee))
    async with ctx.db.transaction() as session:
        active, draft = await propose_fee(
            session, ctx.clock, code, values, maker_id=principal.user_id
        )
        request = await approvals.propose(
            session,
            ctx.clock,
            action_type=ActionType.FEE_RULE_CHANGE,
            target_type="fee_rule",
            target_id=code,
            payload={
                "rule_id": str(draft.id),
                "code": code,
                "txn_type": active.txn_type,
                "from_version": active.version,
                "to_version": draft.version,
                "before": _stringify(fee_values(active)),
                "after": _stringify(fee_values(draft)),
            },
            maker=staff_actor(principal, meta.ip, meta.user_agent),
            reason=body.reason,
        )
        return approval_out(request, principal)


@router.post("/config/limits/{code}/versions", response_model=ApprovalOut, status_code=201)
async def propose_limit_version(
    code: str,
    body: LimitVersionIn,
    ctx: IdentityDep,
    meta: MetaDep,
    principal: Principal = CAN_PROPOSE_CONFIG,
) -> ApprovalOut:
    values = LimitValues(_int(body.max_amount), body.max_count, _int(body.min_amount))
    async with ctx.db.transaction() as session:
        active, draft = await propose_limit(
            session, ctx.clock, code, values, maker_id=principal.user_id
        )
        request = await approvals.propose(
            session,
            ctx.clock,
            action_type=ActionType.LIMIT_RULE_CHANGE,
            target_type="limit_rule",
            target_id=code,
            payload={
                "rule_id": str(draft.id),
                "code": code,
                "txn_type": active.txn_type,
                "from_version": active.version,
                "to_version": draft.version,
                "before": _stringify(limit_values(active)),
                "after": _stringify(limit_values(draft)),
            },
            maker=staff_actor(principal, meta.ip, meta.user_agent),
            reason=body.reason,
        )
        return approval_out(request, principal)


# ---------------------------------------------------------------- roles


class PermissionOut(BaseModel):
    code: str
    description: str
    sensitive: bool


class RoleOut(BaseModel):
    code: str
    name: str
    is_staff: bool
    permissions: list[str]


class RoleCatalogOut(BaseModel):
    roles: list[RoleOut]
    permissions: list[PermissionOut]


class StaffMemberOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    status: str
    roles: list[str]
    last_login_at: datetime | None


class RoleChangeIn(BaseModel):
    role: str = Field(max_length=30)
    op: Literal["GRANT", "REVOKE"]
    reason: str = Field(min_length=10, max_length=500)


@router.get("/roles", response_model=RoleCatalogOut)
async def role_catalog(_principal: Principal = CAN_ASSIGN_ROLES) -> RoleCatalogOut:
    return RoleCatalogOut(
        roles=[
            RoleOut(code=r.code, name=r.name, is_staff=r.is_staff, permissions=list(r.permissions))
            for r in ROLES
        ],
        permissions=[
            PermissionOut(code=p.code, description=p.description, sensitive=p.sensitive)
            for p in PERMISSIONS
        ],
    )


@router.get("/staff", response_model=list[StaffMemberOut])
async def staff_members(
    ctx: IdentityDep, _principal: Principal = CAN_ASSIGN_ROLES
) -> list[StaffMemberOut]:
    async with ctx.db.transaction() as session:
        staff = (
            await session.scalars(
                select(User).where(User.user_type == "STAFF").order_by(User.email)
            )
        ).all()
        holders = await holders_of(session, STAFF_ROLE_CODES)
    return [
        StaffMemberOut(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            status=u.status,
            roles=sorted(holders.get(u.id, set())),
            last_login_at=u.last_login_at,
        )
        for u in staff
    ]


@router.post("/staff/{user_id}/role-changes", response_model=ApprovalOut, status_code=201)
async def propose_role_change(
    user_id: uuid.UUID,
    body: RoleChangeIn,
    ctx: IdentityDep,
    meta: MetaDep,
    principal: Principal = CAN_ASSIGN_ROLES,
) -> ApprovalOut:
    if body.role not in STAFF_ROLE_CODES:
        raise ValidationFailedError(errors=[FieldError("role", "UNKNOWN_ROLE", "not a staff role")])
    if user_id == principal.user_id:
        raise approvals.SelfApprovalError("you cannot propose a change to your own roles")
    async with ctx.db.transaction() as session:
        if body.role == approvals.SUPER_ADMIN:
            held = await holders_of(session, frozenset({approvals.SUPER_ADMIN}))
            if principal.user_id not in held:
                raise approvals.SuperAdminOnlyError()
        user = await session.get(User, user_id)
        if user is None or user.user_type != "STAFF":
            raise NotFoundError()
        request = await approvals.propose(
            session,
            ctx.clock,
            action_type=ActionType.ROLE_CHANGE,
            target_type="user_role",
            target_id=f"{user_id}:{body.role}",
            payload={
                "user_id": str(user_id),
                "email": user.email,
                "role": body.role,
                "op": body.op,
            },
            maker=staff_actor(principal, meta.ip, meta.user_agent),
            reason=body.reason,
        )
        return approval_out(request, principal)
