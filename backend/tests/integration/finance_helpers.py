"""Helpers for wallet / ledger / approval integration tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.db import Database
from app.core.ids import new_id
from app.core.money import Money
from app.modules.identity.public import User, create_staff
from app.modules.ledger.public import (
    AccountCode,
    JournalDraft,
    JournalKind,
    credit,
    debit,
    post,
    system_account,
)
from app.modules.wallet.public import Wallet, ensure_wallet
from tests.integration.identity_helpers import PASSWORD, services, totp_now


async def make_user(session: AsyncSession, clock: Clock, name: str = "Khách Thử") -> uuid.UUID:
    now = clock.now()
    user = User(
        id=new_id(),
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        email_verified_at=now,
        full_name=name,
        user_type="CUSTOMER",
        status="ACTIVE",
        locale="vi",
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    return user.id


async def new_wallet(db: Database, clock: Clock) -> Wallet:
    async with db.transaction() as session:
        return await ensure_wallet(session, clock, await make_user(session, clock))


async def fund(db: Database, clock: Clock, wallet: Wallet, amount: int) -> None:
    """Credit a wallet through the posting engine (adjustment journal, no approval: engine test)."""
    async with db.transaction() as session:
        expense = await system_account(session, AccountCode.ADJUSTMENT_EXPENSE, "VND")
        money = Money(amount, "VND")
        await post(
            session,
            clock,
            JournalDraft(
                JournalKind.ADJUSTMENT,
                (debit(expense.id, money), credit(wallet.ledger_account_id, money)),
            ),
            transaction_id=None,
            created_by="test",
        )


@asynccontextmanager
async def browser(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://viewallet.test", headers={"X-VW-CSRF": "1"}
    ) as http:
        yield http


async def sign_in_staff(client: httpx.AsyncClient, app: FastAPI, email: str, *roles: str) -> str:
    """Create a staff member, sign in and enroll TOTP. Returns the TOTP secret."""
    await create_staff(
        services(app), email=email, full_name="Nhân Viên Demo", password=PASSWORD, roles=roles
    )
    r = await client.post("/api/v1/admin/auth/login", json={"email": email, "password": PASSWORD})
    assert r.json() == {"next_step": "MFA_ENROLLMENT_REQUIRED"}, r.text
    secret = str((await client.post("/api/v1/admin/auth/mfa/enroll")).json()["secret"])
    r = await client.post("/api/v1/admin/auth/mfa/confirm", json={"code": totp_now(secret, app)})
    assert r.status_code == 200, r.text
    return secret
