"""Admin API: risk alerts, account risk flags, risk rule versions (api-spec.md §13 "Rủi ro")."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from app.modules.identity.public import IdentityDep, MetaDep, Principal, require_permission
from app.modules.operations import approvals
from app.modules.operations.api import ApprovalOut, approval_out, staff_actor
from app.modules.operations.models import ActionType
from app.modules.risk.public import (
    FLAGS,
    AccountRiskFlag,
    RiskAlert,
    RiskRule,
    RuleProposal,
    list_alerts,
    list_flags,
    list_rules,
    propose_version,
    remove_flag,
    resolve_alert,
    set_flag,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-risk"])

CAN_MANAGE = require_permission("risk_alert:manage:any")
CAN_PROPOSE_RULE = require_permission("risk_rule:propose:any")


class AlertOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    transaction_id: uuid.UUID | None
    type: str
    severity: str
    detail: dict[str, Any]
    status: str
    assigned_to: uuid.UUID | None
    resolution_note: str | None
    created_at: datetime
    resolved_at: datetime | None


class AlertPageOut(BaseModel):
    items: list[AlertOut]
    page: int
    page_size: int
    total: int


class ResolveIn(BaseModel):
    outcome: Literal["ACKNOWLEDGED", "RESOLVED", "FALSE_POSITIVE"]
    note: str = Field(min_length=5, max_length=500)


class FlagOut(BaseModel):
    id: uuid.UUID
    flag: str
    reason: str
    set_by: uuid.UUID | None
    created_at: datetime
    expires_at: datetime | None
    removed_at: datetime | None


class FlagIn(BaseModel):
    flag: str = Field(description=f"One of {', '.join(FLAGS)}")
    reason: str = Field(min_length=5, max_length=300)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class FlagRemoveIn(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


class RuleOut(BaseModel):
    id: uuid.UUID
    code: str
    version: int
    description: str
    score: int
    block: bool
    enabled: bool
    params: dict[str, Any]
    status: str
    created_by: uuid.UUID | None
    created_at: datetime
    activated_at: datetime | None


class RuleVersionIn(BaseModel):
    score: int = Field(ge=0, le=100)
    block: bool = False
    enabled: bool = True
    params: dict[str, int] = Field(default_factory=dict)
    description: str = Field(default="", max_length=200)
    reason: str = Field(min_length=10, max_length=500)


class RuleProposedOut(BaseModel):
    rule: RuleOut
    approval: ApprovalOut


def _alert(a: RiskAlert) -> AlertOut:
    return AlertOut(
        id=a.id,
        user_id=a.user_id,
        transaction_id=a.transaction_id,
        type=a.type,
        severity=a.severity,
        detail=a.detail,
        status=a.status,
        assigned_to=a.assigned_to,
        resolution_note=a.resolution_note,
        created_at=a.created_at,
        resolved_at=a.resolved_at,
    )


def _flag(f: AccountRiskFlag) -> FlagOut:
    return FlagOut(
        id=f.id,
        flag=f.flag,
        reason=f.reason,
        set_by=f.set_by,
        created_at=f.created_at,
        expires_at=f.expires_at,
        removed_at=f.removed_at,
    )


def _rule(r: RiskRule) -> RuleOut:
    return RuleOut(
        id=r.id,
        code=r.code,
        version=r.version,
        description=r.description,
        score=r.score,
        block=r.block,
        enabled=r.enabled,
        params=r.params,
        status=r.status,
        created_by=r.created_by,
        created_at=r.created_at,
        activated_at=r.activated_at,
    )


@router.get("/risk/alerts", response_model=AlertPageOut)
async def alerts(
    ctx: IdentityDep,
    _principal: Principal = CAN_MANAGE,
    status: str | None = Query(
        default="OPEN", pattern="^(OPEN|ACKNOWLEDGED|RESOLVED|FALSE_POSITIVE)$"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> AlertPageOut:
    async with ctx.db.transaction() as session:
        rows, total = await list_alerts(session, status=status, page=page, page_size=page_size)
    return AlertPageOut(
        items=[_alert(a) for a in rows], page=page, page_size=page_size, total=total
    )


@router.post("/risk/alerts/{alert_id}/resolve", response_model=AlertOut)
async def resolve(
    alert_id: uuid.UUID, body: ResolveIn, ctx: IdentityDep, principal: Principal = CAN_MANAGE
) -> AlertOut:
    async with ctx.db.transaction() as session:
        row = await resolve_alert(
            session,
            ctx.clock,
            alert_id,
            actor_id=principal.user_id,
            outcome=body.outcome,
            note=body.note,
        )
        return _alert(row)


@router.get("/users/{user_id}/risk-flags", response_model=list[FlagOut])
async def user_flags(
    user_id: uuid.UUID, ctx: IdentityDep, _principal: Principal = CAN_MANAGE
) -> list[FlagOut]:
    async with ctx.db.transaction() as session:
        return [_flag(f) for f in await list_flags(session, user_id)]


@router.post("/users/{user_id}/risk-flags", response_model=FlagOut, status_code=201)
async def add_flag(
    user_id: uuid.UUID, body: FlagIn, ctx: IdentityDep, principal: Principal = CAN_MANAGE
) -> FlagOut:
    expires = (
        ctx.clock.now() + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    )
    async with ctx.db.transaction() as session:
        row = await set_flag(
            session,
            ctx.clock,
            user_id=user_id,
            flag=body.flag,
            reason=body.reason,
            actor_id=principal.user_id,
            expires_at=expires,
        )
        return _flag(row)


@router.delete("/users/{user_id}/risk-flags/{flag}", status_code=204)
async def delete_flag(
    user_id: uuid.UUID,
    flag: str,
    body: FlagRemoveIn,
    ctx: IdentityDep,
    principal: Principal = CAN_MANAGE,
) -> Response:
    async with ctx.db.transaction() as session:
        await remove_flag(
            session,
            ctx.clock,
            user_id=user_id,
            flag=flag,
            actor_id=principal.user_id,
            reason=body.reason,
        )
    return Response(status_code=204)


@router.get("/risk/rules", response_model=list[RuleOut])
async def rules(ctx: IdentityDep, _principal: Principal = CAN_MANAGE) -> list[RuleOut]:
    async with ctx.db.transaction() as session:
        return [_rule(r) for r in await list_rules(session)]


@router.post("/risk/rules/{code}/versions", response_model=RuleProposedOut, status_code=201)
async def propose_rule(
    code: str,
    body: RuleVersionIn,
    ctx: IdentityDep,
    meta: MetaDep,
    principal: Principal = CAN_PROPOSE_RULE,
) -> RuleProposedOut:
    async with ctx.db.transaction() as session:
        rule = await propose_version(
            session,
            ctx.clock,
            RuleProposal(
                code=code,
                score=body.score,
                block=body.block,
                enabled=body.enabled,
                params=dict(body.params),
                description=body.description,
            ),
            maker_id=principal.user_id,
        )
        request = await approvals.propose(
            session,
            ctx.clock,
            action_type=ActionType.RISK_RULE_CHANGE,
            target_type="risk_rule",
            target_id=code,
            payload={
                "rule_id": str(rule.id),
                "code": code,
                "version": rule.version,
                "score": rule.score,
                "block": rule.block,
                "enabled": rule.enabled,
                "params": rule.params,
            },
            maker=staff_actor(principal, meta.ip, meta.user_agent),
            reason=body.reason,
        )
        return RuleProposedOut(rule=_rule(rule), approval=approval_out(request, principal))
