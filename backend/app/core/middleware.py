"""Pure ASGI middleware: correlation id + access log, and security headers."""

from __future__ import annotations

import re
import time

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import correlation_id_var
from app.core.ids import new_id

logger = structlog.get_logger("app.access")

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")


class CorrelationIdMiddleware:
    """Accepts a safe client ``X-Request-ID`` or generates one; echoes it and logs the request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope.get("headers", []):
            if name == REQUEST_ID_HEADER.encode():
                incoming = value.decode("latin-1")
                break
        correlation_id = (
            incoming if incoming and _VALID_REQUEST_ID.match(incoming) else (new_id().hex)
        )

        token = correlation_id_var.set(correlation_id)
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = correlation_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            logger.info(
                "http_request",
                method=scope.get("method"),
                path=scope.get("path"),
                route=getattr(route, "path", None),
                status=status_code,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            structlog.contextvars.unbind_contextvars("correlation_id")
            correlation_id_var.reset(token)


API_DOCS_PREFIXES = ("/api/docs", "/api/redoc")

SECURITY_HEADERS: dict[str, str] = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-site",
    "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


class SecurityHeadersMiddleware:
    """Adds defensive headers. API responses are JSON, so they get a deny-all CSP and no-store."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = str(scope.get("path", "")).startswith(API_DOCS_PREFIXES)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if not is_docs:
                    headers.setdefault("content-security-policy", API_CSP)
                headers.setdefault("cache-control", "no-store")
            await send(message)

        await self.app(scope, receive, send_wrapper)


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = "x-vw-csrf"


class CsrfProtectionMiddleware:
    """CSRF defence for cookie-authenticated APIs (ADR-0012).

    Every state-changing request under ``/api/`` must carry ``X-VW-CSRF: 1`` (a custom header
    cannot be sent cross-site without a CORS preflight, which only trusted origins pass), and
    when the browser sends ``Origin`` it must be a trusted origin. Webhooks are exempt: they
    are authenticated by signatures, not cookies.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        trusted_origins: frozenset[str],
        exempt_prefixes: tuple[str, ...] = ("/api/v1/webhooks/",),
    ) -> None:
        self.app = app
        self.trusted_origins = trusted_origins
        self.exempt_prefixes = exempt_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if not path.startswith("/api/") or path.startswith(self.exempt_prefixes):
            await self.app(scope, receive, send)
            return

        headers = {
            name.decode("latin-1"): value.decode("latin-1") for name, value in scope["headers"]
        }
        origin = headers.get("origin")
        if headers.get(CSRF_HEADER) != "1" or (
            origin is not None and origin.rstrip("/") not in self.trusted_origins
        ):
            logger.warning("csrf_rejected", path=path, origin=origin)
            # Imported lazily: errors -> context only, no cycle, but keeps this module light.
            from app.core.errors import negotiate_locale, problem_response  # noqa: PLC0415

            response = problem_response(
                status=403,
                code="CSRF_FAILED",
                locale=negotiate_locale(headers.get("accept-language")),
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
