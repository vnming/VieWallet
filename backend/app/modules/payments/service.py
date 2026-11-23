"""Merchants and payment requests (financial-processing.md §5.5, user-flows.md §4).

A payment request is what a QR code points to: ``{web}/pay/{code}``. The code is 16 random
Crockford base32 characters (80 bits, not guessable). Every row carries an HMAC over the fields
that decide where money goes; reads and payments verify it, so a row edited outside the
application (or a bug) cannot redirect a payment silently.

Nothing here moves money. The transactions module locks a request with ``lock_for_payment``
and marks it paid in the same DB transaction as the PAYMENT journal.
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import ColumnElement, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.crypto import hmac_sha256_hex
from app.core.errors import AppError, NotFoundError, register_titles
from app.core.ids import new_id
from app.core.money import Money
from app.modules.access.public import assign_role
from app.modules.audit.public import AuditEntry, record
from app.modules.identity.public import User
from app.modules.payments.models import (
    IssuerType,
    Merchant,
    MerchantStatus,
    PaymentRequest,
    RequestStatus,
)
from app.modules.wallet.public import Wallet, WalletType, ensure_wallet

logger = structlog.get_logger(__name__)

register_titles(
    {
        "PAYMENT_REQUEST_NOT_FOUND": {
            "vi": "Mã thanh toán không hợp lệ",
            "en": "Payment code not valid",
        },
        "PAYMENT_REQUEST_CLOSED": {
            "vi": "Yêu cầu thanh toán đã đóng (đã trả, hết hạn hoặc bị hủy)",
            "en": "The payment request is closed (paid, expired or cancelled)",
        },
        "PAYMENT_REQUEST_AMOUNT_MISMATCH": {
            "vi": "Số tiền không khớp với yêu cầu thanh toán",
            "en": "The amount does not match the payment request",
        },
        "PAYMENT_REQUEST_NOT_FOR_YOU": {
            "vi": "Yêu cầu này dành cho người khác",
            "en": "This request is addressed to someone else",
        },
        "MERCHANT_NOT_FOUND": {"vi": "Bạn chưa có cửa hàng", "en": "No merchant profile"},
        "MERCHANT_NOT_ACTIVE": {
            "vi": "Cửa hàng đang tạm ngừng",
            "en": "The merchant is not active",
        },
    }
)

CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32
CODE_LENGTH = 16
MERCHANT_SINGLE_USE_TTL = timedelta(minutes=15)
MERCHANT_STATIC_TTL = timedelta(days=30)
USER_REQUEST_TTL = timedelta(days=7)
MAX_TTL = timedelta(days=30)


class PaymentRequestNotFoundError(NotFoundError):
    code = "PAYMENT_REQUEST_NOT_FOUND"


class PaymentRequestClosedError(AppError):
    code = "PAYMENT_REQUEST_CLOSED"
    status = 422


class PaymentRequestAmountError(AppError):
    code = "PAYMENT_REQUEST_AMOUNT_MISMATCH"
    status = 422


class PaymentRequestNotForYouError(AppError):
    code = "PAYMENT_REQUEST_NOT_FOR_YOU"
    status = 403


class MerchantNotFoundError(NotFoundError):
    code = "MERCHANT_NOT_FOUND"


class MerchantNotActiveError(AppError):
    code = "MERCHANT_NOT_ACTIVE"
    status = 422


# ---------------------------------------------------------------- merchants


async def merchant_of(session: AsyncSession, user_id: uuid.UUID) -> Merchant:
    merchant: Merchant | None = await session.scalar(
        select(Merchant).where(Merchant.user_id == user_id)
    )
    if merchant is None:
        raise MerchantNotFoundError()
    return merchant


async def find_merchant(session: AsyncSession, user_id: uuid.UUID) -> Merchant | None:
    merchant: Merchant | None = await session.scalar(
        select(Merchant).where(Merchant.user_id == user_id)
    )
    return merchant


async def merchant_by_wallet(session: AsyncSession, wallet_id: uuid.UUID) -> Merchant | None:
    merchant: Merchant | None = await session.scalar(
        select(Merchant).where(Merchant.wallet_id == wallet_id)
    )
    return merchant


async def create_merchant(
    session: AsyncSession,
    clock: Clock,
    *,
    email: str,
    display_name: str,
    legal_name: str,
    mcc: str,
    reason: str,
) -> Merchant:
    """Turn a customer into a (simulated) merchant with its own MERCHANT wallet. Idempotent."""
    user = await session.scalar(
        select(User).where(User.email == email, User.user_type == "CUSTOMER").with_for_update()
    )
    if user is None:
        raise NotFoundError()
    existing = await find_merchant(session, user.id)
    if existing is not None:
        return existing
    wallet = await ensure_wallet(session, clock, user.id, "VND", WalletType.MERCHANT)
    now = clock.now()
    merchant = Merchant(
        id=new_id(),
        user_id=user.id,
        wallet_id=wallet.id,
        legal_name=legal_name.strip()[:120],
        display_name=display_name.strip()[:60],
        mcc=mcc,
        status=MerchantStatus.ACTIVE.value,
        created_at=now,
        updated_at=now,
    )
    session.add(merchant)
    await session.flush()
    await assign_role(session, clock, user_id=user.id, role_code="MERCHANT")
    await record(
        session,
        clock,
        AuditEntry(
            action="payments.merchant_created",
            actor_type="SYSTEM",
            target_type="merchant",
            target_id=str(merchant.id),
            reason=reason,
            details={"user_id": str(user.id), "display_name": merchant.display_name, "mcc": mcc},
        ),
    )
    return merchant


# ---------------------------------------------------------------- signing


def _canonical(request: PaymentRequest) -> str:
    return "|".join(
        [
            "v1",
            request.code,
            request.issuer_type,
            str(request.issuer_user_id),
            str(request.destination_wallet_id),
            "" if request.amount_minor is None else str(request.amount_minor),
            request.currency,
            "1" if request.single_use else "0",
            "" if request.payer_user_id is None else str(request.payer_user_id),
            str(int(request.expires_at.timestamp())),
        ]
    )


def sign(key: bytes, request: PaymentRequest) -> str:
    return hmac_sha256_hex(key, _canonical(request))


def verify(key: bytes, request: PaymentRequest) -> None:
    """A row whose tag does not match is a security incident: logged loudly, and the payer
    gets "not valid" (404) so nothing is paid to an address nobody signed."""
    if not hmac.compare_digest(request.signature, sign(key, request)):
        logger.error("payment_request_signature_mismatch", payment_request_id=str(request.id))
        raise PaymentRequestNotFoundError()


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(code: str) -> str | None:
    """Accept lower case and the usual Crockford look-alikes; reject anything else."""
    cleaned = code.strip().upper().replace("O", "0").replace("I", "1").replace("L", "1")
    if len(cleaned) != CODE_LENGTH or any(c not in CODE_ALPHABET for c in cleaned):
        return None
    return cleaned


# ---------------------------------------------------------------- requests


@dataclass(frozen=True, slots=True)
class NewRequest:
    issuer_type: IssuerType
    issuer_user_id: uuid.UUID
    destination_wallet: Wallet
    amount: Money | None
    description: str
    single_use: bool
    ttl: timedelta
    merchant_id: uuid.UUID | None = None
    payer_user_id: uuid.UUID | None = None


async def create_request(
    session: AsyncSession, clock: Clock, key: bytes, spec: NewRequest
) -> PaymentRequest:
    if spec.ttl <= timedelta(0) or spec.ttl > MAX_TTL:
        raise ValueError("ttl out of range")
    now = clock.now()
    request = PaymentRequest(
        id=new_id(),
        code=new_code(),
        issuer_type=spec.issuer_type.value,
        issuer_user_id=spec.issuer_user_id,
        merchant_id=spec.merchant_id,
        destination_wallet_id=spec.destination_wallet.id,
        amount_minor=spec.amount.amount_minor if spec.amount else None,
        currency=spec.destination_wallet.currency,
        description=spec.description.strip()[:140],
        single_use=spec.single_use,
        status=RequestStatus.OPEN.value,
        payer_user_id=spec.payer_user_id,
        paid_count=0,
        paid_transaction_id=None,
        signature="",
        # Whole seconds: the signed epoch value survives a DB round trip exactly.
        expires_at=(now + spec.ttl).replace(microsecond=0),
        created_at=now,
        updated_at=now,
    )
    request.signature = sign(key, request)
    session.add(request)
    await session.flush()
    return request


def effective_status(request: PaymentRequest, now: datetime) -> RequestStatus:
    status = RequestStatus(request.status)
    if status is RequestStatus.OPEN and request.expires_at <= now:
        return RequestStatus.EXPIRED
    return status


async def get_by_code(session: AsyncSession, key: bytes, code: str) -> PaymentRequest:
    normalized = normalize_code(code)
    if normalized is None:
        raise PaymentRequestNotFoundError()
    request: PaymentRequest | None = await session.scalar(
        select(PaymentRequest).where(PaymentRequest.code == normalized)
    )
    if request is None:
        raise PaymentRequestNotFoundError()
    verify(key, request)
    return request


async def lock_request(session: AsyncSession, key: bytes, request_id: uuid.UUID) -> PaymentRequest:
    request: PaymentRequest | None = await session.scalar(
        select(PaymentRequest)
        .where(PaymentRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if request is None:
        raise PaymentRequestNotFoundError()
    verify(key, request)
    return request


def ensure_payable(
    request: PaymentRequest, *, payer_id: uuid.UUID, amount: Money | None, now: datetime
) -> Money:
    """The amount to pay, or an error explaining why this payer cannot pay this request."""
    if effective_status(request, now) is not RequestStatus.OPEN:
        raise PaymentRequestClosedError()
    if request.issuer_user_id == payer_id:
        raise PaymentRequestNotForYouError("you issued this request")
    if request.payer_user_id is not None and request.payer_user_id != payer_id:
        raise PaymentRequestNotForYouError()
    if request.amount_minor is not None:
        if amount is not None and amount.amount_minor != request.amount_minor:
            raise PaymentRequestAmountError()
        return Money(request.amount_minor, request.currency)
    if amount is None or not amount.is_positive:
        raise PaymentRequestAmountError("amount required")
    return amount


async def mark_paid(
    session: AsyncSession, clock: Clock, request: PaymentRequest, transaction_id: uuid.UUID
) -> None:
    """Called with the request row locked, in the transaction that posts the payment."""
    request.paid_count += 1
    if request.single_use:
        request.status = RequestStatus.PAID.value
        request.paid_transaction_id = transaction_id
    request.updated_at = clock.now()
    await session.flush()


async def release_reservation(
    session: AsyncSession, clock: Clock, request: PaymentRequest, transaction_id: uuid.UUID
) -> None:
    """A payment held for risk review was rejected: the request becomes payable again."""
    if request.paid_count > 0:
        request.paid_count -= 1
    if request.single_use and request.paid_transaction_id == transaction_id:
        request.paid_transaction_id = None
        request.status = (
            RequestStatus.OPEN if request.expires_at > clock.now() else RequestStatus.EXPIRED
        ).value
    request.updated_at = clock.now()
    await session.flush()


async def close_request(
    session: AsyncSession,
    clock: Clock,
    key: bytes,
    code: str,
    *,
    user_id: uuid.UUID,
    as_issuer: bool,
) -> PaymentRequest:
    """Issuer cancels, or the addressed payer declines, an open request."""
    found = await get_by_code(session, key, code)
    request = await lock_request(session, key, found.id)
    if as_issuer:
        if request.issuer_user_id != user_id:
            raise PaymentRequestNotFoundError()
        target = RequestStatus.CANCELLED
    else:
        if request.payer_user_id != user_id:
            raise PaymentRequestNotFoundError()
        target = RequestStatus.DECLINED
    if effective_status(request, clock.now()) is not RequestStatus.OPEN:
        raise PaymentRequestClosedError()
    request.status = target.value
    request.updated_at = clock.now()
    await session.flush()
    return request


async def list_requests(
    session: AsyncSession,
    *,
    issuer_user_id: uuid.UUID | None = None,
    payer_user_id: uuid.UUID | None = None,
    open_only: bool = False,
    now: datetime,
    limit: int = 50,
) -> list[PaymentRequest]:
    conditions: list[ColumnElement[bool]] = []
    if issuer_user_id is not None:
        conditions.append(PaymentRequest.issuer_user_id == issuer_user_id)
    if payer_user_id is not None:
        conditions.append(PaymentRequest.payer_user_id == payer_user_id)
    if open_only:
        conditions += [
            PaymentRequest.status == RequestStatus.OPEN.value,
            PaymentRequest.expires_at > now,
        ]
    rows = await session.scalars(
        select(PaymentRequest)
        .where(*conditions)
        .order_by(PaymentRequest.created_at.desc())
        .limit(limit)
    )
    return list(rows.all())


async def expire_due(session: AsyncSession, clock: Clock, *, batch: int = 200) -> int:
    """Worker job: open requests past their deadline become EXPIRED (reads already treat them
    as expired; this keeps the table honest for lists and reports)."""
    result = await session.execute(
        update(PaymentRequest)
        .where(
            PaymentRequest.id.in_(
                select(PaymentRequest.id)
                .where(
                    PaymentRequest.status == RequestStatus.OPEN.value,
                    PaymentRequest.expires_at <= clock.now(),
                )
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        )
        .values(status=RequestStatus.EXPIRED.value, updated_at=clock.now())
    )
    return int(result.rowcount)  # type: ignore[attr-defined]
