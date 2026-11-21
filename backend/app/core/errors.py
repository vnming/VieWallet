"""Domain errors and their mapping to RFC 9457 Problem Details responses.

Every error has a stable machine-readable ``code`` (shared with the frontend) and a localized
title. Internal details (stack traces, SQL) never reach the client.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_correlation_id

logger = structlog.get_logger(__name__)

PROBLEM_JSON = "application/problem+json"
ERROR_TYPE_BASE = "https://docs.viewallet.local/errors/"

# Localized titles per error code. `vi` is the default locale (NFR-11).
TITLES: dict[str, dict[str, str]] = {
    "INTERNAL_ERROR": {"vi": "Lỗi hệ thống", "en": "Internal server error"},
    "BAD_REQUEST": {"vi": "Yêu cầu không hợp lệ", "en": "Bad request"},
    "VALIDATION_ERROR": {"vi": "Dữ liệu không hợp lệ", "en": "Validation error"},
    "NOT_FOUND": {"vi": "Không tìm thấy", "en": "Not found"},
    "METHOD_NOT_ALLOWED": {"vi": "Phương thức không được hỗ trợ", "en": "Method not allowed"},
    "CONFLICT": {"vi": "Xung đột dữ liệu", "en": "Conflict"},
    "IDEMPOTENCY_KEY_REQUIRED": {
        "vi": "Thiếu Idempotency-Key",
        "en": "Idempotency-Key header is required",
    },
    "IDEMPOTENCY_KEY_INVALID": {
        "vi": "Idempotency-Key không hợp lệ",
        "en": "Idempotency-Key header is invalid",
    },
    "IDEMPOTENCY_KEY_REUSED": {
        "vi": "Idempotency-Key đã được dùng cho yêu cầu khác",
        "en": "Idempotency-Key was already used with a different request",
    },
    "IDEMPOTENCY_IN_PROGRESS": {
        "vi": "Yêu cầu với Idempotency-Key này đang được xử lý",
        "en": "A request with this Idempotency-Key is still being processed",
    },
    "SERVICE_UNAVAILABLE": {"vi": "Dịch vụ tạm thời không khả dụng", "en": "Service unavailable"},
}


def negotiate_locale(accept_language: str | None) -> str:
    """Return ``en`` when the client prefers English, otherwise the default ``vi``."""
    if not accept_language:
        return "vi"
    first = accept_language.split(",", 1)[0].strip().lower()
    return "en" if first.startswith("en") else "vi"


def title_for(code: str, locale: str) -> str:
    titles = TITLES.get(code) or TITLES["INTERNAL_ERROR"]
    return titles.get(locale) or titles["vi"]


@dataclass(frozen=True, slots=True)
class FieldError:
    field: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": self.message}


class AppError(Exception):
    """Base class for expected errors that map to a Problem Details response."""

    code: ClassVar[str] = "INTERNAL_ERROR"
    status: ClassVar[int] = 500

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: Sequence[FieldError] = (),
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail or self.code)
        self.detail = detail
        self.errors = tuple(errors)
        self.headers = dict(headers or {})


class BadRequestError(AppError):
    code = "BAD_REQUEST"
    status = 400


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status = 404


class ConflictError(AppError):
    code = "CONFLICT"
    status = 409


class ValidationFailedError(AppError):
    code = "VALIDATION_ERROR"
    status = 422


class ServiceUnavailableError(AppError):
    code = "SERVICE_UNAVAILABLE"
    status = 503


class IdempotencyKeyRequiredError(BadRequestError):
    code = "IDEMPOTENCY_KEY_REQUIRED"


class IdempotencyKeyInvalidError(BadRequestError):
    code = "IDEMPOTENCY_KEY_INVALID"


class IdempotencyKeyReusedError(AppError):
    code = "IDEMPOTENCY_KEY_REUSED"
    status = 422


class IdempotencyInProgressError(ConflictError):
    code = "IDEMPOTENCY_IN_PROGRESS"


def problem_response(
    *,
    status: int,
    code: str,
    locale: str,
    detail: str | None = None,
    errors: Sequence[FieldError] = (),
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": ERROR_TYPE_BASE + code.lower().replace("_", "-"),
        "title": title_for(code, locale),
        "status": status,
        "code": code,
        "correlation_id": get_correlation_id(),
    }
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = [e.to_dict() for e in errors]
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


_HTTP_STATUS_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    503: "SERVICE_UNAVAILABLE",
}


def _locale(request: Request) -> str:
    return negotiate_locale(request.headers.get("accept-language"))


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 - registered only for AppError
    if exc.status >= 500:  # noqa: PLR2004
        logger.error("app_error", code=exc.code, detail=exc.detail)
    return problem_response(
        status=exc.status,
        code=exc.code,
        locale=_locale(request),
        detail=exc.detail,
        errors=exc.errors,
        headers=exc.headers,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    errors = [
        FieldError(
            field=".".join(str(p) for p in err.get("loc", ()) if p not in {"body", "query"}),
            code=str(err.get("type", "invalid")).upper(),
            message=str(err.get("msg", "")),
        )
        for err in exc.errors()
    ]
    return problem_response(
        status=422, code="VALIDATION_ERROR", locale=_locale(request), errors=errors
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code = _HTTP_STATUS_CODES.get(exc.status_code, "INTERNAL_ERROR")
    return problem_response(
        status=exc.status_code,
        code=code,
        locale=_locale(request),
        headers=dict(exc.headers or {}),
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", exc_type=type(exc).__name__)
    return problem_response(status=500, code="INTERNAL_ERROR", locale=_locale(request))


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected)
