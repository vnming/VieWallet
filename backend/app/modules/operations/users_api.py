"""Admin API: customer lookup and profile (masked), with an audited PII reveal.

Support staff see masked data (``user:read:any``). Unmasking email, phone and full name needs
``user:pii_reveal:any`` (sensitive: fresh MFA) and writes an audit row every time.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.access.public import roles_for_user
from app.modules.audit.public import AuditEntry, record
from app.modules.identity.public import IdentityDep, MetaDep, Principal, User, require_permission
from app.modules.kyc.public import profile_of
from app.modules.risk.public import list_flags
from app.modules.transactions.public import Transaction
from app.modules.wallet.public import (
    Wallet,
    find_for_owner,
    mask_name,
    mask_wallet_number,
    view,
)

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin-users"])

CAN_READ_USERS = require_permission("user:read:any")
CAN_REVEAL_PII = require_permission("user:pii_reveal:any")
MIN_QUERY = 3
WALLET_NUMBER = re.compile(r"^[0-9]{10}$")
PHONE = re.compile(r"^(?:\+84|0)([0-9]{9})$")


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def mask_phone(phone: str | None) -> str | None:
    return None if not phone else f"{phone[:3]}*****{phone[-3:]}"


class UserSummaryOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    phone: str | None
    user_type: str
    status: str
    kyc_tier: int
    wallet_number: str | None
    created_at: datetime


class WalletSummaryOut(BaseModel):
    wallet_number: str
    status: str
    freeze_source: str | None
    balance: str
    currency: str


class FlagOut(BaseModel):
    flag: str
    reason: str
    created_at: datetime
    expires_at: datetime | None
    removed_at: datetime | None


class RecentTxnOut(BaseModel):
    reference: str
    type: str
    status: str
    amount: str
    currency: str
    direction: str
    created_at: datetime


class UserDetailOut(UserSummaryOut):
    roles: list[str]
    email_verified: bool
    last_login_at: datetime | None
    kyc_status: str | None
    wallet: WalletSummaryOut | None
    risk_flags: list[FlagOut]
    recent_transactions: list[RecentTxnOut]


class RevealedPiiOut(BaseModel):
    email: str
    full_name: str
    phone: str | None


def _summary(user: User, wallet: Wallet | None) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": mask_email(user.email),
        "name": mask_name(user.full_name),
        "phone": mask_phone(user.phone),
        "user_type": user.user_type,
        "status": user.status,
        "kyc_tier": user.kyc_tier,
        "wallet_number": mask_wallet_number(wallet.wallet_number) if wallet else None,
        "created_at": user.created_at,
    }


@router.get("", response_model=list[UserSummaryOut])
async def search_users(
    ctx: IdentityDep,
    _principal: Principal = CAN_READ_USERS,
    q: str = Query(min_length=MIN_QUERY, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[UserSummaryOut]:
    """Exact wallet number or phone, email prefix, or name words (case-insensitive)."""
    term = q.strip()
    async with ctx.db.transaction() as session:
        conditions: list[Any] = []
        if WALLET_NUMBER.match(term):
            owner = await session.scalar(
                select(Wallet.owner_user_id).where(Wallet.wallet_number == term)
            )
            if owner is not None:
                conditions.append(User.id == owner)
        if match := PHONE.match(term.replace(" ", "")):
            conditions.append(User.phone == f"+84{match.group(1)}")
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(func.lower(User.email).like(f"{escaped.lower()}%", escape="\\"))
        conditions.append(User.full_name.ilike(f"%{escaped}%", escape="\\"))
        users = (
            await session.scalars(
                select(User)
                .where(User.user_type == "CUSTOMER", or_(*conditions))
                .order_by(User.created_at.desc())
                .limit(limit)
            )
        ).all()
        return [UserSummaryOut(**_summary(u, await find_for_owner(session, u.id))) for u in users]


async def _customer(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None or user.user_type != "CUSTOMER":
        raise NotFoundError()
    return user


@router.get("/{user_id}", response_model=UserDetailOut)
async def user_detail(
    user_id: uuid.UUID, ctx: IdentityDep, _principal: Principal = CAN_READ_USERS
) -> UserDetailOut:
    async with ctx.db.transaction() as session:
        user = await _customer(session, user_id)
        wallet = await find_for_owner(session, user.id)
        wallet_out: WalletSummaryOut | None = None
        recent: list[RecentTxnOut] = []
        if wallet is not None:
            snapshot = await view(session, wallet)
            wallet_out = WalletSummaryOut(
                wallet_number=mask_wallet_number(wallet.wallet_number),
                status=wallet.status,
                freeze_source=wallet.freeze_source,
                balance=str(snapshot.balance.amount_minor),
                currency=wallet.currency,
            )
            rows = await session.scalars(
                select(Transaction)
                .where(
                    or_(
                        Transaction.source_wallet_id == wallet.id,
                        Transaction.destination_wallet_id == wallet.id,
                    )
                )
                .order_by(Transaction.created_at.desc())
                .limit(10)
            )
            recent = [
                RecentTxnOut(
                    reference=t.reference,
                    type=t.type,
                    status=t.status,
                    amount=str(t.amount_minor),
                    currency=t.currency,
                    direction="OUT" if t.source_wallet_id == wallet.id else "IN",
                    created_at=t.created_at,
                )
                for t in rows.all()
            ]
        profile = await profile_of(session, user.id)
        flags = await list_flags(session, user.id)
        return UserDetailOut(
            **_summary(user, wallet),
            roles=sorted(await roles_for_user(session, user.id)),
            email_verified=user.email_verified_at is not None,
            last_login_at=user.last_login_at,
            kyc_status=profile.status if profile else None,
            wallet=wallet_out,
            risk_flags=[
                FlagOut(
                    flag=f.flag,
                    reason=f.reason,
                    created_at=f.created_at,
                    expires_at=f.expires_at,
                    removed_at=f.removed_at,
                )
                for f in flags
            ],
            recent_transactions=recent,
        )


@router.post("/{user_id}/pii-reveals", response_model=RevealedPiiOut)
async def reveal_pii(
    user_id: uuid.UUID, ctx: IdentityDep, meta: MetaDep, principal: Principal = CAN_REVEAL_PII
) -> RevealedPiiOut:
    async with ctx.db.transaction() as session:
        user = await _customer(session, user_id)
        await record(
            session,
            ctx.clock,
            AuditEntry(
                action="user.pii_revealed",
                actor_type="STAFF",
                actor_id=principal.user_id,
                target_type="user",
                target_id=str(user.id),
                details={"fields": ["email", "full_name", "phone"]},
                ip=meta.ip,
                user_agent=meta.user_agent,
            ),
        )
        return RevealedPiiOut(email=user.email, full_name=user.full_name, phone=user.phone)
