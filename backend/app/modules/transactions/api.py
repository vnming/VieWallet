"""Customer transaction history API and staff transaction lookup."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.modules.identity.public import (
    CustomerPrincipal,
    IdentityDep,
    Principal,
    require_permission,
)
from app.modules.transactions import service
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction, TransactionEvent
from app.modules.wallet.public import ensure_wallet

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])
admin_router = APIRouter(prefix="/api/v1/admin/transactions", tags=["admin-transactions"])

CAN_READ_TRANSACTIONS = require_permission("transaction:read:any")


class Amount(BaseModel):
    amount: str = Field(description="Integer minor units as a string")
    currency: str


class Counterparty(BaseModel):
    kind: str
    name: str | None = None
    wallet_number_masked: str | None = None
    account_masked: str | None = None


class TransactionOut(BaseModel):
    id: uuid.UUID
    reference: str
    type: TxnType
    status: TxnStatus
    direction: Literal["IN", "OUT"] = Field(description="Relative to the caller's wallet")
    amount: Amount
    fee: Amount
    total: Amount = Field(description="What moved on the caller's wallet (amount + fee if OUT)")
    description: str
    counterparty: Counterparty | None
    failure_code: str | None
    created_at: datetime
    completed_at: datetime | None


class TransactionEventOut(BaseModel):
    from_status: TxnStatus | None
    to_status: TxnStatus
    reason_code: str | None
    at: datetime


class TransactionDetailOut(TransactionOut):
    events: list[TransactionEventOut]


class TransactionPageOut(BaseModel):
    items: list[TransactionOut]
    next_cursor: uuid.UUID | None


class AdminTransactionEventOut(TransactionEventOut):
    actor_type: str
    actor_id: uuid.UUID | None
    message: str | None
    correlation_id: str | None


class AdminTransactionOut(BaseModel):
    id: uuid.UUID
    reference: str
    type: TxnType
    status: TxnStatus
    amount: Amount
    fee: Amount
    held: Amount
    initiator_user_id: uuid.UUID
    source_wallet_id: uuid.UUID | None
    destination_wallet_id: uuid.UUID | None
    description: str
    metadata: dict[str, Any]
    failure_code: str | None
    risk_decision: str | None
    created_at: datetime
    completed_at: datetime | None
    version: int
    events: list[AdminTransactionEventOut]


def _amount(value: int, currency: str) -> Amount:
    return Amount(amount=str(value), currency=currency)


def customer_view(txn: Transaction, wallet_id: uuid.UUID) -> TransactionOut:
    outgoing = txn.source_wallet_id == wallet_id
    total = txn.amount_minor + txn.fee_minor if outgoing else txn.amount_minor
    # counterparty_snapshot is what the paying side sees; source_party what the receiver sees.
    snapshot = (
        txn.counterparty_snapshot if outgoing else txn.extra.get("source_party")
    ) or txn.counterparty_snapshot
    return TransactionOut(
        id=txn.id,
        reference=txn.reference,
        type=TxnType(txn.type),
        status=TxnStatus(txn.status),
        direction="OUT" if outgoing else "IN",
        amount=_amount(txn.amount_minor, txn.currency),
        fee=_amount(txn.fee_minor if outgoing else 0, txn.currency),
        total=_amount(total, txn.currency),
        description=txn.description,
        counterparty=Counterparty(**snapshot) if snapshot else None,
        failure_code=txn.failure_code,
        created_at=txn.created_at,
        completed_at=txn.completed_at,
    )


def event_out(e: TransactionEvent) -> TransactionEventOut:
    return TransactionEventOut(
        from_status=TxnStatus(e.from_status) if e.from_status else None,
        to_status=TxnStatus(e.to_status),
        reason_code=e.reason_code,
        at=e.at,
    )


@router.get("", response_model=TransactionPageOut)
async def list_my_transactions(
    *,
    principal: CustomerPrincipal,
    ctx: IdentityDep,
    cursor: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    type: TxnType | None = None,  # noqa: A002 - public query parameter name
    status: TxnStatus | None = None,
) -> TransactionPageOut:
    async with ctx.db.transaction() as session:
        wallet = await ensure_wallet(session, ctx.clock, principal.user_id)
        page = await service.list_for_wallet(
            session, wallet.id, limit=limit, cursor=cursor, txn_type=type, status=status
        )
    return TransactionPageOut(
        items=[customer_view(t, wallet.id) for t in page.items], next_cursor=page.next_cursor
    )


@router.get("/{reference}", response_model=TransactionDetailOut)
async def get_my_transaction(
    reference: str, principal: CustomerPrincipal, ctx: IdentityDep
) -> TransactionDetailOut:
    async with ctx.db.transaction() as session:
        wallet = await ensure_wallet(session, ctx.clock, principal.user_id)
        txn = await service.get_for_wallet(session, wallet.id, reference)
        events = await service.events_of(session, txn.id)
    return TransactionDetailOut(
        **customer_view(txn, wallet.id).model_dump(), events=[event_out(e) for e in events]
    )


@admin_router.get("/{reference}", response_model=AdminTransactionOut)
async def admin_get_transaction(
    reference: str, ctx: IdentityDep, _principal: Principal = CAN_READ_TRANSACTIONS
) -> AdminTransactionOut:
    async with ctx.db.transaction() as session:
        txn = await service.get_by_reference(session, reference)
        events = await service.events_of(session, txn.id)
    return AdminTransactionOut(
        id=txn.id,
        reference=txn.reference,
        type=TxnType(txn.type),
        status=TxnStatus(txn.status),
        amount=_amount(txn.amount_minor, txn.currency),
        fee=_amount(txn.fee_minor, txn.currency),
        held=_amount(txn.held_minor, txn.currency),
        initiator_user_id=txn.initiator_user_id,
        source_wallet_id=txn.source_wallet_id,
        destination_wallet_id=txn.destination_wallet_id,
        description=txn.description,
        metadata=txn.extra,
        failure_code=txn.failure_code,
        risk_decision=txn.risk_decision,
        created_at=txn.created_at,
        completed_at=txn.completed_at,
        version=txn.version,
        events=[
            AdminTransactionEventOut(
                **event_out(e).model_dump(),
                actor_type=e.actor_type,
                actor_id=e.actor_id,
                message=e.message,
                correlation_id=e.correlation_id,
            )
            for e in events
        ],
    )
