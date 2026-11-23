"""Provider port (payment-provider-simulator.md §2). Adapters implement ``PaymentProvider``.

Results are normalized to ``PENDING | SUCCEEDED | FAILED | EXPIRED | RETURNED``. Transport
problems raise ``ProviderTimeout`` (outcome unknown) or ``ProviderUnavailable`` (certainly
not accepted); callers never map a timeout to FAILED.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Outcome(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    RETURNED = "RETURNED"


class ProviderError(Exception):
    pass


class ProviderTimeout(ProviderError):
    """No usable answer: the operation may or may not exist at the provider."""


class ProviderUnavailable(ProviderError):
    """The provider refused before accepting anything (safe to retry with the same key)."""


class ProviderRejected(ProviderError):
    """The provider rejected the request itself (bad data, key reused with another body)."""


class OperationNotFound(ProviderError):
    """Status inquiry: the provider has no operation with this key."""


class InvalidCallback(ProviderError):
    """Signature missing/wrong or timestamp outside the allowed window."""


@dataclass(frozen=True, slots=True)
class ChargeRequest:
    operation_key: str
    amount_minor: int
    currency: str
    reference: str
    method: str
    callback_url: str
    return_url: str


@dataclass(frozen=True, slots=True)
class PayoutRequest:
    operation_key: str
    amount_minor: int
    currency: str
    reference: str
    callback_url: str
    bank_code: str
    account_number: str
    account_name: str


@dataclass(frozen=True, slots=True)
class OperationStatus:
    operation_key: str
    provider_reference: str | None
    outcome: Outcome
    amount_minor: int | None = None
    currency: str | None = None
    failure_code: str | None = None
    checkout_url: str | None = None


_EVENT_OUTCOMES = {
    "succeeded": Outcome.SUCCEEDED,
    "failed": Outcome.FAILED,
    "expired": Outcome.EXPIRED,
    "returned": Outcome.RETURNED,
}


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    event_id: str
    type: str
    operation_key: str
    provider_reference: str | None
    amount_minor: int
    currency: str
    failure_code: str | None
    body: Mapping[str, object]

    @property
    def outcome(self) -> Outcome:
        return _EVENT_OUTCOMES.get(self.type.rsplit(".", 1)[-1], Outcome.PENDING)


class PaymentProvider(Protocol):
    code: str

    async def create_charge(self, request: ChargeRequest) -> OperationStatus: ...

    async def create_payout(self, request: PayoutRequest) -> OperationStatus: ...

    async def get_operation(self, operation_key: str) -> OperationStatus: ...

    def verify_callback(self, headers: Mapping[str, str], raw_body: bytes) -> ProviderEvent: ...
