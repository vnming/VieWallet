"""Recipient lookup, saved recipients and the personal receive QR (api-spec.md §5)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from app.core.errors import FieldError, ValidationFailedError
from app.modules.identity.public import CustomerPrincipal, IdentityDep
from app.modules.wallet import recipients, service

router = APIRouter(prefix="/api/v1/recipients", tags=["recipients"])
qr_router = APIRouter(prefix="/api/v1/wallet", tags=["wallet"])


class RecipientOut(BaseModel):
    wallet_number_masked: str
    display_name: str = Field(description="Masked name (people) or business name (merchants)")
    kind: str
    active: bool


class LookupOut(RecipientOut):
    wallet_number: str | None = Field(
        default=None, description="Echoed back only when the caller typed it"
    )


class SavedRecipientOut(BaseModel):
    id: uuid.UUID
    nickname: str | None
    last_used_at: datetime | None
    recipient: RecipientOut


class SaveRecipientIn(BaseModel):
    wallet_number: str = Field(pattern=r"^[0-9]{10}$")
    nickname: str | None = Field(default=None, max_length=40)


class RenameIn(BaseModel):
    nickname: str | None = Field(default=None, max_length=40)


class ReceiveQrOut(BaseModel):
    wallet_number: str
    display_name: str
    payload: str = Field(description="Text to encode in the QR code (a web link)")


def _out(view: recipients.RecipientView) -> RecipientOut:
    return RecipientOut(
        wallet_number_masked=view.wallet_number_masked,
        display_name=view.display_name,
        kind=view.kind,
        active=view.active,
    )


@router.get("/lookup", response_model=LookupOut)
async def lookup(
    principal: CustomerPrincipal,
    ctx: IdentityDep,
    wallet_number: str | None = Query(default=None, pattern=r"^[0-9]{10}$"),
    email: str | None = Query(default=None, max_length=254),
) -> LookupOut:
    if (wallet_number is None) == (email is None):
        raise ValidationFailedError(
            errors=[FieldError("wallet_number", "ONE_OF", "give wallet_number or email")]
        )
    subject = str(principal.user_id)
    # Counted before the lookup, so misses cost the same as hits (anti-enumeration).
    await ctx.rate_limiter.enforce(recipients.LOOKUP_PER_USER, subject)
    await ctx.rate_limiter.enforce(recipients.LOOKUP_PER_USER_DAY, subject)
    async with ctx.db.transaction() as session:
        wallet = await recipients.find_recipient(
            session, requester_id=principal.user_id, wallet_number=wallet_number, email=email
        )
        view = await recipients.describe_wallet(session, wallet)
    return LookupOut(**_out(view).model_dump(), wallet_number=wallet_number)


@router.get("", response_model=list[SavedRecipientOut])
async def list_saved(principal: CustomerPrincipal, ctx: IdentityDep) -> list[SavedRecipientOut]:
    async with ctx.db.transaction() as session:
        rows = await recipients.list_saved(session, principal.user_id)
    return [
        SavedRecipientOut(
            id=r.id, nickname=r.nickname, last_used_at=r.last_used_at, recipient=_out(r.recipient)
        )
        for r in rows
    ]


@router.post("", response_model=SavedRecipientOut, status_code=201)
async def save(
    body: SaveRecipientIn, principal: CustomerPrincipal, ctx: IdentityDep
) -> SavedRecipientOut:
    async with ctx.db.transaction() as session:
        wallet = await recipients.find_recipient(
            session, requester_id=principal.user_id, wallet_number=body.wallet_number
        )
        row = await recipients.save_recipient(
            session,
            ctx.clock,
            owner_user_id=principal.user_id,
            wallet=wallet,
            nickname=body.nickname,
        )
        view = await recipients.describe_wallet(session, wallet)
    return SavedRecipientOut(
        id=row.id, nickname=row.nickname, last_used_at=row.last_used_at, recipient=_out(view)
    )


@router.patch("/{recipient_id}", response_model=SavedRecipientOut)
async def rename(
    recipient_id: uuid.UUID, body: RenameIn, principal: CustomerPrincipal, ctx: IdentityDep
) -> SavedRecipientOut:
    async with ctx.db.transaction() as session:
        row = await recipients.rename_saved(session, principal.user_id, recipient_id, body.nickname)
        wallet = await service.get_wallet(session, row.wallet_id)
        view = await recipients.describe_wallet(session, wallet)
    return SavedRecipientOut(
        id=row.id, nickname=row.nickname, last_used_at=row.last_used_at, recipient=_out(view)
    )


@router.delete("/{recipient_id}", status_code=204)
async def delete(
    recipient_id: uuid.UUID, principal: CustomerPrincipal, ctx: IdentityDep
) -> Response:
    async with ctx.db.transaction() as session:
        await recipients.delete_saved(session, principal.user_id, recipient_id)
    return Response(status_code=204)


@qr_router.get("/qr", response_model=ReceiveQrOut)
async def receive_qr(principal: CustomerPrincipal, ctx: IdentityDep) -> ReceiveQrOut:
    """Personal QR: a link to the transfer screen with this wallet prefilled. Whoever scans it
    still sees the masked name from a lookup before paying, so it needs no signature."""
    async with ctx.db.transaction() as session:
        wallet = await service.ensure_wallet(session, ctx.clock, principal.user_id)
        view = await recipients.describe_wallet(session, wallet)
    base = ctx.settings.web_base_url.rstrip("/")
    return ReceiveQrOut(
        wallet_number=wallet.wallet_number,
        display_name=view.display_name,
        payload=f"{base}/transfer?to={wallet.wallet_number}",
    )
