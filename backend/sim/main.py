"""Simulator HTTP service: provider API, SimBank checkout page, control endpoints.

Run: ``uvicorn --factory sim.main:app_factory --port 8090``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from html import escape
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
import structlog
from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sim.config import SimSettings, get_sim_settings
from sim.db import SimClock, SimDatabase, SystemSimClock
from sim.scenarios import Mode
from sim.service import ConnectionReset, Sender, SimError, Simulator

logger = structlog.get_logger("sim")

AMOUNT_PATTERN = r"^[1-9][0-9]{0,15}$"


class ChargeIn(BaseModel):
    operation_key: str = Field(min_length=8, max_length=64)
    amount: str = Field(pattern=AMOUNT_PATTERN)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    reference: str = Field(max_length=64)
    method: str = Field(default="SIMBANK", max_length=20)
    callback_url: str = Field(max_length=500)
    return_url: str | None = Field(default=None, max_length=500)


class Destination(BaseModel):
    bank_code: str = Field(max_length=20)
    account_number: str = Field(pattern=r"^[0-9]{6,20}$")
    account_name: str = Field(max_length=120)


class PayoutIn(BaseModel):
    operation_key: str = Field(min_length=8, max_length=64)
    amount: str = Field(pattern=AMOUNT_PATTERN)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    reference: str = Field(max_length=64)
    callback_url: str = Field(max_length=500)
    destination: Destination


class ModeIn(BaseModel):
    mode: Mode


class ActionIn(BaseModel):
    action: str = Field(pattern=r"^(SUCCEED|FAIL|EXPIRE|RETURN|RESEND_CALLBACK)$")


class ConnectionResetMiddleware:
    """Turns ``ConnectionReset`` into a truncated response, i.e. a broken connection."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except ConnectionReset:
            if started:
                raise
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", b"64")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"partial', "more_body": False})


def _simulator(request: Request) -> Simulator:
    value: Simulator = request.app.state.simulator
    return value


SimDep = Annotated[Simulator, Depends(_simulator)]


