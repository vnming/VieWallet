"""Error mapping and middleware, tested on a minimal app (no database)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from app.core.errors import (
    PROBLEM_JSON,
    FieldError,
    IdempotencyKeyReusedError,
    NotFoundError,
    ValidationFailedError,
    negotiate_locale,
    register_exception_handlers,
    title_for,
)
from app.core.idempotency import idempotency_key_header
from app.core.middleware import CorrelationIdMiddleware, SecurityHeadersMiddleware


class Body(BaseModel):
    amount: str = Field(pattern=r"^[0-9]+$")


def build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("wallet not found")

    @app.get("/invalid")
    async def invalid() -> None:
        raise ValidationFailedError(errors=[FieldError("amount", "TOO_LARGE", "too large")])

    @app.get("/reused")
    async def reused() -> None:
        raise IdempotencyKeyReusedError()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("database password=hunter2 leaked?")

    @app.post("/body")
    async def body(payload: Body) -> Body:
        return payload

    @app.post("/idem")
    async def idem(key: str = Depends(idempotency_key_header)) -> dict[str, str]:
        return {"key": key}

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


class TestLocale:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            (None, "vi"),
            ("", "vi"),
            ("vi-VN,vi;q=0.9", "vi"),
            ("en-US,en;q=0.8", "en"),
            ("fr", "vi"),
        ],
    )
    def test_negotiate(self, header: str | None, expected: str) -> None:
        assert negotiate_locale(header) == expected

    def test_unknown_code_falls_back_to_internal_error(self) -> None:
        assert title_for("NO_SUCH_CODE", "en") == "Internal server error"


class TestProblemDetails:
    async def test_app_error(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/missing", headers={"Accept-Language": "en"})
        assert response.status_code == 404
        assert response.headers["content-type"] == PROBLEM_JSON
        body = response.json()
        assert body["code"] == "NOT_FOUND"
        assert body["title"] == "Not found"
        assert body["detail"] == "wallet not found"
        assert body["type"].endswith("/not-found")
        assert body["correlation_id"] == response.headers["x-request-id"]

    async def test_vietnamese_is_default(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/reused")).json()
        assert body["code"] == "IDEMPOTENCY_KEY_REUSED"
        assert body["title"] == "Idempotency-Key đã được dùng cho yêu cầu khác"

    async def test_field_errors(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/invalid")).json()
        assert body["errors"] == [{"field": "amount", "code": "TOO_LARGE", "message": "too large"}]

    async def test_request_validation(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/body", json={"amount": "1.5"})
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "VALIDATION_ERROR"
        assert body["errors"][0]["field"] == "amount"

    async def test_unknown_route(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/nope")
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"

    async def test_method_not_allowed(self, client: httpx.AsyncClient) -> None:
        response = await client.delete("/ok")
        assert response.status_code == 405
        assert response.json()["code"] == "METHOD_NOT_ALLOWED"

    async def test_unexpected_error_hides_details(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/boom")
        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "INTERNAL_ERROR"
        assert "hunter2" not in response.text
        assert "detail" not in body


class TestIdempotencyHeader:
    async def test_missing(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/idem")
        assert response.status_code == 400
        assert response.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    @pytest.mark.parametrize("key", ["short", "has space here", "x" * 256, "key/with/slashes"])
    async def test_invalid(self, client: httpx.AsyncClient, key: str) -> None:
        response = await client.post("/idem", headers={"Idempotency-Key": key})
        assert response.status_code == 400
        assert response.json()["code"] == "IDEMPOTENCY_KEY_INVALID"

    async def test_valid(self, client: httpx.AsyncClient) -> None:
        key = str(uuid.uuid4())
        response = await client.post("/idem", headers={"Idempotency-Key": key})
        assert response.status_code == 200
        assert response.json() == {"key": key}


class TestMiddleware:
    async def test_generates_request_id(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/ok")
        assert len(response.headers["x-request-id"]) == 32

    async def test_echoes_safe_request_id(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/ok", headers={"X-Request-ID": "client-req-123456"})
        assert response.headers["x-request-id"] == "client-req-123456"

    @pytest.mark.parametrize(
        "bad", ["short", "bad id with spaces", "x" * 65, "<script>alert</script>"]
    )
    async def test_replaces_unsafe_request_id(self, client: httpx.AsyncClient, bad: str) -> None:
        response = await client.get("/ok", headers={"X-Request-ID": bad})
        assert response.headers["x-request-id"] != bad

    async def test_security_headers(self, client: httpx.AsyncClient) -> None:
        headers = (await client.get("/ok")).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["cache-control"] == "no-store"
        assert "default-src 'none'" in headers["content-security-policy"]

    async def test_error_responses_also_get_headers(self, client: httpx.AsyncClient) -> None:
        headers = (await client.get("/missing")).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert "x-request-id" in headers
