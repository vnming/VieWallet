"""Transaction PIN: storage, policy, lockout and reset (FR-PIN, FT-11)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.clock import FixedClock
from app.modules.identity.context import RequestMeta
from app.modules.identity.errors import PinInvalidError, PinLockedError, PinNotSetError
from app.modules.identity.models import TransactionPin, User
from app.modules.identity.public import verify_pin
from tests.integration.identity_helpers import (
    NEW_PIN,
    PASSWORD,
    PIN,
    code_in,
    deliver_emails,
    last_to,
    services,
    signed_in_customer,
)


async def user_id(app: FastAPI) -> uuid.UUID:
    async with services(app).db.transaction() as session:
        uid = await session.scalar(select(User.id).where(User.email == "lan@example.com"))
    assert uid is not None
    return uid


async def set_pin(client: httpx.AsyncClient, pin: str = PIN) -> httpx.Response:
    return await client.post("/api/v1/me/pin", json={"password": PASSWORD, "pin": pin})


async def test_set_pin_requires_password_and_strong_pin(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await signed_in_customer(client, app)
    wrong_password = await client.post("/api/v1/me/pin", json={"password": "sai", "pin": PIN})
    assert wrong_password.json()["code"] == "PASSWORD_INCORRECT"
    weak = await set_pin(client, "123456")
    assert weak.json()["code"] == "PIN_WEAK"
    malformed = await set_pin(client, "12ab56")
    assert malformed.status_code == 422
    assert (await set_pin(client)).status_code == 204
    assert (await set_pin(client, NEW_PIN)).json()["code"] == "PIN_ALREADY_SET"
    assert (await client.get("/api/v1/me")).json()["pin"]["is_set"] is True


async def test_pin_is_stored_peppered_and_hashed(client: httpx.AsyncClient, app: FastAPI) -> None:
    await signed_in_customer(client, app)
    await set_pin(client)
    ctx = services(app)
    async with ctx.db.transaction() as session:
        row = await session.scalar(select(TransactionPin))
    assert row is not None
    assert PIN not in row.pin_hash
    assert row.pin_hash.startswith("$argon2id$")
    assert not ctx.hasher.verify(row.pin_hash, PIN)  # the raw PIN alone does not verify
    assert ctx.hasher.verify(row.pin_hash, ctx.pin_input(PIN))


async def test_wrong_pins_lock_and_failures_are_persisted(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    await signed_in_customer(client, app)
    await set_pin(client)
    ctx, uid, meta = services(app), await user_id(app), RequestMeta(ip="127.0.0.1")
    remaining = []
    for _ in range(4):
        with pytest.raises(PinInvalidError) as exc_info:
            await verify_pin(ctx, user_id=uid, pin="999999", meta=meta)
        remaining.append(exc_info.value.remaining_attempts)
    assert remaining == [4, 3, 2, 1]
    with pytest.raises(PinLockedError):
        await verify_pin(ctx, user_id=uid, pin="999999", meta=meta)
    with pytest.raises(PinLockedError):  # correct PIN is refused while locked
        await verify_pin(ctx, user_id=uid, pin=PIN, meta=meta)
    assert "PIN giao dịch bị khóa" in last_to(await deliver_emails(app), "lan@example.com").subject

    clock.advance(timedelta(minutes=31))
    await verify_pin(ctx, user_id=uid, pin=PIN, meta=meta)  # unlocked; success resets the counter
    async with ctx.db.transaction() as session:
        row = await session.get(TransactionPin, uid)
    assert row is not None
    assert row.failed_attempts == 0


async def test_change_pin_counts_wrong_current_pin(client: httpx.AsyncClient, app: FastAPI) -> None:
    await signed_in_customer(client, app)
    await set_pin(client)
    bad = await client.put("/api/v1/me/pin", json={"current_pin": "999999", "new_pin": NEW_PIN})
    assert bad.json()["code"] == "PIN_INVALID"
    assert bad.json()["detail"] == "remaining_attempts=4"
    ok = await client.put("/api/v1/me/pin", json={"current_pin": PIN, "new_pin": NEW_PIN})
    assert ok.status_code == 204
    await verify_pin(services(app), user_id=await user_id(app), pin=NEW_PIN, meta=RequestMeta())


async def test_third_lock_requires_reset_then_reset_flow(
    client: httpx.AsyncClient, app: FastAPI, clock: FixedClock
) -> None:
    await signed_in_customer(client, app)
    await set_pin(client)
    ctx, uid, meta = services(app), await user_id(app), RequestMeta()
    for _ in range(3):
        for _ in range(5):
            with pytest.raises((PinInvalidError, PinLockedError)):
                await verify_pin(ctx, user_id=uid, pin="999999", meta=meta)
        clock.advance(timedelta(minutes=31))
    with pytest.raises(PinLockedError) as exc_info:
        await verify_pin(ctx, user_id=uid, pin=PIN, meta=meta)
    assert exc_info.value.requires_reset
    status = (await client.get("/api/v1/me")).json()["pin"]
    assert status["requires_reset"] is True

    assert (
        await client.post("/api/v1/me/pin/reset/request", json={"password": PASSWORD})
    ).status_code == 202
    code = code_in(last_to(await deliver_emails(app), "lan@example.com"))
    weak = await client.post(
        "/api/v1/me/pin/reset/confirm", json={"code": code, "new_pin": "111111"}
    )
    assert weak.json()["code"] == "PIN_WEAK"
    ok = await client.post("/api/v1/me/pin/reset/confirm", json={"code": code, "new_pin": NEW_PIN})
    assert ok.status_code == 204
    await verify_pin(ctx, user_id=uid, pin=NEW_PIN, meta=meta)
    async with ctx.db.transaction() as session:
        row = await session.get(TransactionPin, uid)
    assert row is not None
    assert row.reset_at == clock.now()  # later phases apply a cooldown after a reset


async def test_verify_without_pin(client: httpx.AsyncClient, app: FastAPI) -> None:
    await signed_in_customer(client, app)
    with pytest.raises(PinNotSetError):
        await verify_pin(services(app), user_id=await user_id(app), pin=PIN, meta=RequestMeta())
    r = await client.post("/api/v1/me/pin/reset/request", json={"password": PASSWORD})
    assert r.json()["code"] == "PIN_NOT_SET"
