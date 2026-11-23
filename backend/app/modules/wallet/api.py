"""Wallet API: the customer's own wallet and staff lookup/freeze."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.money import Money
from app.modules.identity.public import (
    CustomerPrincipal,
    IdentityDep,
    MetaDep,
    Principal,
    require_permission,
    verify_pin,
)
from app.modules.wallet import destinations, service
from app.modules.wallet.domain import FreezeSource, WalletStatus

router = APIRouter(prefix="/api/v1/wallet", tags=["wallet"])
admin_router = APIRouter(prefix="/api/v1/admin/wallets", tags=["admin-wallets"])

CAN_READ_USERS = require_permission("user:read:any")
CAN_FREEZE = require_permission("wallet:freeze:any")


class AmountOut(BaseModel):
    amount: str = Field(description="Integer minor units as a string")
    currency: str

    @classmethod
    def of(cls, money: Money) -> AmountOut:
        return cls(amount=str(money.amount_minor), currency=money.currency)


class WalletOut(BaseModel):
    id: uuid.UUID
    wallet_number: str
    currency: str
    status: WalletStatus
    freeze_source: FreezeSource | None
    frozen_at: datetime | None
    balance: AmountOut


class AdminWalletOut(WalletOut):
    owner_user_id: uuid.UUID
    status_reason: str | None
    created_at: datetime


class FreezeIn(BaseModel):
    reason: str = Field(default="", max_length=200)


class StaffFreezeIn(BaseModel):
    reason: str = Field(min_length=5, max_length=200)


class UnfreezeIn(BaseModel):
    pin: str = Field(min_length=6, max_length=6)


def wallet_out(view: service.WalletView) -> WalletOut:
    w = view.wallet
    return WalletOut(
        id=w.id,
        wallet_number=w.wallet_number,
        currency=w.currency,
        status=WalletStatus(w.status),
        freeze_source=FreezeSource(w.freeze_source) if w.freeze_source else None,
        frozen_at=w.frozen_at,
        balance=AmountOut.of(view.balance),
    )


def admin_wallet_out(view: service.WalletView) -> AdminWalletOut:
    w = view.wallet
    return AdminWalletOut(
        **wallet_out(view).model_dump(),
        owner_user_id=w.owner_user_id,
        status_reason=w.status_reason,
        created_at=w.created_at,
    )


@router.get("", response_model=WalletOut)
async def get_my_wallet(principal: CustomerPrincipal, ctx: IdentityDep) -> WalletOut:
    async with ctx.db.transaction() as session:
        wallet = await service.ensure_wallet(session, ctx.clock, principal.user_id)
        return wallet_out(await service.view(session, wallet))


@router.post("/freeze", response_model=WalletOut)
async def freeze_my_wallet(
    body: FreezeIn, principal: CustomerPrincipal, ctx: IdentityDep, meta: MetaDep
) -> WalletOut:
    """Self-service freeze (lost phone): no PIN needed, it only removes capabilities."""
    async with ctx.db.transaction() as session:
        wallet = await service.ensure_wallet(session, ctx.clock, principal.user_id)
        await service.freeze(
            session,
            ctx.clock,
            wallet.id,
            source=FreezeSource.SELF,
            actor_id=principal.user_id,
            reason=body.reason or "self-service",
            ip=meta.ip,
            user_agent=meta.user_agent,
        )
        return wallet_out(await service.view(session, wallet))


@router.post("/unfreeze", response_model=WalletOut)
async def unfreeze_my_wallet(
    body: UnfreezeIn, principal: CustomerPrincipal, ctx: IdentityDep, meta: MetaDep
) -> WalletOut:
    await verify_pin(ctx, user_id=principal.user_id, pin=body.pin, meta=meta)
    async with ctx.db.transaction() as session:
        wallet = await service.ensure_wallet(session, ctx.clock, principal.user_id)
        await service.unfreeze(
            session,
            ctx.clock,
            wallet.id,
            by_owner=True,
            actor_id=principal.user_id,
            reason="self-service",
            ip=meta.ip,
            user_agent=meta.user_agent,
        )
        return wallet_out(await service.view(session, wallet))


@admin_router.get("/{wallet_number}", response_model=AdminWalletOut)
async def admin_get_wallet(
    wallet_number: str, ctx: IdentityDep, _principal: Principal = CAN_READ_USERS
) -> AdminWalletOut:
    async with ctx.db.transaction() as session:
        wallet = await service.get_by_number(session, wallet_number)
        return admin_wallet_out(await service.view(session, wallet))


@admin_router.post("/{wallet_number}/freeze", response_model=AdminWalletOut)
async def admin_freeze_wallet(
    wallet_number: str,
    body: StaffFreezeIn,
    ctx: IdentityDep,
    meta: MetaDep,
    principal: Principal = CAN_FREEZE,
) -> AdminWalletOut:
    """Freezing is immediate (protective). Unfreezing needs a second person (operations)."""
    async with ctx.db.transaction() as session:
        wallet = await service.get_by_number(session, wallet_number)
        await service.freeze(
            session,
            ctx.clock,
            wallet.id,
            source=FreezeSource.STAFF,
            actor_id=principal.user_id,
            reason=body.reason,
            ip=meta.ip,
            user_agent=meta.user_agent,
        )
        return admin_wallet_out(await service.view(session, wallet))


# ---------------------------------------------------------------- bank accounts (withdrawals)


class BankOut(BaseModel):
    code: str
    name: str


class DestinationIn(BaseModel):
    bank_code: str = Field(max_length=20)
    account_number: str = Field(min_length=6, max_length=20)
    account_name: str = Field(min_length=2, max_length=120)


class DestinationOut(BaseModel):
    id: uuid.UUID
    bank_code: str
    bank_name: str
    account_number_masked: str
    account_name: str
    created_at: datetime
    usable_from: datetime = Field(
        description="Withdrawals to a new account are blocked until this time (risk cooldown)"
    )


def destination_out(row: destinations.BankDestination, cooldown_minutes: int) -> DestinationOut:
    return DestinationOut(
        id=row.id,
        bank_code=row.bank_code,
        bank_name=destinations.BANKS.get(row.bank_code, row.bank_code),
        account_number_masked=row.account_number_masked,
        account_name=row.account_name,
        created_at=row.created_at,
        usable_from=row.created_at + timedelta(minutes=cooldown_minutes),
    )


@router.get("/banks", response_model=list[BankOut])
async def list_banks(_principal: CustomerPrincipal) -> list[BankOut]:
    return [BankOut(code=code, name=name) for code, name in destinations.BANKS.items()]


@router.get("/destinations", response_model=list[DestinationOut])
async def list_my_destinations(
    principal: CustomerPrincipal, ctx: IdentityDep
) -> list[DestinationOut]:
    async with ctx.db.transaction() as session:
        rows = await destinations.list_destinations(session, principal.user_id)
    minutes = ctx.settings.new_destination_cooldown_minutes
    return [destination_out(r, minutes) for r in rows]


@router.post("/destinations", response_model=DestinationOut, status_code=201)
async def add_my_destination(
    body: DestinationIn, principal: CustomerPrincipal, ctx: IdentityDep
) -> DestinationOut:
    async with ctx.db.transaction() as session:
        row = await destinations.add_destination(
            session,
            ctx.clock,
            ctx.cipher,
            ctx.lookup_key,
            user_id=principal.user_id,
            bank_code=body.bank_code,
            account_number=body.account_number,
            account_name=body.account_name,
        )
    return destination_out(row, ctx.settings.new_destination_cooldown_minutes)


@router.delete("/destinations/{destination_id}", status_code=204)
async def remove_my_destination(
    destination_id: uuid.UUID, principal: CustomerPrincipal, ctx: IdentityDep
) -> None:
    async with ctx.db.transaction() as session:
        await destinations.remove_destination(
            session, ctx.clock, user_id=principal.user_id, destination_id=destination_id
        )
