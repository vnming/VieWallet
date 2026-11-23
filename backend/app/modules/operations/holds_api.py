"""Admin API: reversal proposals (maker-checker) and risk holds (api-spec.md §13)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.core.idempotency import IdempotencyDep, IdempotencyKey, OperationResult
from app.modules.identity.public import (
    IdentityDep,
    MetaDep,
    Principal,
    require_permission,
)
from app.modules.operations import approvals
from app.modules.operations.api import ApprovalOut, approval_out, staff_actor
from app.modules.operations.models import ActionType
from app.modules.transactions.public import FinanceContext, Transaction, reversal, review

router = APIRouter(prefix="/api/v1/admin", tags=["admin-holds-reversals"])

CAN_PROPOSE_REVERSAL = require_permission("reversal:propose:any")
CAN_DECIDE_HOLDS = require_permission("risk_hold:decide:any")  # sensitive: fresh MFA


def finance(request: Request) -> FinanceContext:
    ctx: FinanceContext = request.app.state.finance
    return ctx


FinanceDep = Annotated[FinanceContext, Depends(finance)]


class ReversalIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


class ReversalCreatedOut(BaseModel):
    approval: ApprovalOut
    reversal_reference: str


class HoldOut(BaseModel):
    reference: str
    type: str
    status: str
    amount: str
    fee: str
    held: str
    currency: str
    source_wallet_id: uuid.UUID | None
    destination_wallet_id: uuid.UUID | None
    counterparty: dict[str, Any] | None
    confirmed_at: datetime | None
    created_at: datetime


class HoldDecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class HoldRejectIn(BaseModel):
    note: str = Field(min_length=5, max_length=500)


def _hold_out(txn: Transaction) -> HoldOut:
    return HoldOut(
        reference=txn.reference,
        type=txn.type,
        status=txn.status,
        amount=str(txn.amount_minor),
        fee=str(txn.fee_minor),
        held=str(txn.held_minor),
        currency=txn.currency,
        source_wallet_id=txn.source_wallet_id,
        destination_wallet_id=txn.destination_wallet_id,
        counterparty=txn.counterparty_snapshot,
        confirmed_at=txn.confirmed_at,
        created_at=txn.created_at,
    )


@router.post(
    "/transactions/{reference}/reversals", response_model=ReversalCreatedOut, status_code=201
)
async def propose_reversal(
    *,
    principal: Principal = CAN_PROPOSE_REVERSAL,
    reference: str,
    body: ReversalIn,
    response: Response,
    ctx: IdentityDep,
    store: IdempotencyDep,
    key: IdempotencyKey,
    meta: MetaDep,
) -> dict[str, Any]:
    async def operation(session: Any) -> OperationResult:
        original, txn = await reversal.propose(
            session,
            ctx.clock,
            reference=reference,
            maker_id=principal.user_id,
            reason=body.reason,
            idempotency_key=key,
        )
        request = await approvals.propose(
            session,
            ctx.clock,
            action_type=ActionType.REVERSAL,
            target_type="transaction",
            target_id=str(original.id),
            payload={
                "original_reference": original.reference,
                "original_type": original.type,
                "reversal_transaction_id": str(txn.id),
                "reversal_reference": txn.reference,
                "amount": str(original.amount_minor),
                "fee": str(original.fee_minor),
                "currency": original.currency,
            },
            maker=staff_actor(principal, meta.ip, meta.user_agent),
            reason=body.reason,
        )
        out = ReversalCreatedOut(
            approval=approval_out(request, principal), reversal_reference=txn.reference
        )
        return OperationResult(201, out.model_dump(mode="json"), str(request.id))

    outcome = await store.execute(
        ctx.db,
        principal_id=str(principal.user_id),
        scope="admin.reversals.create",
        key=key,
        payload={"reference": reference, **body.model_dump(mode="json")},
        operation=operation,
    )
    response.status_code = outcome.status_code
    if outcome.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return outcome.body


@router.get("/risk/holds", response_model=list[HoldOut])
async def list_holds(ctx: FinanceDep, _principal: Principal = CAN_DECIDE_HOLDS) -> list[HoldOut]:
    async with ctx.db.transaction() as session:
        return [_hold_out(t) for t in await review.list_holds(session)]


@router.post("/risk/holds/{reference}/approve", response_model=HoldOut)
async def approve_hold(
    reference: str,
    body: HoldDecisionIn,
    ctx: FinanceDep,
    meta: MetaDep,
    principal: Principal = CAN_DECIDE_HOLDS,
) -> HoldOut:
    async with ctx.db.transaction() as session:
        txn = await review.approve(
            session,
            ctx,
            reference,
            reviewer=review.Reviewer(principal.user_id, meta.ip, meta.user_agent),
            note=body.note,
        )
        return _hold_out(txn)


@router.post("/risk/holds/{reference}/reject", response_model=HoldOut)
async def reject_hold(
    reference: str,
    body: HoldRejectIn,
    ctx: FinanceDep,
    meta: MetaDep,
    principal: Principal = CAN_DECIDE_HOLDS,
) -> HoldOut:
    async with ctx.db.transaction() as session:
        txn = await review.reject(
            session,
            ctx,
            reference,
            reviewer=review.Reviewer(principal.user_id, meta.ip, meta.user_agent),
            note=body.note,
        )
        return _hold_out(txn)
