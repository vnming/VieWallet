"""E-mail channel: request through the outbox, deliver from the worker.

Secrets that must appear in an e-mail (verification codes, reset links) are encrypted with
the data cipher before they are written to the outbox, so a database reader cannot use
them. The worker decrypts them only to render the message.
"""

from __future__ import annotations

import asyncio
import json
import smtplib
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.crypto import DataCipher
from app.core.outbox import HandlerRegistry, OutboxMessage, publish
from app.modules.notifications.templates import TEMPLATES, render

logger = structlog.get_logger(__name__)

EMAIL_TOPIC = "notifications.email.requested"
_SECRET_AAD = "notifications.email"  # noqa: S105 - AEAD context label, not a secret


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    async def send(self, email: OutgoingEmail) -> None: ...


class SmtpEmailSender:
    """Plain SMTP (Mailpit in development). Runs the blocking client in a thread."""

    def __init__(self, host: str, port: int, sender: str, *, timeout: float = 10.0) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._timeout = timeout

    def _send_blocking(self, email: OutgoingEmail) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = email.to
        message["Subject"] = email.subject
        message.set_content(email.body)
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            smtp.send_message(message)

    async def send(self, email: OutgoingEmail) -> None:
        await asyncio.to_thread(self._send_blocking, email)


@dataclass
class RecordingEmailSender:
    """Test double that keeps sent messages in memory."""

    sent: list[OutgoingEmail] = field(default_factory=list)

    async def send(self, email: OutgoingEmail) -> None:
        self.sent.append(email)


async def request_email(
    session: AsyncSession,
    clock: Clock,
    cipher: DataCipher,
    *,
    to: str,
    template: str,
    locale: str,
    params: Mapping[str, str] | None = None,
    secret_params: Mapping[str, str] | None = None,
    aggregate_id: str,
) -> None:
    """Queue an e-mail in the caller's transaction."""
    if template not in TEMPLATES:
        raise KeyError(f"unknown e-mail template {template}")
    payload: dict[str, Any] = {
        "to": to,
        "template": template,
        "locale": locale,
        "params": dict(params or {}),
    }
    if secret_params:
        payload["secret"] = cipher.encrypt(
            json.dumps(dict(secret_params)), associated_data=_SECRET_AAD
        )
    await publish(
        session,
        clock,
        topic=EMAIL_TOPIC,
        aggregate_type="user",
        aggregate_id=aggregate_id,
        payload=payload,
    )


def build_email_handler(cipher: DataCipher, sender: EmailSender):  # type: ignore[no-untyped-def]
    async def handle(message: OutboxMessage) -> None:
        payload = message.payload
        params: dict[str, str] = dict(payload.get("params", {}))
        if secret := payload.get("secret"):
            params |= json.loads(cipher.decrypt(secret, associated_data=_SECRET_AAD))
        subject, body = render(payload["template"], payload.get("locale", "vi"), params)
        await sender.send(OutgoingEmail(to=payload["to"], subject=subject, body=body))
        logger.info("email_sent", template=payload["template"], event_id=str(message.id))

    return handle


def register_handlers(registry: HandlerRegistry, cipher: DataCipher, sender: EmailSender) -> None:
    registry.register(EMAIL_TOPIC, build_email_handler(cipher, sender))
