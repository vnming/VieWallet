"""Simulator service: scenarios, idempotency, statements, controls, and a contract test of the
VieWallet adapter against a real HTTP server (timeouts and dropped connections)."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import date

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from app.modules.providers.public import (
    ChargeRequest,
    OperationNotFound,
    Outcome,
    PayoutRequest,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
    SimulatorProvider,
)
from sim.config import SimSettings
from sim.main import create_sim_app
from sim.scenarios import Scenario, scenario_for, trigger_code
from tests.integration.conftest import SIM_ADMIN_KEY, SIM_API_KEY, SIM_WEBHOOK_SECRET

AUTH = {"Authorization": f"Bearer {SIM_API_KEY}"}


def charge(amount: str = "250000", key: str | None = None, **extra: str) -> dict[str, str]:
    return {
        "operation_key": key or uuid.uuid4().hex,
        "amount": amount,
        "currency": "VND",
        "reference": "VW261121AAAAAAAAAA",
        "callback_url": "http://viewallet.test/api/v1/webhooks/providers/simulator",
        "return_url": "http://127.0.0.1:8080/deposits/VW261121AAAAAAAAAA",
        **extra,
    }


@pytest.fixture
async def sim(sim_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://simulator.test") as http:
        yield http


async def test_scenario_triggers() -> None:
    assert trigger_code(1_111_000) == 111
    assert trigger_code(2_500_000) == 500
    assert scenario_for(1_111_000) is Scenario.FAIL
    assert scenario_for(250_000) is Scenario.SUCCESS
    assert scenario_for(3_999_000) is Scenario.RETURNED


async def test_auth_and_idempotency(sim: httpx.AsyncClient) -> None:
    assert (await sim.post("/v1/charges", json=charge())).status_code == 401
    assert (
        await sim.post("/v1/charges", json=charge(), headers={"Authorization": "Bearer x"})
    ).status_code == 401
    body = charge()
    first = await sim.post("/v1/charges", json=body, headers=AUTH)
    assert first.status_code == 201
    assert first.json()["status"] == "PENDING"
    concurrent = await asyncio.gather(
        *(sim.post("/v1/charges", json=body, headers=AUTH) for _ in range(5))
    )
    assert {r.json()["provider_reference"] for r in concurrent} == {
        first.json()["provider_reference"]
    }
    conflict = await sim.post("/v1/charges", json={**body, "amount": "1000"}, headers=AUTH)
    assert conflict.status_code == 409
    assert (await sim.get(f"/v1/operations/{body['operation_key']}", headers=AUTH)).json()[
        "status"
    ] == "PENDING"
    assert (await sim.get("/v1/operations/unknown-key", headers=AUTH)).status_code == 404
    assert (await sim.get("/health")).json() == {"status": "ok"}


async def test_checkout_page(sim: httpx.AsyncClient) -> None:
    body = charge(reference="<script>alert(1)</script>", return_url="https://evil.example/steal")
    created = (await sim.post("/v1/charges", json=body, headers=AUTH)).json()
    ref = created["checkout_url"].rsplit("/", 1)[-1]
    page = await sim.get(f"/checkout/{ref}")
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    # Untrusted return URLs are not followed (no open redirect through SimBank).
    approved = await sim.post(f"/checkout/{ref}/approve")
    assert approved.status_code == 200
    assert "Đã duyệt" in approved.text
    again = await sim.post(f"/checkout/{ref}/approve")
    assert again.status_code == 409
    assert "đã được xử lý" in (await sim.get(f"/checkout/{ref}")).text
    assert (await sim.get("/checkout/NOPE")).status_code == 404


async def test_statement_and_controls(sim: httpx.AsyncClient, sim_app: FastAPI) -> None:
    mismatch = charge(amount="1888000")
    await sim.post("/v1/charges", json=mismatch, headers=AUTH)
    lines = (await sim.get("/v1/statements/2026-11-21", headers=AUTH)).json()["lines"]
    assert [line["amount"] for line in lines] == ["1889000"]  # scenario 888: injected error
    assert (await sim.get(f"/v1/statements/{date(2026, 1, 1)}", headers=AUTH)).json()["lines"] == []

    assert (await sim.get("/v1/admin/settings")).status_code == 401
    admin = {"X-Sim-Admin-Key": SIM_ADMIN_KEY}
    assert (
        await sim.put("/v1/admin/settings", json={"mode": "DEGRADED"}, headers=admin)
    ).json() == {"mode": "DEGRADED"}
    assert (await sim.get("/v1/admin/settings", headers=admin)).json() == {"mode": "DEGRADED"}
    await sim.put("/v1/admin/settings", json={"mode": "NORMAL"}, headers=admin)
    action = await sim.post(
        f"/v1/admin/operations/{mismatch['operation_key']}/actions",
        json={"action": "RETURN"},
        headers=admin,
    )
    assert action.status_code == 409
    resend = await sim.post(
        f"/v1/admin/operations/{mismatch['operation_key']}/actions",
        json={"action": "RESEND_CALLBACK"},
        headers=admin,
    )
    assert resend.status_code == 409  # nothing was sent yet
    assert (await sim.get("/v1/admin/logs", headers=admin)).status_code == 200


async def test_controls_disabled_outside_simulation(sim_settings: SimSettings) -> None:
    settings = sim_settings.model_copy(update={"environment": "production-like"})
    app = create_sim_app(settings, run_worker=False)
    from asgi_lifespan import LifespanManager  # noqa: PLC0415

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://s") as http:
            r = await http.get("/v1/admin/settings", headers={"X-Sim-Admin-Key": SIM_ADMIN_KEY})
    assert r.status_code == 404


# ---------------------------------------------------------------- real HTTP contract test


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def live_sim(sim_settings: SimSettings) -> AsyncIterator[str]:
    port = _free_port()
    settings = sim_settings.model_copy(update={"slow_response_seconds": 2.0})
    server = uvicorn.Server(
        uvicorn.Config(
            create_sim_app(settings, run_worker=False),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
        )
    )
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=10)


async def test_adapter_against_a_real_server(live_sim: str) -> None:
    adapter = SimulatorProvider.build(
        live_sim, api_key=SIM_API_KEY, webhook_secret=SIM_WEBHOOK_SECRET, read_timeout=0.5
    )
    try:

        def request(amount: int) -> ChargeRequest:
            return ChargeRequest(
                operation_key=str(uuid.uuid4()),
                amount_minor=amount,
                currency="VND",
                reference="VW261121AAAAAAAAAA",
                method="SIMBANK",
                callback_url="http://127.0.0.1:9/cb",
                return_url="http://127.0.0.1:8080/x",
            )

        ok = await adapter.create_charge(request(250_000))
        assert ok.outcome is Outcome.PENDING
        assert ok.checkout_url

        slow = request(1_333_000)  # scenario 333: accepted, answer arrives too late
        with pytest.raises(ProviderTimeout):
            await adapter.create_charge(slow)
        assert (await adapter.get_operation(slow.operation_key)).outcome is Outcome.PENDING

        reset = request(1_444_000)  # scenario 444: connection dropped, nothing created
        with pytest.raises(ProviderTimeout):
            await adapter.create_charge(reset)
        with pytest.raises(OperationNotFound):
            await adapter.get_operation(reset.operation_key)

        with pytest.raises(ProviderUnavailable):
            await adapter.create_charge(request(1_777_000))
        payout = PayoutRequest(
            operation_key=str(uuid.uuid4()),
            amount_minor=1_111_000,
            currency="VND",
            reference="VW261121BBBBBBBBBB",
            callback_url="http://127.0.0.1:9/cb",
            bank_code="SIMBANK",
            account_number="0123456789",
            account_name="LAN",
        )
        assert (await adapter.create_payout(payout)).outcome is Outcome.FAILED
        with pytest.raises(ProviderRejected):
            await adapter.create_charge(
                ChargeRequest(
                    payout.operation_key,
                    5_000,
                    "VND",
                    "VWX",
                    "SIMBANK",
                    "http://x/cb",
                    "http://x/r",
                )
            )
    finally:
        await adapter.aclose()
    down = SimulatorProvider.build(
        "http://127.0.0.1:9", api_key="k", webhook_secret="s", connect_timeout=0.5
    )
    with pytest.raises(ProviderUnavailable):
        await down.create_charge(
            ChargeRequest(str(uuid.uuid4()), 1000, "VND", "VWX", "SIMBANK", "http://x", "http://x")
        )
    await down.aclose()
