from __future__ import annotations

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.deps import AppState


async def test_live(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_when_all_dependencies_are_up(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "migrations": "ok", "redis": "ok"},
    }


async def test_not_ready_when_migrations_are_behind(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    state: AppState = app.state.vw
    state.expected_migration_head = "9999"
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["migrations"] == "fail"


async def test_not_ready_when_redis_is_down(app: FastAPI, client: httpx.AsyncClient) -> None:
    state: AppState = app.state.vw
    await state.redis.aclose()
    state.redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    try:
        response = await client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["checks"] == {
            "database": "ok",
            "migrations": "ok",
            "redis": "fail",
        }
    finally:
        await state.redis.aclose()


async def test_system_status(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"
    assert body["version"] == "0.1.0"
    assert body["server_time"] == "2026-11-21T08:00:00Z"
    assert response.headers["cache-control"] == "no-store"


async def test_cors_preflight_for_allowed_origin(client: httpx.AsyncClient) -> None:
    response = await client.options(
        "/api/v1/system/status",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_cors_rejects_unknown_origin(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/system/status", headers={"Origin": "https://evil.test"})
    assert "access-control-allow-origin" not in response.headers


async def test_openapi_is_served_outside_production(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert "/api/v1/system/status" in response.json()["paths"]
