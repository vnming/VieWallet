"""Admin API: adjustments, unfreeze requests, approvals, internal reconciliation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from app.core.errors import FieldError, ForbiddenError, NotFoundError, ValidationFailedError
from app.core.idempotency import IdempotencyDep, IdempotencyKey, OperationResult
from app.core.money import Money, MoneyError
from app.modules.identity.public import (
    IdentityDep,
    MetaDep,
    Principal,
    StaffPrincipal,
    ensure_permission,
    require_permission,
)
from app.modules.operations import approvals, reconciliation, recovery
from app.modules.operations.models import (
    ActionType,
    ApprovalRequest,
    ApprovalStatus,
    ReconciliationItem,
    ReconciliationRun,
)
from app.modules.transactions.public import (
    AdjustmentDirection,
    AdjustmentReason,
    AdjustmentRequest,
    adjustment,
)
from app.modules.wallet.public import (
    WalletStateConflictError,
    WalletStatus,
    get_by_number,
    mask_wallet_number,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-operations"])

CAN_PROPOSE_ADJUSTMENT = require_permission("adjustment:propose:any")
CAN_REQUEST_UNFREEZE = require_permission("wallet:unfreeze:any")
CAN_RECONCILE = require_permission("reconciliation:manage:any")


def staff_actor(
    principal: Principal, ip: str | None, user_agent: str | None
) -> approvals.StaffActor:
    return approvals.StaffActor(principal.user_id, (), ip, user_agent)


# ---------------------------------------------------------------- schemas


class ApprovalOut(BaseModel):
    id: uuid.UUID
    action_type: ActionType
    target_type: str
    target_id: str
    payload: dict[str, Any]
    maker_id: uuid.UUID
    maker_reason: str
    status: ApprovalStatus
    checker_id: uuid.UUID | None
    checker_note: str | None
    failure_code: str | None
    created_at: datetime
    decided_at: datetime | None
    executed_at: datetime | None
    expires_at: datetime
    can_decide: bool = Field(description="The caller may approve/reject (not the maker)")


class ApprovalPageOut(BaseModel):
    items: list[ApprovalOut]
    page: int
    page_size: int
    total: int


class AdjustmentIn(BaseModel):
    wallet_number: str = Field(pattern=r"^[0-9]{10}$")
    direction: AdjustmentDirection
    amount: str = Field(description="Integer minor units as a string", max_length=19)
    reason_code: AdjustmentReason
    note: str = Field(min_length=10, max_length=500, description="Internal justification")
    description: str = Field(default="", max_length=140, description="Shown to the customer")
    ticket_ref: str | None = Field(default=None, max_length=64)


class AdjustmentCreatedOut(BaseModel):
    approval: ApprovalOut
    transaction_reference: str


class UnfreezeRequestIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


class DecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class RejectIn(BaseModel):
    note: str = Field(min_length=5, max_length=500)


class ReconciliationItemOut(BaseModel):
    id: uuid.UUID
    type: str
    invariant: str | None
    subject: str
    expected: str
    actual: str
    status: str


class ReconciliationRunOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    violations: int
    stats: dict[str, Any]
    triggered_by: uuid.UUID | None
    started_at: datetime
    finished_at: datetime | None


class ReconciliationRunDetailOut(ReconciliationRunOut):
    items: list[ReconciliationItemOut]


class ReconciliationRunPageOut(BaseModel):
    items: list[ReconciliationRunOut]
    page: int
    page_size: int
    total: int


def _can_decide(request: ApprovalRequest, principal: Principal) -> bool:
    permission = approvals.APPROVE_PERMISSION[ActionType(request.action_type)]
    return (
        request.status == ApprovalStatus.PENDING.value
        and request.maker_id != principal.user_id
        and permission in principal.permissions
    )


def approval_out(request: ApprovalRequest, principal: Principal) -> ApprovalOut:
    return ApprovalOut(
        id=request.id,
        action_type=ActionType(request.action_type),
        target_type=request.target_type,
        target_id=request.target_id,
        payload=request.payload,
        maker_id=request.maker_id,
        maker_reason=request.maker_reason,
        status=ApprovalStatus(request.status),
        checker_id=request.checker_id,
        checker_note=request.checker_note,
        failure_code=request.failure_code,
        created_at=request.created_at,
        decided_at=request.decided_at,
        executed_at=request.executed_at,
        expires_at=request.expires_at,
        can_decide=_can_decide(request, principal),
    )


def _run_out(run: ReconciliationRun) -> ReconciliationRunOut:
    return ReconciliationRunOut(
        id=run.id,
        kind=run.kind,
        status=run.status,
        violations=run.violations,
        stats=run.stats,
        triggered_by=run.triggered_by,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _item_out(item: ReconciliationItem) -> ReconciliationItemOut:
    return ReconciliationItemOut(
        id=item.id,
        type=item.type,
        invariant=item.invariant,
        subject=item.subject,
        expected=item.expected,
        actual=item.actual,
        status=item.status,
    )


# ---------------------------------------------------------------- adjustments


@router.post("/adjustments", response_model=AdjustmentCreatedOut, status_code=201)
async def propose_adjustment(
    *,
    # Authorization first: anonymous callers get 401, not a header validation error.
    principal: Principal = CAN_PROPOSE_ADJUSTMENT,
    body: AdjustmentIn,
    response: Response,
    ctx: IdentityDep,
    store: IdempotencyDep,
    key: IdempotencyKey,
    meta: MetaDep,
) -> dict[str, Any]:
    try:
        Money.from_api(body.amount, "VND")
    except MoneyError as exc:
        raise ValidationFailedError(
            errors=[FieldError("amount", "INVALID_AMOUNT", str(exc))]
        ) from exc
    if int(body.amount) <= 0:
        raise ValidationFailedError(errors=[FieldError("amount", "NOT_POSITIVE", "must be > 0")])

    async def operation(session: Any) -> OperationResult:
        wallet = await get_by_number(session, body.wallet_number)
        amount = Money.from_api(body.amount, wallet.currency)
        txn = await adjustment.propose(
            session,
            ctx.clock,
            AdjustmentRequest(
                wallet=wallet,
                direction=body.direction,
                amount=amount,
                reason=body.reason_code,
                note=body.note,
                description=body.description,
                ticket_ref=body.ticket_ref,
            ),
            maker_id=principal.user_id,
            idempotency_key=key,
        )
        request = await approvals.propose(
            session,
            ctx.clock,
            action_type=ActionType.ADJUSTMENT,
            target_type="transaction",
            target_id=str(txn.id),
            payload={
                "transaction_reference": txn.reference,
                "wallet_number_masked": mask_wallet_number(wallet.wallet_number),
                "wallet_id": str(wallet.id),
                "direction": body.direction.value,
                "amount": str(amount.amount_minor),
                "currency": amount.currency,
                "reason_code": body.reason_code.value,
                "ticket_ref": body.ticket_ref,
            },
            maker=staff_actor(principal, meta.ip, meta.user_agent),
            reason=body.note,
        )
        out = AdjustmentCreatedOut(
            approval=approval_out(request, principal), transaction_reference=txn.reference
        )
        return OperationResult(201, out.model_dump(mode="json"), str(request.id))

    outcome = await store.execute(
        ctx.db,
        principal_id=str(principal.user_id),
        scope="admin.adjustments.create",
        key=key,
        payload=body.model_dump(mode="json"),
        operation=operation,
    )
    response.status_code = outcome.status_code
    if outcome.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return outcome.body


# ---------------------------------------------------------------- wallet unfreeze


@router.post(
    "/wallets/{wallet_number}/unfreeze-requests", response_model=ApprovalOut, status_code=201
)
async def request_unfreeze(
    wallet_number: str,
    body: UnfreezeRequestIn,
    ctx: IdentityDep,
    meta: MetaDep,
    principal: Principal = CAN_REQUEST_UNFREEZE,
) -> ApprovalOut:
    async with ctx.db.transaction() as session:
        wallet = await get_by_number(session, wallet_number)
        if wallet.status != WalletStatus.FROZEN.value:
            raise WalletStateConflictError()
        request = await approvals.propose(
            session,
            ctx.clock,
            action_type=ActionType.WALLET_UNFREEZE,
            target_type="wallet",
            target_id=str(wallet.id),
            payload={
                "wallet_id": str(wallet.id),
                "wallet_number_masked": mask_wallet_number(wallet.wallet_number),
                "freeze_source": wallet.freeze_source,
            },
            maker=staff_actor(principal, meta.ip, meta.user_agent),
            reason=body.reason,
        )
        return approval_out(request, principal)


# ---------------------------------------------------------------- approvals


def _visible_action_types(principal: Principal) -> list[str]:
    return [
        a.value for a, perm in approvals.APPROVE_PERMISSION.items() if perm in principal.permissions
    ]


@router.get("/approvals", response_model=ApprovalPageOut)
async def list_approvals(
    principal: StaffPrincipal,
    ctx: IdentityDep,
    status: ApprovalStatus | None = ApprovalStatus.PENDING,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApprovalPageOut:
    types = _visible_action_types(principal)
    if not types:
        raise ForbiddenError()
    async with ctx.db.transaction() as session:
        result = await approvals.list_requests(
            session, action_types=types, status=status, page=page, page_size=page_size
        )
    return ApprovalPageOut(
        items=[approval_out(r, principal) for r in result.items],
        page=page,
        page_size=page_size,
        total=result.total,
    )


async def _visible(session: Any, approval_id: uuid.UUID, principal: Principal) -> ApprovalRequest:
    request = await approvals.get(session, approval_id)
    if request.action_type not in _visible_action_types(principal):
        raise approvals.ApprovalNotFoundError()
    return request


@router.get("/approvals/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: uuid.UUID, principal: StaffPrincipal, ctx: IdentityDep
) -> ApprovalOut:
    async with ctx.db.transaction() as session:
        return approval_out(await _visible(session, approval_id, principal), principal)


async def _check_decider(ctx: IdentityDep, approval_id: uuid.UUID, principal: Principal) -> None:
    async with ctx.db.transaction() as session:
        request = await _visible(session, approval_id, principal)
    ensure_permission(
        principal, approvals.APPROVE_PERMISSION[ActionType(request.action_type)], ctx.clock.now()
    )


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalOut)
async def approve_request(
    approval_id: uuid.UUID,
    body: DecisionIn,
    principal: StaffPrincipal,
    ctx: IdentityDep,
    meta: MetaDep,
) -> ApprovalOut:
    await _check_decider(ctx, approval_id, principal)
    async with ctx.db.transaction() as session:
        decided = await approvals.approve(
            session,
            ctx.clock,
            approval_id,
            checker=staff_actor(principal, meta.ip, meta.user_agent),
            note=body.note,
        )
        out = approval_out(decided.request, principal)
    if decided.error is not None:  # committed (audit / expiry) before raising
        raise decided.error
    return out


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalOut)
async def reject_request(
    approval_id: uuid.UUID,
    body: RejectIn,
    principal: StaffPrincipal,
    ctx: IdentityDep,
    meta: MetaDep,
) -> ApprovalOut:
    await _check_decider(ctx, approval_id, principal)
    async with ctx.db.transaction() as session:
        decided = await approvals.reject(
            session,
            ctx.clock,
            approval_id,
            checker=staff_actor(principal, meta.ip, meta.user_agent),
            note=body.note,
        )
        out = approval_out(decided.request, principal)
    if decided.error is not None:
        raise decided.error
    return out


@router.post("/approvals/{approval_id}/cancel", response_model=ApprovalOut)
async def cancel_request(
    approval_id: uuid.UUID, principal: StaffPrincipal, ctx: IdentityDep, meta: MetaDep
) -> ApprovalOut:
    async with ctx.db.transaction() as session:
        await _visible(session, approval_id, principal)
        request = await approvals.cancel(
            session, ctx.clock, approval_id, maker=staff_actor(principal, meta.ip, meta.user_agent)
        )
        return approval_out(request, principal)


# ---------------------------------------------------------------- reconciliation


@router.post("/reconciliation/runs", response_model=ReconciliationRunDetailOut, status_code=201)
async def trigger_reconciliation(
    ctx: IdentityDep, principal: Principal = CAN_RECONCILE
) -> ReconciliationRunDetailOut:
    async with ctx.db.transaction() as session:
        result = await reconciliation.run_internal(
            session, ctx.clock, triggered_by=principal.user_id
        )
    return ReconciliationRunDetailOut(
        **_run_out(result.run).model_dump(), items=[_item_out(i) for i in result.items]
    )


@router.get("/reconciliation/runs", response_model=ReconciliationRunPageOut)
async def list_reconciliation_runs(
    ctx: IdentityDep,
    _principal: Principal = CAN_RECONCILE,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ReconciliationRunPageOut:
    async with ctx.db.transaction() as session:
        result = await reconciliation.list_runs(session, page=page, page_size=page_size)
    return ReconciliationRunPageOut(
        items=[_run_out(r) for r in result.items],
        page=page,
        page_size=page_size,
        total=result.total,
    )


@router.get("/reconciliation/runs/{run_id}", response_model=ReconciliationRunDetailOut)
async def get_reconciliation_run(
    run_id: uuid.UUID, ctx: IdentityDep, _principal: Principal = CAN_RECONCILE
) -> ReconciliationRunDetailOut:
    async with ctx.db.transaction() as session:
        result = await reconciliation.get_run(session, run_id)
    if result is None:
        raise NotFoundError()
    return ReconciliationRunDetailOut(
        **_run_out(result.run).model_dump(), items=[_item_out(i) for i in result.items]
    )


# ---------------------------------------------------------------- recovery cases

CAN_READ_TRANSACTIONS = require_permission("transaction:read:any")


class RecoveryCaseOut(BaseModel):
    id: uuid.UUID
    transaction_id: uuid.UUID
    provider_operation_id: uuid.UUID | None
    reason: str
    detail: str
    status: str
    created_at: datetime


class RecoveryCasePageOut(BaseModel):
    items: list[RecoveryCaseOut]
    page: int
    page_size: int
    total: int


@router.get("/recovery-cases", response_model=RecoveryCasePageOut)
async def list_recovery_cases(
    ctx: IdentityDep,
    _principal: Principal = CAN_READ_TRANSACTIONS,
    status: str | None = Query(default="OPEN", pattern="^(OPEN|INVESTIGATING|RESOLVED)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> RecoveryCasePageOut:
    rows, total = await recovery.list_cases(ctx.db, status=status, page=page, page_size=page_size)
    return RecoveryCasePageOut(
        items=[
            RecoveryCaseOut(
                id=r.id,
                transaction_id=r.transaction_id,
                provider_operation_id=r.provider_operation_id,
                reason=r.reason,
                detail=r.detail,
                status=r.status,
                created_at=r.created_at,
            )
            for r in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )
