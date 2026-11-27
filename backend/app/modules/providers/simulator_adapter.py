"""``PaymentProvider`` adapter for the Payment Provider Simulator over HTTP."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

import httpx

from app.modules.providers.port import (
    ChargeRequest,
    InvalidCallback,
    OperationNotFound,
    OperationStatus,
    Outcome,
    PayoutRequest,
    ProviderEvent,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
    StatementLine,
)

SIMULATOR = "SIMULATOR"


def expected_signature(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "v1=" + mac.hexdigest()


def _status(data: Mapping[str, Any]) -> OperationStatus:
    amount = data.get("amount")
    return OperationStatus(
        operation_key=str(data["operation_key"]),
        provider_reference=data.get("provider_reference"),
        outcome=Outcome(str(data["status"])),
        amount_minor=int(str(amount)) if amount is not None else None,
        currency=data.get("currency"),
        failure_code=data.get("failure_code"),
        checkout_url=data.get("checkout_url"),
    )


class SimulatorProvider:
    code = SIMULATOR

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str,
        webhook_secret: str,
        max_age_seconds: int = 300,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._secret = webhook_secret
        self._max_age = max_age_seconds
        self._now = now

    @classmethod
    def build(
        cls,
        base_url: str,
        *,
        api_key: str,
        webhook_secret: str,
        connect_timeout: float = 3,
        read_timeout: float = 10,
        max_age_seconds: int = 300,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], float] = time.time,
    ) -> SimulatorProvider:
        # No automatic retries: retrying is a business decision (financial-processing §5.3).
        client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport,
        )
        return cls(
            client,
            api_key=api_key,
            webhook_secret=webhook_secret,
            max_age_seconds=max_age_seconds,
            now=now,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method, path, json=body, headers={"Authorization": f"Bearer {self._api_key}"}
            )
            # Read the whole body here: a truncated body is a transport failure too.
            await response.aread()
            if response.status_code < 400:  # noqa: PLR2004
                response.json()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # No connection was ever made, so the provider cannot have accepted anything.
            raise ProviderUnavailable(type(exc).__name__) from exc
        except (httpx.TransportError, ValueError) as exc:  # timeouts, resets, garbage
            raise ProviderTimeout(type(exc).__name__) from exc
        if response.status_code == 503:  # noqa: PLR2004
            raise ProviderUnavailable("503")
        if response.status_code >= 500:  # noqa: PLR2004
            raise ProviderTimeout(f"HTTP {response.status_code}")
        return response

    @staticmethod
    def _parse(response: httpx.Response) -> OperationStatus:
        if response.status_code >= 400:  # noqa: PLR2004
            raise ProviderRejected(f"HTTP {response.status_code}: {response.text[:200]}")
        return _status(response.json())

    async def create_charge(self, request: ChargeRequest) -> OperationStatus:
        response = await self._call(
            "POST",
            "/v1/charges",
            {
                "operation_key": request.operation_key,
                "amount": str(request.amount_minor),
                "currency": request.currency,
                "reference": request.reference,
                "method": request.method,
                "callback_url": request.callback_url,
                "return_url": request.return_url,
            },
        )
        return self._parse(response)

    async def create_payout(self, request: PayoutRequest) -> OperationStatus:
        response = await self._call(
            "POST",
            "/v1/payouts",
            {
                "operation_key": request.operation_key,
                "amount": str(request.amount_minor),
                "currency": request.currency,
                "reference": request.reference,
                "callback_url": request.callback_url,
                "destination": {
                    "bank_code": request.bank_code,
                    "account_number": request.account_number,
                    "account_name": request.account_name,
                },
            },
        )
        return self._parse(response)

    async def get_operation(self, operation_key: str) -> OperationStatus:
        response = await self._call("GET", f"/v1/operations/{operation_key}")
        if response.status_code == 404:  # noqa: PLR2004
            raise OperationNotFound(operation_key)
        return self._parse(response)

    async def get_statement(self, business_date: date) -> list[StatementLine]:
        response = await self._call("GET", f"/v1/statements/{business_date.isoformat()}")
        if response.status_code >= 400:  # noqa: PLR2004
            raise ProviderRejected(f"HTTP {response.status_code}: {response.text[:200]}")
        return [
            StatementLine(
                operation_key=str(line["operation_key"]),
                provider_reference=line.get("provider_reference"),
                kind=str(line["kind"]),
                status=str(line["status"]),
                amount_minor=int(line["amount"]),
                currency=str(line["currency"]),
            )
            for line in response.json()["lines"]
        ]

    def verify_callback(self, headers: Mapping[str, str], raw_body: bytes) -> ProviderEvent:
        lowered = {k.lower(): v for k, v in headers.items()}
        timestamp = lowered.get("x-sim-timestamp", "")
        given = lowered.get("x-sim-signature", "")
        if not timestamp.isdigit() or abs(self._now() - int(timestamp)) > self._max_age:
            raise InvalidCallback("timestamp outside the allowed window")
        if not hmac.compare_digest(given, expected_signature(self._secret, timestamp, raw_body)):
            raise InvalidCallback("bad signature")
        try:
            body = json.loads(raw_body)
            return ProviderEvent(
                event_id=str(body["event_id"]),
                type=str(body["type"]),
                operation_key=str(body["operation_key"]),
                provider_reference=body.get("provider_reference"),
                amount_minor=int(str(body["amount"])),
                currency=str(body["currency"]),
                failure_code=body.get("failure_code"),
                body=body,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidCallback("malformed body") from exc
