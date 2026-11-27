"""Customer transaction history API and staff transaction lookup."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from app.core.idempotency import IdempotencyDep, IdempotencyKey, OperationResult
from app.modules.identity.public import (
    CustomerPrincipal,
    IdentityDep,
    Principal,
    require_permission,
)
from app.modules.transactions import service
from app.modules.transactions.categories import (
    CATEGORIES,
    default_category,
    overrides_for,
    set_category,
)
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.exports import (
    ExportJob,
    ExportNotReadyError,
    check_range,
    create_job,
    get_job,
)
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


class CategoryOut(BaseModel):
    code: str
    name: str


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
    category: CategoryOut = Field(description="Default for this side, or the customer's own")
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
    refunded: Amount
    related_reference: str | None
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


def category_out(code: str, locale: str) -> CategoryOut:
    names = {c.code: c for c in CATEGORIES}
    category = names.get(code, names["OTHER"])
    return CategoryOut(code=category.code, name=category.en if locale == "en" else category.vi)


def customer_view(
    txn: Transaction, wallet_id: uuid.UUID, *, category: str | None = None, locale: str = "vi"
) -> TransactionOut:
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
        category=category_out(category or default_category(txn, outgoing), locale),
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
    direction: Literal["IN", "OUT"] | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    min_amount: int | None = Query(default=None, ge=0, le=9_000_000_000_000),
    max_amount: int | None = Query(default=None, ge=0, le=9_000_000_000_000),
    q: str | None = Query(default=None, min_length=2, max_length=60),
) -> TransactionPageOut:
    tz = ZoneInfo(ctx.settings.business_timezone)
    flt = service.HistoryFilter(
        txn_type=type,
        status=status,
        direction=direction,
        # Dates are calendar days in the business time zone, "to" included.
        created_from=datetime.combine(date_from, time.min, tz) if date_from else None,
        created_to=datetime.combine(date_to + timedelta(days=1), time.min, tz) if date_to else None,
        min_amount=min_amount,
        max_amount=max_amount,
        query=q,
    )
    async with ctx.db.transaction() as session:
        wallet = await ensure_wallet(session, ctx.clock, principal.user_id)
        page = await service.list_for_wallet(
            session, wallet.id, limit=limit, cursor=cursor, flt=flt
        )
        overrides = await overrides_for(session, principal.user_id, [t.id for t in page.items])
    return TransactionPageOut(
        items=[
            customer_view(t, wallet.id, category=overrides.get(t.id), locale=principal.locale)
            for t in page.items
        ],
        next_cursor=page.next_cursor,
    )


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(principal: CustomerPrincipal) -> list[CategoryOut]:
    return [category_out(c.code, principal.locale) for c in CATEGORIES]


class ExportIn(BaseModel):
    date_from: date
    date_to: date


class ExportOut(BaseModel):
    id: uuid.UUID
    status: Literal["PENDING", "READY", "FAILED", "EXPIRED"]
    date_from: date
    date_to: date
    row_count: int | None
    truncated: bool = Field(description="More than 10 000 rows: only the first ones are in")
    created_at: datetime
    expires_at: datetime
    download_url: str | None


def _export_out(job: ExportJob) -> ExportOut:
    return ExportOut(
        id=job.id,
        status=job.status,  # type: ignore[arg-type]
        date_from=job.date_from,
        date_to=job.date_to,
        row_count=job.row_count,
        truncated=job.truncated,
        created_at=job.created_at,
        expires_at=job.expires_at,
        download_url=f"/api/v1/transactions/exports/{job.id}/download"
        if job.status == "READY"
        else None,
    )


@router.post("/exports", response_model=ExportOut, status_code=202)
async def create_export(
    *,
    principal: CustomerPrincipal,
    body: ExportIn,
    response: Response,
    ctx: IdentityDep,
    store: IdempotencyDep,
    key: IdempotencyKey,
) -> dict[str, Any]:
    check_range(body.date_from, body.date_to)

    async def operation(session: Any) -> OperationResult:
        wallet = await ensure_wallet(session, ctx.clock, principal.user_id)
        job = await create_job(
            session,
            ctx.clock,
            user_id=principal.user_id,
            wallet_id=wallet.id,
            date_from=body.date_from,
            date_to=body.date_to,
            locale=principal.locale,
        )
        return OperationResult(202, _export_out(job).model_dump(mode="json"), str(job.id))

    outcome = await store.execute(
        ctx.db,
        principal_id=str(principal.user_id),
        scope="transactions.exports",
        key=key,
        payload=body.model_dump(mode="json"),
        operation=operation,
    )
    response.status_code = outcome.status_code
    if outcome.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return outcome.body


@router.get("/exports/{export_id}", response_model=ExportOut)
async def get_export(
    export_id: uuid.UUID, principal: CustomerPrincipal, ctx: IdentityDep
) -> ExportOut:
    async with ctx.db.transaction() as session:
        return _export_out(await get_job(session, principal.user_id, export_id))


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: uuid.UUID, principal: CustomerPrincipal, ctx: IdentityDep
) -> Response:
    async with ctx.db.transaction() as session:
        job = await get_job(session, principal.user_id, export_id)
    if job.status != "READY" or job.content is None:
        raise ExportNotReadyError()
    name = f"viewallet-{job.date_from.isoformat()}-{job.date_to.isoformat()}.csv"
    return Response(
        # UTF-8 with BOM so spreadsheet programs read Vietnamese correctly.
        content="\ufeff" + job.content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{reference}", response_model=TransactionDetailOut)
async def get_my_transaction(
    reference: str, principal: CustomerPrincipal, ctx: IdentityDep
) -> TransactionDetailOut:
    async with ctx.db.transaction() as session:
        wallet = await ensure_wallet(session, ctx.clock, principal.user_id)
        txn = await service.get_for_wallet(session, wallet.id, reference)
        events = await service.events_of(session, txn.id)
        overrides = await overrides_for(session, principal.user_id, [txn.id])
    return TransactionDetailOut(
        **customer_view(
            txn, wallet.id, category=overrides.get(txn.id), locale=principal.locale
        ).model_dump(),
        events=[event_out(e) for e in events],
    )


class CategoryIn(BaseModel):
    category_code: str = Field(max_length=20)


@router.patch("/{reference}/category", response_model=TransactionDetailOut)
async def change_category(
    reference: str, body: CategoryIn, principal: CustomerPrincipal, ctx: IdentityDep
) -> TransactionDetailOut:
    async with ctx.db.transaction() as session:
        wallet = await ensure_wallet(session, ctx.clock, principal.user_id)
        txn = await service.get_for_wallet(session, wallet.id, reference)
        await set_category(
            session,
            user_id=principal.user_id,
            transaction_id=txn.id,
            code=body.category_code,
            now=ctx.clock.now(),
        )
    return await get_my_transaction(reference, principal, ctx)


@admin_router.get("/{reference}", response_model=AdminTransactionOut)
async def admin_get_transaction(
    reference: str, ctx: IdentityDep, _principal: Principal = CAN_READ_TRANSACTIONS
) -> AdminTransactionOut:
    async with ctx.db.transaction() as session:
        txn = await service.get_by_reference(session, reference)
        events = await service.events_of(session, txn.id)
        related = (
            await service.get(session, txn.related_transaction_id)
            if txn.related_transaction_id
            else None
        )
    return AdminTransactionOut(
        id=txn.id,
        reference=txn.reference,
        type=TxnType(txn.type),
        status=TxnStatus(txn.status),
        amount=_amount(txn.amount_minor, txn.currency),
        fee=_amount(txn.fee_minor, txn.currency),
        held=_amount(txn.held_minor, txn.currency),
        refunded=_amount(txn.refunded_amount_minor, txn.currency),
        related_reference=related.reference if related else None,
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
