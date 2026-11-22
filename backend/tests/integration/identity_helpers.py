"""Helpers for identity integration tests."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import pyotp
from fastapi import FastAPI

from app.core.outbox import HandlerRegistry, OutboxDispatcher
from app.modules.identity.context import IdentityServices
from app.modules.notifications.public import OutgoingEmail, RecordingEmailSender, register_handlers

PASSWORD = "mây-trắng-bay-2026"
NEW_PASSWORD = "biển-xanh-sóng-vỗ-99"
PIN = "270419"
NEW_PIN = "835162"


def services(app: FastAPI) -> IdentityServices:
    ctx: IdentityServices = app.state.identity
    return ctx


async def deliver_emails(app: FastAPI) -> list[OutgoingEmail]:
    """Run the worker's e-mail handler over pending outbox events; return what was 'sent'."""
    ctx = services(app)
    sender = RecordingEmailSender()
    registry = HandlerRegistry()
    register_handlers(registry, ctx.cipher, sender)
    dispatcher = OutboxDispatcher(ctx.db, registry, ctx.clock, batch_size=100)
    while await dispatcher.run_once():
        pass
    return sender.sent


def code_in(email: OutgoingEmail) -> str:
    match = re.search(r"\b(\d{6})\b", email.body)
    assert match, email.body
    return match.group(1)


def link_token_in(email: OutgoingEmail) -> str:
    match = re.search(r"token=([A-Za-z0-9_\-]+)", email.body)
    assert match, email.body
    return match.group(1)


def last_to(emails: list[OutgoingEmail], address: str) -> OutgoingEmail:
    matching = [e for e in emails if e.to == address]
    assert matching, f"no e-mail to {address}: {[e.to for e in emails]}"
    return matching[-1]


async def register_and_verify(
    client: httpx.AsyncClient,
    app: FastAPI,
    *,
    email: str = "lan@example.com",
    password: str = PASSWORD,
    full_name: str = "Trần Thị Lan",
) -> None:
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password, "full_name": full_name}
    )
    assert response.status_code == 202, response.text
    code = code_in(last_to(await deliver_emails(app), email))
    response = await client.post("/api/v1/auth/verify-email", json={"email": email, "code": code})
    assert response.status_code == 204, response.text


async def login(
    client: httpx.AsyncClient, *, email: str = "lan@example.com", password: str = PASSWORD
) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def signed_in_customer(
    client: httpx.AsyncClient, app: FastAPI, *, email: str = "lan@example.com"
) -> None:
    await register_and_verify(client, app, email=email)
    response = await login(client, email=email)
    assert response.status_code == 200, response.text


def totp_now(secret: str, app: FastAPI, *, offset_steps: int = 0) -> str:
    now: datetime = services(app).clock.now().astimezone(UTC)
    return pyotp.TOTP(secret).at(int(now.timestamp()) + offset_steps * 30)
