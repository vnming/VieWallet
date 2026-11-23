"""Helpers for deposit/withdrawal integration tests (real simulator over ASGI)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import select, text

from app.core.clock import FixedClock
from app.core.outbox import OutboxDispatcher
from app.modules.identity.public import set_kyc_tier
from app.modules.ledger.public import check_invariants
from app.modules.notifications.public import RecordingEmailSender
from app.modules.operations.public import run_internal
from app.modules.providers.public import ProviderCallbackResult, expected_signature
from app.modules.transactions.public import FinanceContext
from app.modules.wallet.public import get_wallet
from app.worker import build_registry
from tests.integration.conftest import SIM_ADMIN_KEY, SIM_WEBHOOK_SECRET
from tests.integration.finance_helpers import fund
from tests.integration.identity_helpers import PASSWORD, PIN, signed_in_customer

WEBHOOK = "/api/v1/webhooks/providers/simulator"


def finance(app: FastAPI) -> FinanceContext:
    ctx: FinanceContext = app.state.finance
    return ctx


async def drain(app: FastAPI, *, rounds: int = 20) -> None:
    """Run every due outbox event through the worker's real handler registry."""
    ctx = finance(app)
    registry = build_registry(ctx.settings, ctx, email_sender=RecordingEmailSender())
    dispatcher = OutboxDispatcher(ctx.db, registry, ctx.clock, batch_size=100)
    for _ in range(rounds):
        if not await dispatcher.run_once():
            return


async def sim_tick(sim_app: FastAPI) -> int:
    count: int = await sim_app.state.simulator.run_once()
    return count


async def settle(app: FastAPI, sim_app: FastAPI, *, rounds: int = 5) -> None:
    """Let the simulator act and deliver callbacks, then process them, until quiet."""
    for _ in range(rounds):
        await drain(app)
        if not await sim_tick(sim_app):
            await drain(app)
            return


async def customer(
    client: httpx.AsyncClient, app: FastAPI, email: str = "lan@example.com"
) -> dict[str, Any]:
    await signed_in_customer(client, app, email=email)
    r = await client.post("/api/v1/me/pin", json={"password": PASSWORD, "pin": PIN})
    assert r.status_code == 204, r.text
    wallet: dict[str, Any] = (await client.get("/api/v1/wallet")).json()
    return wallet


async def make_tier1(app: FastAPI, email: str = "lan@example.com") -> None:
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        await set_kyc_tier(session, ctx.clock, email=email, tier=1, reason="test")


async def fund_wallet(app: FastAPI, wallet_id: str, amount: int) -> None:
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        wallet = await get_wallet(session, uuid.UUID(wallet_id))
    await fund(ctx.db, ctx.clock, wallet, amount)


async def balance(client: httpx.AsyncClient) -> int:
    return int((await client.get("/api/v1/wallet")).json()["balance"]["amount"])


def key() -> str:
    return f"k-{uuid.uuid4().hex}"


async def deposit(
    client: httpx.AsyncClient, amount: int, idem: str | None = None
) -> httpx.Response:
    return await client.post(
        "/api/v1/deposits", headers={"Idempotency-Key": idem or key()}, json={"amount": str(amount)}
    )


async def approve_checkout(
    sim_app: FastAPI, checkout_url: str, *, approve: bool = True
) -> httpx.Response:
    reference = checkout_url.rsplit("/", 1)[-1]
    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://simulator.test") as http:
        page = await http.get(f"/checkout/{reference}")
        assert page.status_code == 200
        action = "approve" if approve else "decline"
        return await http.post(f"/checkout/{reference}/{action}")


async def sim_admin(
    sim_app: FastAPI, method: str, path: str, body: dict[str, Any] | None = None
) -> httpx.Response:
    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://simulator.test") as http:
        return await http.request(
            method, path, json=body, headers={"X-Sim-Admin-Key": SIM_ADMIN_KEY}
        )


def signed_callback(
    clock: FixedClock, payload: dict[str, Any], *, secret: str = SIM_WEBHOOK_SECRET
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(clock.now().timestamp()))
    return body, {
        "Content-Type": "application/json",
        "X-Sim-Timestamp": timestamp,
        "X-Sim-Signature": expected_signature(secret, timestamp, body),
    }


async def operation_key_of(app: FastAPI, reference: str) -> str:
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        value = await session.scalar(
            text(
                "SELECT o.id::text FROM app.provider_operations o JOIN app.transactions t"
                " ON t.id = o.transaction_id WHERE t.reference = :r"
            ),
            {"r": reference},
        )
    assert value
    return str(value)


def marker() -> uuid.UUID:
    """UUIDv7 'now': append-only results created after it belong to the current test."""
    return uuid.uuid7()


async def callback_results(app: FastAPI, since: uuid.UUID) -> list[str]:
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        rows = await session.scalars(
            select(ProviderCallbackResult.result)
            .where(ProviderCallbackResult.id > since)
            .order_by(ProviderCallbackResult.id)
        )
        return list(rows.all())


async def assert_books_clean(app: FastAPI) -> None:
    ctx = finance(app)
    async with ctx.db.transaction() as session:
        assert await check_invariants(session) == []
        run = await run_internal(session, ctx.clock, triggered_by=None)
        assert run.run.violations == 0, [
            (i.invariant, i.subject, i.expected, i.actual) for i in run.items
        ]


def reference_in(url: str) -> str:
    match = re.search(r"(VW\d{6}[0-9A-Z]{10})", url)
    assert match, url
    return match.group(1)
