"""Payment requests (QR) and the merchant profile (api-spec.md §7).

Paying a request (quote + confirm) lives in the transactions module, which owns money.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from app.core.errors import FieldError, ValidationFailedError
from app.core.idempotency import IdempotencyDep, IdempotencyKey, OperationResult
from app.core.money import Money, MoneyError
from app.modules.identity.public import CustomerPrincipal, IdentityDep, IdentityServices, User
from app.modules.payments import service
from app.modules.payments.models import IssuerType, Merchant, PaymentRequest, RequestStatus
from app.modules.wallet.public import (
    describe_wallet,
    ensure_wallet,
    find_recipient,
    get_wallet,
    mask_name,
    view,
)

router = APIRouter(prefix="/api/v1", tags=["payment-requests"])


class AmountOut(BaseModel):
    amount: str
    currency: str


class IssuerOut(BaseModel):
    kind: IssuerType
    name: str = Field(description="Verified business name, or the masked name of a person")
    wallet_number_masked: str
    mcc: str | None = None


class PaymentRequestOut(BaseModel):
    code: str
    url: str = Field(description="Text encoded in the QR code")
    issuer: IssuerOut
    amount: AmountOut | None = Field(description="Null: the payer enters the amount")
    description: str
    single_use: bool
    status: RequestStatus
    expires_at: datetime
    created_at: datetime
    is_mine: bool = Field(description="The caller issued this request")
    addressed_to_me: bool = Field(description="The caller is the named payer")
    payer_name: str | None = Field(default=None, description="Masked; for the issuer only")
    paid_count: int


class PaymentRequestIn(BaseModel):
    kind: Literal["MERCHANT", "USER"] = Field(
        description="MERCHANT: a shop's QR (needs a merchant profile); USER: ask someone for money"
    )
    amount: str | None = Field(default=None, max_length=19)
    description: str = Field(default="", max_length=140)
    single_use: bool = True
    expires_in_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 30)
    payer_wallet_number: str | None = Field(default=None, pattern=r"^[0-9]{10}$")
    payer_email: str | None = Field(default=None, max_length=254)


class MerchantOut(BaseModel):
    id: uuid.UUID
    display_name: str
    legal_name: str
    mcc: str
    status: str
    wallet_number: str
    balance: AmountOut


def _url(ctx: IdentityServices, request: PaymentRequest) -> str:
    return f"{ctx.settings.web_base_url.rstrip('/')}/pay/{request.code}"


async def request_out(
    session: Any, ctx: IdentityServices, request: PaymentRequest, viewer_id: uuid.UUID
) -> PaymentRequestOut:
    wallet = await get_wallet(session, request.destination_wallet_id)
    merchant = await session.get(Merchant, request.merchant_id) if request.merchant_id else None
    who = await describe_wallet(session, wallet, merchant.display_name if merchant else None)
    payer_name = None
    if request.payer_user_id is not None and request.issuer_user_id == viewer_id:
        payer = await session.get(User, request.payer_user_id)
        payer_name = mask_name(payer.full_name) if payer else None
    return PaymentRequestOut(
        code=request.code,
        url=_url(ctx, request),
        issuer=IssuerOut(
            kind=IssuerType(request.issuer_type),
            name=who.display_name,
            wallet_number_masked=who.wallet_number_masked,
            mcc=merchant.mcc if merchant else None,
        ),
        amount=AmountOut(amount=str(request.amount_minor), currency=request.currency)
        if request.amount_minor is not None
        else None,
        description=request.description,
        single_use=request.single_use,
        status=service.effective_status(request, ctx.clock.now()),
        expires_at=request.expires_at,
        created_at=request.created_at,
        is_mine=request.issuer_user_id == viewer_id,
        addressed_to_me=request.payer_user_id == viewer_id,
        payer_name=payer_name,
        paid_count=request.paid_count,
    )


def _amount(value: str | None) -> Money | None:
    if value is None or value == "":
        return None
    try:
        amount = Money.from_api(value, "VND")
    except MoneyError as exc:
        raise ValidationFailedError(
            errors=[FieldError("amount", "INVALID_AMOUNT", str(exc))]
        ) from exc
    if not amount.is_positive:
        raise ValidationFailedError(errors=[FieldError("amount", "NOT_POSITIVE", "must be > 0")])
    return amount


@router.post("/payment-requests", response_model=PaymentRequestOut, status_code=201)
async def create_payment_request(
    *,
    principal: CustomerPrincipal,
    body: PaymentRequestIn,
    response: Response,
    ctx: IdentityDep,
    store: IdempotencyDep,
    key: IdempotencyKey,
) -> dict[str, Any]:
    amount = _amount(body.amount)
    if body.kind == "USER":
        if amount is None:
            raise ValidationFailedError(
                errors=[FieldError("amount", "REQUIRED", "amount required")]
            )
        if body.payer_wallet_number is not None and body.payer_email is not None:
            raise ValidationFailedError(
                errors=[FieldError("payer_email", "ONE_OF", "give one payer identifier")]
            )

    async def operation(session: Any) -> OperationResult:
        payer_id = None
        if body.kind == "MERCHANT":
            merchant = await service.merchant_of(session, principal.user_id)
            if merchant.status != "ACTIVE":
                raise service.MerchantNotActiveError()
            wallet = await get_wallet(session, merchant.wallet_id)
            default_ttl = (
                service.MERCHANT_SINGLE_USE_TTL if body.single_use else service.MERCHANT_STATIC_TTL
            )
            spec_kwargs: dict[str, Any] = {
                "merchant_id": merchant.id,
                "single_use": body.single_use,
            }
            issuer = IssuerType.MERCHANT
        else:
            wallet = await ensure_wallet(session, ctx.clock, principal.user_id)
            if body.payer_wallet_number or body.payer_email:
                payer_wallet = await find_recipient(
                    session,
                    requester_id=principal.user_id,
                    wallet_number=body.payer_wallet_number,
                    email=body.payer_email,
                )
                payer_id = payer_wallet.owner_user_id
            default_ttl = service.USER_REQUEST_TTL
            spec_kwargs = {"single_use": True}  # a person's request is paid once
            issuer = IssuerType.USER
        ttl = timedelta(minutes=body.expires_in_minutes) if body.expires_in_minutes else default_ttl
        request = await service.create_request(
            session,
            ctx.clock,
            ctx.signing_key,
            service.NewRequest(
                issuer_type=issuer,
                issuer_user_id=principal.user_id,
                destination_wallet=wallet,
                amount=amount,
                description=body.description,
                ttl=ttl,
                payer_user_id=payer_id,
                **spec_kwargs,
            ),
        )
        out = await request_out(session, ctx, request, principal.user_id)
        return OperationResult(201, out.model_dump(mode="json"), str(request.id))

    outcome = await store.execute(
        ctx.db,
        principal_id=str(principal.user_id),
        scope="payment_requests.create",
        key=key,
        payload=body.model_dump(mode="json"),
        operation=operation,
    )
    response.status_code = outcome.status_code
    if outcome.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return outcome.body


@router.get("/payment-requests", response_model=list[PaymentRequestOut])
async def list_payment_requests(
    principal: CustomerPrincipal,
    ctx: IdentityDep,
    role: Literal["issued", "incoming"] = "issued",
    open_only: bool = Query(default=False, alias="open"),
) -> list[PaymentRequestOut]:
    async with ctx.db.transaction() as session:
        rows = await service.list_requests(
            session,
            issuer_user_id=principal.user_id if role == "issued" else None,
            payer_user_id=principal.user_id if role == "incoming" else None,
            open_only=open_only,
            now=ctx.clock.now(),
        )
        return [await request_out(session, ctx, r, principal.user_id) for r in rows]


@router.get("/payment-requests/{code}", response_model=PaymentRequestOut)
async def get_payment_request(
    code: str, principal: CustomerPrincipal, ctx: IdentityDep
) -> PaymentRequestOut:
    async with ctx.db.transaction() as session:
        request = await service.get_by_code(session, ctx.signing_key, code)
        if request.payer_user_id not in (None, principal.user_id) and (
            request.issuer_user_id != principal.user_id
        ):
            raise service.PaymentRequestNotFoundError()  # addressed to someone else: 404
        return await request_out(session, ctx, request, principal.user_id)


@router.post("/payment-requests/{code}/cancel", response_model=PaymentRequestOut)
async def cancel_payment_request(
    code: str, principal: CustomerPrincipal, ctx: IdentityDep
) -> PaymentRequestOut:
    async with ctx.db.transaction() as session:
        request = await service.close_request(
            session, ctx.clock, ctx.signing_key, code, user_id=principal.user_id, as_issuer=True
        )
        return await request_out(session, ctx, request, principal.user_id)


@router.post("/payment-requests/{code}/decline", response_model=PaymentRequestOut)
async def decline_payment_request(
    code: str, principal: CustomerPrincipal, ctx: IdentityDep
) -> PaymentRequestOut:
    async with ctx.db.transaction() as session:
        request = await service.close_request(
            session, ctx.clock, ctx.signing_key, code, user_id=principal.user_id, as_issuer=False
        )
        return await request_out(session, ctx, request, principal.user_id)


@router.get("/merchant", response_model=MerchantOut)
async def my_merchant(principal: CustomerPrincipal, ctx: IdentityDep) -> MerchantOut:
    async with ctx.db.transaction() as session:
        merchant = await service.merchant_of(session, principal.user_id)
        wallet_view = await view(session, await get_wallet(session, merchant.wallet_id))
    return MerchantOut(
        id=merchant.id,
        display_name=merchant.display_name,
        legal_name=merchant.legal_name,
        mcc=merchant.mcc,
        status=merchant.status,
        wallet_number=wallet_view.wallet.wallet_number,
        balance=AmountOut(
            amount=str(wallet_view.balance.amount_minor), currency=wallet_view.balance.currency
        ),
    )
