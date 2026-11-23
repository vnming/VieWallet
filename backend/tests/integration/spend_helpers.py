"""Helpers for transfer / payment / refund / reversal integration tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.core.money import Money
from app.modules.ledger.public import Journal
from app.modules.payments.public import create_merchant
from app.modules.risk.public import Decision, RiskResult, assess
from app.modules.transactions.public import Transaction
from app.modules.transactions.use_cases import spend
from tests.integration.identity_helpers import PIN
from tests.integration.money_helpers import customer, finance, fund_wallet, key

OTHER = "minh@example.com"


async def two_customers(
    client: httpx.AsyncClient,
    other_client: httpx.AsyncClient,
    app: FastAPI,
    *,
    funds: int = 3_000_000,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Lan (funded) and Minh, both signed in with a PIN. Returns their wallets."""
    lan = await customer(client, app)
    minh = await customer(other_client, app, email=OTHER)
    if funds:
        await fund_wallet(app, lan["id"], funds)
    return lan, minh


async def quote(
    client: httpx.AsyncClient,
    *,
    to: str | None = None,
    email: str | None = None,
    recipient_id: str | None = None,
    amount: int,
    note: str = "",
    idem: str | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"amount": str(amount), "note": note}
    if to is not None:
        body["wallet_number"] = to
    if email is not None:
        body["email"] = email
    if recipient_id is not None:
        body["recipient_id"] = recipient_id
    return await client.post(
        "/api/v1/transfers", headers={"Idempotency-Key": idem or key()}, json=body
    )


async def quoted(client: httpx.AsyncClient, to: str, amount: int, note: str = "") -> dict[str, Any]:
    r = await quote(client, to=to, amount=amount, note=note)
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


async def confirm(
    client: httpx.AsyncClient,
    reference: str,
    *,
    idem: str | None = None,
    pin: str = PIN,
    kind: str = "transfers",
) -> httpx.Response:
    return await client.post(
        f"/api/v1/{kind}/{reference}/confirm",
        headers={"Idempotency-Key": idem or key()},
        json={"pin": pin},
    )


async def send(client: httpx.AsyncClient, to: str, amount: int) -> dict[str, Any]:
    q = await quoted(client, to, amount)
    r = await confirm(client, q["reference"])
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def journal_count(app: FastAPI, reference: str) -> int:
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        txn_id = await session.scalar(
            select(Transaction.id).where(Transaction.reference == reference)
        )
        return int(
            await session.scalar(
                select(func.count()).select_from(Journal).where(Journal.transaction_id == txn_id)
            )
            or 0
        )


async def make_merchant(
    app: FastAPI, email: str = OTHER, name: str = "Cà phê Mây"
) -> dict[str, str]:
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        merchant = await create_merchant(
            session,
            ctx.clock,
            email=email,
            display_name=name,
            legal_name="Công ty Mây (hư cấu)",
            mcc="5814",
            reason="test",
        )
        return {"id": str(merchant.id), "wallet_id": str(merchant.wallet_id)}


@contextmanager
def risk_decision(monkeypatch: pytest.MonkeyPatch, decision: Decision | None) -> Iterator[None]:
    """Force the risk outcome of the spend pipeline (None: no rules at all)."""
    original = assess

    async def forced(session: Any, ctx: Any, *, transaction_id: uuid.UUID | None) -> RiskResult:
        result = await original(session, ctx, transaction_id=transaction_id, rules=())
        if decision is None:
            return result
        return RiskResult(score=80, decision=decision, hits=())

    monkeypatch.setattr(spend, "assess", forced)
    try:
        yield
    finally:
        monkeypatch.setattr(spend, "assess", original)


def vnd(amount: int) -> Money:
    return Money(amount, "VND")
