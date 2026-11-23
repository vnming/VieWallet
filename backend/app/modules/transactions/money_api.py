"""Deposit and withdrawal API (api-spec.md §7)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.errors import FieldError, ValidationFailedError
from app.core.idempotency import IdempotencyKey
from app.core.money import Money, MoneyError
from app.modules.identity.public import CustomerPrincipal, MetaDep
from app.modules.transactions import service
from app.modules.transactions.api import Amount, TransactionEventOut, event_out
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.transactions.use_cases import deposit, withdrawal
from app.modules.transactions.use_cases.common import FinanceContext

router = APIRouter(prefix="/api/v1", tags=["money-movement"])


def finance(request: Request) -> FinanceContext:
    ctx: FinanceContext = request.app.state.finance
    return ctx


FinanceDep = Annotated[FinanceContext, Depends(finance)]

NextAction = Literal["NONE", "REDIRECT", "WAIT_PROVIDER", "WAIT_REVIEW"]


class DestinationSnapshot(BaseModel):
    bank_code: str
    name: str
    account_masked: str
    account_name: str


class MoneyFlowOut(BaseModel):
    id: uuid.UUID
    reference: str
    type: TxnType
    status: TxnStatus
    amount: Amount
    fee: Amount
    total: Amount = Field(description="Amount + fee for withdrawals; the amount for deposits")
    failure_code: str | None
    next_action: NextAction
    checkout_url: str | None = Field(default=None, description="SimBank page (deposits)")
    quote_expires_at: datetime | None
    destination: DestinationSnapshot | None
    created_at: datetime
    completed_at: datetime | None
    events: list[TransactionEventOut]


class DepositIn(BaseModel):
    amount: str = Field(max_length=19, description="Integer minor units as a string")
    method: Literal["SIMBANK"] = "SIMBANK"


class WithdrawalIn(BaseModel):
    destination_id: uuid.UUID
    amount: str = Field(max_length=19, description="Integer minor units as a string")


class ConfirmIn(BaseModel):
    pin: str = Field(min_length=6, max_length=6)


def _money(value: str) -> Money:
    try:
        return Money.from_api(value, "VND")
    except MoneyError as exc:
        raise ValidationFailedError(
            errors=[FieldError("amount", "INVALID_AMOUNT", str(exc))]
        ) from exc


def _next_action(txn: Transaction) -> NextAction:
    if txn.status == TxnStatus.PENDING.value:
        if txn.type == TxnType.DEPOSIT.value and txn.extra.get("checkout_url"):
            return "REDIRECT"
        if txn.risk_decision == "REVIEW":
            return "WAIT_REVIEW"
        return "WAIT_PROVIDER"
    if txn.status == TxnStatus.PROCESSING.value:
        return "WAIT_PROVIDER"
    return "NONE"


async def _out(ctx: FinanceContext, txn: Transaction) -> MoneyFlowOut:
    async with ctx.db.transaction() as session:
        events = await service.events_of(session, txn.id)
    outgoing = txn.type == TxnType.WITHDRAWAL.value
    destination = txn.extra.get("destination")
    pending = txn.status == TxnStatus.PENDING.value
    return MoneyFlowOut(
        id=txn.id,
        reference=txn.reference,
        type=TxnType(txn.type),
        status=TxnStatus(txn.status),
        amount=Amount(amount=str(txn.amount_minor), currency=txn.currency),
        fee=Amount(amount=str(txn.fee_minor), currency=txn.currency),
        total=Amount(
            amount=str(txn.amount_minor + (txn.fee_minor if outgoing else 0)), currency=txn.currency
        ),
        failure_code=txn.failure_code,
        next_action=_next_action(txn),
        checkout_url=txn.extra.get("checkout_url") if pending else None,
        quote_expires_at=txn.quote_expires_at,
        destination=DestinationSnapshot(**destination) if destination else None,
        created_at=txn.created_at,
        completed_at=txn.completed_at,
        events=[event_out(e) for e in events],
    )


async def _mine(
    ctx: FinanceContext, user_id: uuid.UUID, reference: str, kind: TxnType
) -> Transaction:
    async with ctx.db.transaction() as session:
        txn = await service.get_by_reference(session, reference)
    if txn.initiator_user_id != user_id or txn.type != kind.value:
        raise service.TransactionNotFoundError()
    return txn


@router.post("/deposits", response_model=MoneyFlowOut, status_code=201)
async def create_deposit(
    *, principal: CustomerPrincipal, body: DepositIn, key: IdempotencyKey, ctx: FinanceDep
) -> MoneyFlowOut:
    txn = await deposit.start(
        ctx, user_id=principal.user_id, amount=_money(body.amount), method=body.method, key=key
    )
    return await _out(ctx, txn)


@router.get("/deposits/{reference}", response_model=MoneyFlowOut)
async def get_deposit(
    reference: str, principal: CustomerPrincipal, ctx: FinanceDep
) -> MoneyFlowOut:
    return await _out(ctx, await _mine(ctx, principal.user_id, reference, TxnType.DEPOSIT))


@router.post("/withdrawals", response_model=MoneyFlowOut, status_code=201)
async def quote_withdrawal(
    *, principal: CustomerPrincipal, body: WithdrawalIn, key: IdempotencyKey, ctx: FinanceDep
) -> MoneyFlowOut:
    txn = await withdrawal.quote(
        ctx,
        user_id=principal.user_id,
        destination_id=body.destination_id,
        amount=_money(body.amount),
        key=key,
    )
    return await _out(ctx, txn)


@router.post("/withdrawals/{reference}/confirm", response_model=MoneyFlowOut)
async def confirm_withdrawal(
    *,
    principal: CustomerPrincipal,
    reference: str,
    body: ConfirmIn,
    key: IdempotencyKey,
    ctx: FinanceDep,
    meta: MetaDep,
) -> MoneyFlowOut:
    txn = await withdrawal.confirm(
        ctx, user_id=principal.user_id, reference=reference, pin=body.pin, key=key, meta=meta
    )
    return await _out(ctx, txn)


@router.post("/withdrawals/{reference}/cancel", response_model=MoneyFlowOut)
async def cancel_withdrawal(
    reference: str, principal: CustomerPrincipal, ctx: FinanceDep
) -> MoneyFlowOut:
    txn = await withdrawal.cancel(ctx, user_id=principal.user_id, reference=reference)
    return await _out(ctx, txn)


@router.get("/withdrawals/{reference}", response_model=MoneyFlowOut)
async def get_withdrawal(
    reference: str, principal: CustomerPrincipal, ctx: FinanceDep
) -> MoneyFlowOut:
    return await _out(ctx, await _mine(ctx, principal.user_id, reference, TxnType.WITHDRAWAL))
