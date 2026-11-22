"""Wallet API: the customer's own wallet and staff lookup/freeze."""

from __future__ import annotations

import uuid
from datetime import datetime

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
from app.modules.wallet import service
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
