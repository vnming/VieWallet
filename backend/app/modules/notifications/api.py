"""Customer notification inbox API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from app.core.errors import NotFoundError
from app.modules.identity.public import CustomerPrincipal, IdentityDep
from app.modules.notifications import inapp

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: uuid.UUID
    category: str
    type: str
    title: str
    body: str
    data: dict[str, Any]
    priority: str
    read_at: datetime | None
    created_at: datetime


class NotificationPageOut(BaseModel):
    items: list[NotificationOut]
    next_cursor: uuid.UUID | None


class UnreadCountOut(BaseModel):
    unread: int


def _out(n: inapp.Notification) -> NotificationOut:
    return NotificationOut(
        id=n.id,
        category=n.category,
        type=n.type,
        title=n.title,
        body=n.body,
        data=n.data,
        priority=n.priority,
        read_at=n.read_at,
        created_at=n.created_at,
    )


@router.get("", response_model=NotificationPageOut)
async def list_notifications(
    *,
    principal: CustomerPrincipal,
    ctx: IdentityDep,
    cursor: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    unread: bool = False,
) -> NotificationPageOut:
    page = await inapp.list_for_user(
        ctx.db, principal.user_id, limit=limit, cursor=cursor, unread_only=unread
    )
    return NotificationPageOut(items=[_out(n) for n in page.items], next_cursor=page.next_cursor)


@router.get("/unread-count", response_model=UnreadCountOut)
async def unread_count(principal: CustomerPrincipal, ctx: IdentityDep) -> UnreadCountOut:
    return UnreadCountOut(unread=await inapp.unread_count(ctx.db, principal.user_id))


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    notification_id: uuid.UUID, principal: CustomerPrincipal, ctx: IdentityDep
) -> None:
    if await inapp.mark_read(ctx.db, ctx.clock, principal.user_id, notification_id) == 0:
        # Unknown, someone else's or already read: indistinguishable on purpose.
        async with ctx.db.transaction() as session:
            row = await session.get(inapp.Notification, notification_id)
        if row is None or row.user_id != principal.user_id:
            raise NotFoundError()


@router.post("/read-all", response_model=UnreadCountOut)
async def mark_all_read(principal: CustomerPrincipal, ctx: IdentityDep) -> UnreadCountOut:
    await inapp.mark_read(ctx.db, ctx.clock, principal.user_id, None)
    return UnreadCountOut(unread=0)
