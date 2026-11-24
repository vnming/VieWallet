"""Help center, customer tickets and the staff ticket API (api-spec.md §12, §13 "Ticket")."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.modules.identity.public import (
    CustomerPrincipal,
    IdentityDep,
    Principal,
    require_permission,
)
from app.modules.support import service
from app.modules.support.models import TICKET_CATEGORIES, HelpArticle, Ticket, TicketMessage
from app.modules.transactions.public import get_for_wallet
from app.modules.wallet.public import ensure_wallet

router = APIRouter(prefix="/api/v1", tags=["support"])
admin_router = APIRouter(prefix="/api/v1/admin/tickets", tags=["admin-support"])

CAN_MANAGE = require_permission("ticket:manage:any")

Category = Literal["TRANSACTION", "ACCOUNT", "KYC", "SECURITY", "OTHER"]
Status = Literal["OPEN", "PENDING_CUSTOMER", "RESOLVED", "CLOSED"]
assert set(TICKET_CATEGORIES) == {"TRANSACTION", "ACCOUNT", "KYC", "SECURITY", "OTHER"}  # noqa: S101


class ArticleSummaryOut(BaseModel):
    slug: str
    category: str
    title: str
    summary: str


class ArticleOut(ArticleSummaryOut):
    body: str = Field(description="Markdown subset: ## headings, paragraphs, - lists, **bold**")
    updated_at: datetime


class TicketIn(BaseModel):
    category: Category
    subject: str = Field(min_length=5, max_length=120)
    message: str = Field(min_length=10, max_length=4000)
    transaction_reference: str | None = Field(default=None, pattern=r"^VW\d{6}[0-9A-Z]{10}$")


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class StaffMessageIn(BaseModel):
    body: str = Field(default="", max_length=4000)
    internal: bool = False
    status: Status | None = None


class MessageOut(BaseModel):
    id: uuid.UUID
    author: Literal["CUSTOMER", "STAFF"]
    body: str
    internal: bool
    created_at: datetime


class TicketOut(BaseModel):
    reference: str
    category: Category
    subject: str
    status: Status
    transaction_reference: str | None
    created_at: datetime
    updated_at: datetime


class TicketDetailOut(TicketOut):
    messages: list[MessageOut]


class StaffTicketOut(TicketOut):
    user_id: uuid.UUID
    assigned_to: uuid.UUID | None


class StaffTicketDetailOut(StaffTicketOut):
    messages: list[MessageOut]


class StaffTicketPageOut(BaseModel):
    items: list[StaffTicketOut]
    page: int
    page_size: int
    total: int


def _summary(a: HelpArticle) -> ArticleSummaryOut:
    return ArticleSummaryOut(slug=a.slug, category=a.category, title=a.title, summary=a.summary)


def _ticket(t: Ticket) -> TicketOut:
    return TicketOut(
        reference=t.reference,
        category=t.category,  # type: ignore[arg-type]
        subject=t.subject,
        status=t.status,  # type: ignore[arg-type]
        transaction_reference=t.transaction_reference,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _message(m: TicketMessage) -> MessageOut:
    return MessageOut(
        id=m.id,
        author=m.author_type,  # type: ignore[arg-type]
        body=m.body,
        internal=m.internal,
        created_at=m.created_at,
    )


# ---------------------------------------------------------------- help center (no sign-in)


@router.get("/help/articles", response_model=list[ArticleSummaryOut])
async def help_articles(
    ctx: IdentityDep,
    q: str | None = Query(default=None, max_length=80),
    locale: Literal["vi", "en"] = "vi",
) -> list[ArticleSummaryOut]:
    async with ctx.db.transaction() as session:
        rows = await service.search_articles(session, locale=locale, query=q)
    return [_summary(a) for a in rows]


@router.get("/help/articles/{slug}", response_model=ArticleOut)
async def help_article(
    slug: str, ctx: IdentityDep, locale: Literal["vi", "en"] = "vi"
) -> ArticleOut:
    async with ctx.db.transaction() as session:
        a = await service.get_article(session, slug, locale)
    return ArticleOut(**_summary(a).model_dump(), body=a.body, updated_at=a.updated_at)


# ---------------------------------------------------------------- customer tickets


@router.get("/support/tickets", response_model=list[TicketOut])
async def my_tickets(principal: CustomerPrincipal, ctx: IdentityDep) -> list[TicketOut]:
    async with ctx.db.transaction() as session:
        return [_ticket(t) for t in await service.customer_tickets(session, principal.user_id)]


@router.post("/support/tickets", response_model=TicketDetailOut, status_code=201)
async def open_ticket(
    body: TicketIn, principal: CustomerPrincipal, ctx: IdentityDep
) -> TicketDetailOut:
    await ctx.rate_limiter.enforce(service.TICKETS_PER_USER, str(principal.user_id))
    async with ctx.db.transaction() as session:
        if body.transaction_reference is not None:
            # Only one's own transaction can be attached (404 otherwise, like history).
            wallet = await ensure_wallet(session, ctx.clock, principal.user_id)
            await get_for_wallet(session, wallet.id, body.transaction_reference)
        ticket = await service.open_ticket(
            session,
            ctx.clock,
            user_id=principal.user_id,
            ticket=service.NewTicket(
                category=body.category,
                subject=body.subject,
                message=body.message,
                transaction_reference=body.transaction_reference,
            ),
        )
        messages = await service.messages_of(session, ticket.id, include_internal=False)
    return TicketDetailOut(**_ticket(ticket).model_dump(), messages=[_message(m) for m in messages])


@router.get("/support/tickets/{reference}", response_model=TicketDetailOut)
async def my_ticket(
    reference: str, principal: CustomerPrincipal, ctx: IdentityDep
) -> TicketDetailOut:
    async with ctx.db.transaction() as session:
        ticket = await service.get_ticket(session, reference, user_id=principal.user_id)
        messages = await service.messages_of(session, ticket.id, include_internal=False)
    return TicketDetailOut(**_ticket(ticket).model_dump(), messages=[_message(m) for m in messages])


@router.post("/support/tickets/{reference}/messages", response_model=TicketDetailOut)
async def reply(
    reference: str, body: MessageIn, principal: CustomerPrincipal, ctx: IdentityDep
) -> TicketDetailOut:
    async with ctx.db.transaction() as session:
        ticket = await service.customer_reply(
            session, ctx.clock, reference, user_id=principal.user_id, body=body.body
        )
        messages = await service.messages_of(session, ticket.id, include_internal=False)
    return TicketDetailOut(**_ticket(ticket).model_dump(), messages=[_message(m) for m in messages])


# ---------------------------------------------------------------- staff


def _staff(t: Ticket) -> StaffTicketOut:
    return StaffTicketOut(**_ticket(t).model_dump(), user_id=t.user_id, assigned_to=t.assigned_to)


@admin_router.get("", response_model=StaffTicketPageOut)
async def staff_tickets(
    ctx: IdentityDep,
    _principal: Principal = CAN_MANAGE,
    status: Status | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> StaffTicketPageOut:
    async with ctx.db.transaction() as session:
        rows, total = await service.staff_queue(
            session, status=status, page=page, page_size=page_size
        )
    return StaffTicketPageOut(
        items=[_staff(t) for t in rows], page=page, page_size=page_size, total=total
    )


@admin_router.get("/{reference}", response_model=StaffTicketDetailOut)
async def staff_ticket(
    reference: str, ctx: IdentityDep, _principal: Principal = CAN_MANAGE
) -> StaffTicketDetailOut:
    async with ctx.db.transaction() as session:
        ticket = await service.get_ticket(session, reference, user_id=None)
        messages = await service.messages_of(session, ticket.id, include_internal=True)
    return StaffTicketDetailOut(
        **_staff(ticket).model_dump(), messages=[_message(m) for m in messages]
    )


@admin_router.post("/{reference}/messages", response_model=StaffTicketDetailOut)
async def staff_reply(
    reference: str, body: StaffMessageIn, ctx: IdentityDep, principal: Principal = CAN_MANAGE
) -> StaffTicketDetailOut:
    async with ctx.db.transaction() as session:
        ticket = await service.staff_reply(
            session,
            ctx.clock,
            reference,
            staff_id=principal.user_id,
            body=body.body,
            internal=body.internal,
            status=body.status,
        )
        messages = await service.messages_of(session, ticket.id, include_internal=True)
    return StaffTicketDetailOut(
        **_staff(ticket).model_dump(), messages=[_message(m) for m in messages]
    )
