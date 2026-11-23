"""Transfers, QR payments, merchant payments and refunds (api-spec.md §7).

Every money-moving endpoint runs through ``IdempotencyStore.execute``: the response is stored
in the same DB transaction as the business change, and a retry with the same key returns that
exact body with ``Idempotent-Replayed: true`` (FT-02, FT-04). Business failures at confirm are
a 200 with ``status = FAILED`` and ``failure_code`` (owner decision, same as withdrawals).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FieldError, ValidationFailedError
from app.core.idempotency import IdempotencyDep, IdempotencyKey, IdempotencyStore, OperationResult
from app.core.money import Money, MoneyError
from app.modules.identity.public import CustomerPrincipal, MetaDep
from app.modules.payments.public import merchant_of
from app.modules.transactions import service
from app.modules.transactions.api import Amount, Counterparty, TransactionEventOut, event_out
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.transactions.money_api import ConfirmIn, FinanceDep
from app.modules.transactions.use_cases import refund, transfer
from app.modules.transactions.use_cases.common import FinanceContext
from app.modules.transactions.use_cases.review import is_awaiting_review

router = APIRouter(prefix="/api/v1", tags=["transfers-payments"])

NextAction = Literal["NONE", "CONFIRM", "WAIT_REVIEW"]


class SpendOut(BaseModel):
    id: uuid.UUID
    reference: str
    type: TxnType
    status: TxnStatus
    amount: Amount
    fee: Amount
    total: Amount = Field(description="What leaves the payer's wallet (amount + fee)")
    description: str
    counterparty: Counterparty | None = Field(description="The receiver, as the payer sees it")
    warnings: list[str] = Field(description="NEW_RECIPIENT, POSSIBLE_DUPLICATE")
    payment_request_code: str | None
    failure_code: str | None
    next_action: NextAction
    quote_expires_at: datetime | None
    created_at: datetime
    completed_at: datetime | None
    events: list[TransactionEventOut]


class TransferIn(BaseModel):
    wallet_number: str | None = Field(default=None, pattern=r"^[0-9]{10}$")
    email: str | None = Field(default=None, max_length=254)
    recipient_id: uuid.UUID | None = None
    amount: str = Field(max_length=19, description="Integer minor units as a string")
    note: str = Field(default="", max_length=140)

    @model_validator(mode="after")
    def _one_recipient(self) -> TransferIn:
        given = [v for v in (self.wallet_number, self.email, self.recipient_id) if v is not None]
        if len(given) != 1:
            raise ValueError("give exactly one of wallet_number, email, recipient_id")
        return self


class PayIn(BaseModel):
    amount: str | None = Field(default=None, max_length=19, description="Only for open amounts")


class RefundIn(BaseModel):
    amount: str = Field(max_length=19)
    reason: str = Field(min_length=3, max_length=140)


class MerchantPaymentOut(BaseModel):
    reference: str
    type: TxnType
    status: TxnStatus
    amount: Amount
    refunded: Amount
    refundable: Amount
    payer: Counterparty | None
    description: str
    related_reference: str | None = Field(description="For refunds: the payment refunded")
    created_at: datetime
    completed_at: datetime | None


class MerchantPaymentPageOut(BaseModel):
    items: list[MerchantPaymentOut]
    next_cursor: uuid.UUID | None


def _money(value: str | None, field: str = "amount") -> Money:
    try:
        amount = Money.from_api(value or "", "VND")
    except MoneyError as exc:
        raise ValidationFailedError(errors=[FieldError(field, "INVALID_AMOUNT", str(exc))]) from exc
    if not amount.is_positive:
        raise ValidationFailedError(errors=[FieldError(field, "NOT_POSITIVE", "must be > 0")])
    return amount


def _next_action(txn: Transaction) -> NextAction:
    if txn.status == TxnStatus.CREATED.value:
        return "CONFIRM"
    if is_awaiting_review(txn):
        return "WAIT_REVIEW"
    return "NONE"


async def spend_out(session: AsyncSession, txn: Transaction) -> SpendOut:
    events = await service.events_of(session, txn.id)
    snapshot = txn.counterparty_snapshot
    return SpendOut(
        id=txn.id,
        reference=txn.reference,
        type=TxnType(txn.type),
        status=TxnStatus(txn.status),
        amount=Amount(amount=str(txn.amount_minor), currency=txn.currency),
        fee=Amount(amount=str(txn.fee_minor), currency=txn.currency),
        total=Amount(amount=str(txn.amount_minor + txn.fee_minor), currency=txn.currency),
        description=txn.description,
        counterparty=Counterparty(**snapshot) if snapshot else None,
        warnings=list(txn.extra.get("warnings", [])),
        payment_request_code=txn.extra.get("payment_request_code"),
        failure_code=txn.failure_code,
        next_action=_next_action(txn),
        quote_expires_at=txn.quote_expires_at,
        created_at=txn.created_at,
        completed_at=txn.completed_at,
        events=[event_out(e) for e in events],
    )


async def _idempotent(
    store: IdempotencyStore,
    ctx: FinanceContext,
    response: Response,
    *,
    principal_id: uuid.UUID,
    scope: str,
    key: str,
    payload: dict[str, Any],
    run: Callable[[AsyncSession], Awaitable[Transaction]],
    status_code: int = 200,
    before: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    async def operation(session: AsyncSession) -> OperationResult:
        txn = await run(session)
        out = await spend_out(session, txn)
        return OperationResult(status_code, out.model_dump(mode="json"), str(txn.id))

    outcome = await store.execute_checked(
        ctx.db,
        principal_id=str(principal_id),
        scope=scope,
        key=key,
        payload=payload,
        before=before,
        operation=operation,
    )
    response.status_code = outcome.status_code
    if outcome.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return outcome.body


# ---------------------------------------------------------------- transfers


@router.post("/transfers", response_model=SpendOut, status_code=201)
async def quote_transfer(
    *,
    principal: CustomerPrincipal,
    body: TransferIn,
    response: Response,
    key: IdempotencyKey,
    ctx: FinanceDep,
    store: IdempotencyDep,
) -> dict[str, Any]:
    amount = _money(body.amount)
    target = transfer.RecipientTarget(
        wallet_number=body.wallet_number, email=body.email, recipient_id=body.recipient_id
    )
    return await _idempotent(
        store,
        ctx,
        response,
        principal_id=principal.user_id,
        scope="transfers.quote",
        key=key,
        payload=body.model_dump(mode="json"),
        run=lambda s: transfer.quote_transfer(
            s, ctx, user_id=principal.user_id, target=target, amount=amount, note=body.note, key=key
        ),
        status_code=201,
    )


async def _confirm(
    *,
    principal: CustomerPrincipal,
    reference: str,
    body: ConfirmIn,
    response: Response,
    key: str,
    ctx: FinanceContext,
    store: IdempotencyStore,
    meta: Any,
    scope: str,
) -> dict[str, Any]:
    return await _idempotent(
        store,
        ctx,
        response,
        principal_id=principal.user_id,
        scope=scope,
        key=key,
        payload={"reference": reference},  # never the PIN: a 6-digit hash is reversible
        before=lambda: transfer.check_pin(
            ctx, user_id=principal.user_id, reference=reference, pin=body.pin, meta=meta
        ),
        run=lambda s: transfer.confirm(
            s, ctx, user_id=principal.user_id, reference=reference, key=key
        ),
    )


@router.post("/transfers/{reference}/confirm", response_model=SpendOut)
async def confirm_transfer(
    *,
    principal: CustomerPrincipal,
    reference: str,
    body: ConfirmIn,
    response: Response,
    key: IdempotencyKey,
    ctx: FinanceDep,
    store: IdempotencyDep,
    meta: MetaDep,
) -> dict[str, Any]:
    return await _confirm(
        principal=principal,
        reference=reference,
        body=body,
        response=response,
        key=key,
        ctx=ctx,
        store=store,
        meta=meta,
        scope="spend.confirm",
    )


async def _cancel(principal_id: uuid.UUID, reference: str, ctx: FinanceContext) -> SpendOut:
    async with ctx.db.transaction() as session:
        txn = await transfer.cancel(session, ctx, user_id=principal_id, reference=reference)
        return await spend_out(session, txn)


async def _get(principal_id: uuid.UUID, reference: str, ctx: FinanceContext) -> SpendOut:
    async with ctx.db.transaction() as session:
        txn = await transfer.owned_spend(session, principal_id, reference)
        return await spend_out(session, txn)


@router.post("/transfers/{reference}/cancel", response_model=SpendOut)
async def cancel_transfer(
    reference: str, principal: CustomerPrincipal, ctx: FinanceDep
) -> SpendOut:
    return await _cancel(principal.user_id, reference, ctx)


@router.get("/transfers/{reference}", response_model=SpendOut)
async def get_transfer(reference: str, principal: CustomerPrincipal, ctx: FinanceDep) -> SpendOut:
    return await _get(principal.user_id, reference, ctx)


# ---------------------------------------------------------------- QR payments


@router.post("/payment-requests/{code}/pay", response_model=SpendOut, status_code=201)
async def pay_request(
    *,
    principal: CustomerPrincipal,
    code: str,
    body: PayIn,
    response: Response,
    key: IdempotencyKey,
    ctx: FinanceDep,
    store: IdempotencyDep,
) -> dict[str, Any]:
    amount = _money(body.amount) if body.amount else None
    return await _idempotent(
        store,
        ctx,
        response,
        principal_id=principal.user_id,
        scope="payment_requests.pay",
        key=key,
        payload={"code": code.upper(), **body.model_dump(mode="json")},
        run=lambda s: transfer.quote_request(
            s, ctx, user_id=principal.user_id, code=code, amount=amount, key=key
        ),
        status_code=201,
    )


@router.post("/payments/{reference}/confirm", response_model=SpendOut)
async def confirm_payment(
    *,
    principal: CustomerPrincipal,
    reference: str,
    body: ConfirmIn,
    response: Response,
    key: IdempotencyKey,
    ctx: FinanceDep,
    store: IdempotencyDep,
    meta: MetaDep,
) -> dict[str, Any]:
    return await _confirm(
        principal=principal,
        reference=reference,
        body=body,
        response=response,
        key=key,
        ctx=ctx,
        store=store,
        meta=meta,
        scope="spend.confirm",
    )


@router.post("/payments/{reference}/cancel", response_model=SpendOut)
async def cancel_payment(reference: str, principal: CustomerPrincipal, ctx: FinanceDep) -> SpendOut:
    return await _cancel(principal.user_id, reference, ctx)


@router.get("/payments/{reference}", response_model=SpendOut)
async def get_payment(reference: str, principal: CustomerPrincipal, ctx: FinanceDep) -> SpendOut:
    return await _get(principal.user_id, reference, ctx)


# ---------------------------------------------------------------- merchant


def _merchant_view(txn: Transaction, related: dict[uuid.UUID, str]) -> MerchantPaymentOut:
    is_refund = txn.type == TxnType.REFUND.value
    party = txn.counterparty_snapshot if is_refund else txn.extra.get("source_party")
    refunded = 0 if is_refund else txn.refunded_amount_minor
    refundable = (
        txn.amount_minor - refunded
        if txn.status in (TxnStatus.COMPLETED.value, TxnStatus.PARTIALLY_REFUNDED.value)
        and not is_refund
        else 0
    )
    return MerchantPaymentOut(
        reference=txn.reference,
        type=TxnType(txn.type),
        status=TxnStatus(txn.status),
        amount=Amount(amount=str(txn.amount_minor), currency=txn.currency),
        refunded=Amount(amount=str(refunded), currency=txn.currency),
        refundable=Amount(amount=str(refundable), currency=txn.currency),
        payer=Counterparty(**party) if party else None,
        description=txn.description,
        related_reference=related.get(txn.related_transaction_id)
        if txn.related_transaction_id
        else None,
        created_at=txn.created_at,
        completed_at=txn.completed_at,
    )


@router.get("/merchant/payments", response_model=MerchantPaymentPageOut)
async def merchant_payments(
    principal: CustomerPrincipal,
    ctx: FinanceDep,
    cursor: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> MerchantPaymentPageOut:
    async with ctx.db.transaction() as session:
        merchant = await merchant_of(session, principal.user_id)
        conditions = [
            or_(
                (Transaction.type == TxnType.PAYMENT.value)
                & (Transaction.destination_wallet_id == merchant.wallet_id),
                (Transaction.type == TxnType.REFUND.value)
                & (Transaction.source_wallet_id == merchant.wallet_id),
            ),
            Transaction.status != TxnStatus.CREATED.value,
        ]
        if cursor is not None:
            conditions.append(Transaction.id < cursor)
        rows = list(
            (
                await session.scalars(
                    select(Transaction)
                    .where(*conditions)
                    .order_by(Transaction.id.desc())
                    .limit(limit + 1)
                )
            ).all()
        )
        items = rows[:limit]
        related_ids = {t.related_transaction_id for t in items if t.related_transaction_id}
        related = (
            {
                t.id: t.reference
                for t in (
                    await session.scalars(
                        select(Transaction).where(Transaction.id.in_(related_ids))
                    )
                ).all()
            }
            if related_ids
            else {}
        )
    return MerchantPaymentPageOut(
        items=[_merchant_view(t, related) for t in items],
        next_cursor=items[-1].id if len(rows) > limit and items else None,
    )


@router.get("/merchant/payments/{reference}", response_model=MerchantPaymentOut)
async def merchant_payment(
    reference: str, principal: CustomerPrincipal, ctx: FinanceDep
) -> MerchantPaymentOut:
    async with ctx.db.transaction() as session:
        txn = await refund.merchant_payment(session, principal.user_id, reference)
    return _merchant_view(txn, {})


@router.post("/merchant/payments/{reference}/refunds", response_model=SpendOut, status_code=201)
async def create_refund(
    *,
    principal: CustomerPrincipal,
    reference: str,
    body: RefundIn,
    response: Response,
    key: IdempotencyKey,
    ctx: FinanceDep,
    store: IdempotencyDep,
) -> dict[str, Any]:
    amount = _money(body.amount)
    return await _idempotent(
        store,
        ctx,
        response,
        principal_id=principal.user_id,
        scope="merchant.refunds",
        key=key,
        payload={"reference": reference, **body.model_dump(mode="json")},
        run=lambda s: refund.refund(
            s,
            ctx,
            merchant_user_id=principal.user_id,
            reference=reference,
            amount=amount,
            reason=body.reason,
            key=key,
        ),
        status_code=201,
    )