def create_sim_app(
    settings: SimSettings | None = None,
    *,
    clock: SimClock | None = None,
    sender: Sender | None = None,
    run_worker: bool = True,
) -> FastAPI:
    settings = settings or get_sim_settings()
    clock = clock or SystemSimClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = SimDatabase(settings.database_url)
        client = httpx.AsyncClient(timeout=settings.callback_timeout_seconds)

        async def http_sender(url: str, body: bytes, headers: dict[str, str]) -> int:
            response = await client.post(url, content=body, headers=headers)
            return response.status_code

        simulator = Simulator(db, settings, clock, sender or http_sender)
        app.state.simulator = simulator
        stop = asyncio.Event()

        async def loop() -> None:
            while not stop.is_set():
                try:
                    await simulator.run_once()
                except Exception:
                    logger.exception("sim_worker_error")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_interval_seconds)

        task = asyncio.create_task(loop()) if run_worker else None
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                await task
            await client.aclose()
            await db.dispose()

    app = FastAPI(
        title="VieWallet Payment Provider Simulator",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(ConnectionResetMiddleware)

    @app.exception_handler(SimError)
    async def sim_error(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, SimError)  # noqa: S101
        return JSONResponse({"code": exc.code, "detail": exc.detail}, status_code=exc.status)

    def require_api_key(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {settings.api_key}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise _Unauthorized()

    def require_admin(x_sim_admin_key: Annotated[str | None, Header()] = None) -> None:
        if not settings.controls_enabled:
            raise _NotFoundError()
        if not x_sim_admin_key or not hmac.compare_digest(x_sim_admin_key, settings.admin_key):
            raise _Unauthorized()

    ApiKey = Depends(require_api_key)
    AdminKey = Depends(require_admin)

    async def respond(result_body: dict[str, Any], delay: float, status: int) -> JSONResponse:
        if delay:
            await asyncio.sleep(delay)
        return JSONResponse(result_body, status_code=status)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/charges", dependencies=[ApiKey])
    async def create_charge(body: ChargeIn, simulator: SimDep) -> JSONResponse:
        result = await simulator.create_charge(body.model_dump())
        return await respond(result.body, result.delay_seconds, 201)

    @app.post("/v1/payouts", dependencies=[ApiKey])
    async def create_payout(body: PayoutIn, simulator: SimDep) -> JSONResponse:
        result = await simulator.create_payout(body.model_dump())
        return await respond(result.body, result.delay_seconds, 201)

    @app.get("/v1/operations/{operation_key}", dependencies=[ApiKey])
    async def get_operation(operation_key: str, simulator: SimDep) -> dict[str, Any]:
        return await simulator.get_operation(operation_key)

    @app.get("/v1/statements/{business_date}", dependencies=[ApiKey])
    async def statement(business_date: date, simulator: SimDep) -> dict[str, Any]:
        return {
            "business_date": business_date.isoformat(),
            "lines": await simulator.statement(business_date),
        }

    # ---------------------------------------------------------------- SimBank checkout

    def page(title: str, body: str) -> HTMLResponse:
        html = (
            "<!doctype html><html lang='vi'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)} · SimBank</title><style>"
            "body{font-family:system-ui,sans-serif;background:#f4f6f8;margin:0;padding:24px;color:#14213d}"
            "main{max-width:420px;margin:0 auto;background:#fff;border-radius:12px;padding:24px;"
            "box-shadow:0 1px 3px #0002}h1{font-size:1.25rem}.amount{font-size:2rem;font-weight:700}"
            ".note{background:#fff7e0;padding:8px 12px;border-radius:8px;font-size:.85rem}"
            "button{font:inherit;padding:12px 16px;border-radius:8px;border:0;margin-right:8px;cursor:pointer}"
            ".ok{background:#0b7a53;color:#fff}.no{background:#e5e7eb}"
            "label{display:block;margin:12px 0 4px}input{font:inherit;padding:10px;width:100%;box-sizing:border-box}"
            "</style></head><body><main>"
            "<p class='note'>SimBank — ngân hàng mô phỏng. Không có tiền thật, không nhập thông tin thật.</p>"
            f"{body}</main></body></html>"
        )
        return HTMLResponse(
            html,
            headers={
                # form-action also governs the redirect back to the wallet after submitting.
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                f"form-action 'self' {' '.join(settings.allowed_return_origins)}; "
                "frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            },
        )

    def safe_return(url: str | None) -> str | None:
        if not url:
            return None
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        return url if origin in settings.allowed_return_origins else None

    @app.get("/checkout/{reference}", response_class=HTMLResponse)
    async def checkout(reference: str, simulator: SimDep) -> HTMLResponse:
        async with simulator.db.transaction() as session:
            op = await simulator.by_reference(session, reference)
        amount = f"{op.amount_minor:,}".replace(",", ".")
        if op.status != "AWAITING_CUSTOMER":
            return page(
                "Giao dịch",
                f"<h1>Giao dịch đã được xử lý</h1><p>Trạng thái: {escape(op.status)}</p>",
            )
        ref = escape(op.provider_reference)
        return page(
            "Thanh toán",
            f"<h1>Xác nhận thanh toán</h1><p>Mã giao dịch: {escape(op.reference)}</p>"
            f"<p class='amount'>{amount} ₫</p>"
            f"<form method='post' action='/checkout/{ref}/approve'>"
            "<label for='u'>Tên đăng nhập SimBank (tùy ý)</label><input id='u' name='username' autocomplete='off'>"
            "<p><button class='ok' type='submit'>Duyệt thanh toán</button>"
            f"<button class='no' type='submit' formaction='/checkout/{ref}/decline'>Từ chối</button></p></form>",
        )

    async def decide(reference: str, simulator: Simulator, *, approve: bool) -> Response:
        op = await simulator.checkout_decision(reference, approve=approve)
        target = safe_return(op.return_url)
        if target:
            return RedirectResponse(target, status_code=303)
        verdict = "Đã duyệt" if approve else "Đã từ chối"
        return page("Kết quả", f"<h1>{verdict}</h1><p>Bạn có thể quay lại ứng dụng.</p>")

    @app.post("/checkout/{reference}/approve")
    async def approve(reference: str, simulator: SimDep) -> Response:
        return await decide(reference, simulator, approve=True)

    @app.post("/checkout/{reference}/decline")
    async def decline(reference: str, simulator: SimDep) -> Response:
        return await decide(reference, simulator, approve=False)

    # ---------------------------------------------------------------- controls

    @app.get("/v1/admin/settings", dependencies=[AdminKey])
    async def get_settings_(simulator: SimDep) -> dict[str, str]:
        async with simulator.db.transaction() as session:
            return {"mode": (await simulator.mode(session)).value}

    @app.put("/v1/admin/settings", dependencies=[AdminKey])
    async def put_settings(body: ModeIn, simulator: SimDep) -> dict[str, str]:
        await simulator.set_mode(body.mode)
        return {"mode": body.mode.value}

    @app.get("/v1/admin/operations", dependencies=[AdminKey])
    async def admin_operations(simulator: SimDep) -> dict[str, Any]:
        return {"items": await simulator.list_operations()}

    @app.post("/v1/admin/operations/{operation_key}/actions", dependencies=[AdminKey])
    async def admin_action(operation_key: str, body: ActionIn, simulator: SimDep) -> dict[str, Any]:
        return await simulator.admin_action(operation_key, body.action)

    @app.get("/v1/admin/logs", dependencies=[AdminKey])
    async def admin_logs(simulator: SimDep) -> dict[str, Any]:
        return {"items": await simulator.list_logs()}

    return app


class _Unauthorized(SimError):
    status = 401
    code = "UNAUTHORIZED"


class _NotFoundError(SimError):
    status = 404
    code = "NOT_FOUND"


def app_factory() -> FastAPI:
    return create_sim_app()
