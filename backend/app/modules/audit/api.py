"""Admin audit-log API (read-only)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.modules.audit.service import list_entries, verify_chain
from app.modules.identity.public import IdentityDep, Principal, require_permission

router = APIRouter(prefix="/api/v1/admin/audit-logs", tags=["admin-audit"])

CAN_READ_AUDIT = require_permission("audit:read:any")
CAN_VERIFY_AUDIT = require_permission("audit:verify:any")


class AuditLogItem(BaseModel):
    id: uuid.UUID
    seq: int
    at: datetime
    actor_type: str
    actor_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    result: str
    ip: str | None
    correlation_id: str | None
    details: dict[str, object] | None


class AuditLogPage(BaseModel):
    items: list[AuditLogItem]
    page: int
    page_size: int
    total: int


class ChainVerificationResponse(BaseModel):
    ok: bool
    checked: int
    first_broken_seq: int | None


@router.get("", response_model=AuditLogPage)
async def list_audit_logs(
    *,
    ctx: IdentityDep,
    _principal: Principal = CAN_READ_AUDIT,
    action: str | None = Query(default=None, max_length=100),
    actor_id: uuid.UUID | None = None,
    target_type: str | None = Query(default=None, max_length=50),
    target_id: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> AuditLogPage:
    async with ctx.db.transaction() as session:
        result = await list_entries(
            session,
            action=action,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            page=page,
            page_size=page_size,
        )
    return AuditLogPage(
        items=[
            AuditLogItem(
                id=r.id,
                seq=r.seq,
                at=r.at,
                actor_type=r.actor_type,
                actor_id=r.actor_id,
                action=r.action,
                target_type=r.target_type,
                target_id=r.target_id,
                result=r.result,
                ip=r.ip,
                correlation_id=r.correlation_id,
                details=r.details,
            )
            for r in result.items
        ],
        page=page,
        page_size=page_size,
        total=result.total,
    )


@router.get("/verify", response_model=ChainVerificationResponse)
async def verify_audit_chain(
    ctx: IdentityDep, _principal: Principal = CAN_VERIFY_AUDIT
) -> ChainVerificationResponse:
    async with ctx.db.transaction() as session:
        result = await verify_chain(session)
    return ChainVerificationResponse(
        ok=result.ok, checked=result.checked, first_broken_seq=result.first_broken_seq
    )
