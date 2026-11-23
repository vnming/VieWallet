"""TRANSFER and PAYMENT quotes, confirmation and cancellation (§5.4, §5.5, user-flows §3-§4).

Each function takes the session of the caller's idempotent unit of work (the HTTP layer wraps
them in ``IdempotencyStore.execute``), so a replayed request returns the stored response.

* ``quote_transfer``: to a wallet number, an e-mail or a saved recipient.
* ``quote_request``: from a payment request (QR). A merchant's request becomes a PAYMENT; a
  person's "request money" becomes a TRANSFER linked to the request (owner decision, Phase 5).
* ``confirm``: PIN, then the shared pipeline in ``spend.confirm_locked``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, IdempotencyKeyReusedError, register_titles
from app.core.money import Money
from app.modules.identity.public import RequestMeta, User, kyc_tier_of, verify_pin
from app.modules.payments.public import (
    IssuerType,
    MerchantNotActiveError,
    MerchantStatus,
    PaymentRequest,
    ensure_payable,
    get_by_code,
    merchant_by_wallet,
)
from app.modules.pricing.public import LimitDirection, Subject, fee_for
from app.modules.transactions import service
from app.modules.transactions.domain.state_machine import TxnStatus, TxnType
from app.modules.transactions.models import Transaction
from app.modules.transactions.service import Actor, TxnInvalidStateError
from app.modules.transactions.use_cases.common import (
    COUNTED_STATUSES,
    FinanceContext,
    LimitExceededError,
    check_limits,
)
from app.modules.transactions.use_cases.spend import SPEND_TYPES, confirm_locked
from app.modules.wallet.public import (
    RecipientView,
    Wallet,
    WalletNotActiveError,
    WalletStatus,
    describe_wallet,
    ensure_wallet,
    find_recipient,
    get_saved,
    get_wallet,
    mask_name,
    mask_wallet_number,
)

register_titles(
    {
        "WALLET_FROZEN": {"vi": "Ví của bạn đang bị khóa", "en": "Your wallet is frozen"},
        "RECIPIENT_UNAVAILABLE": {
            "vi": "Người nhận hiện không nhận được tiền",
            "en": "The recipient cannot receive money right now",
        },
    }
)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class WalletFrozenError(AppError):
    code = "WALLET_FROZEN"
    status = 422


class RecipientUnavailableError(AppError):
    code = "RECIPIENT_UNAVAILABLE"
    status = 422


def clean_note(note: str) -> str:
    return _CONTROL.sub(" ", note).strip()[:140]


@dataclass(frozen=True, slots=True)
class RecipientTarget:
    wallet_number: str | None = None
    email: str | None = None
    recipient_id: uuid.UUID | None = None


def _party(view: RecipientView) -> dict[str, str]:
    return {
        "kind": view.kind,
        "name": view.display_name,
        "wallet_number_masked": view.wallet_number_masked,
    }


async def _payer_party(session: AsyncSession, wallet: Wallet) -> dict[str, str]:
    owner = await session.get(User, wallet.owner_user_id)
    assert owner is not None  # noqa: S101 - FK
    return {
        "kind": "USER",
        "name": mask_name(owner.full_name),
        "wallet_number_masked": mask_wallet_number(wallet.wallet_number),
    }


async def _sender_wallet(session: AsyncSession, ctx: FinanceContext, user_id: uuid.UUID) -> Wallet:
    wallet = await ensure_wallet(session, ctx.clock, user_id, "VND")
    if wallet.status == WalletStatus.FROZEN.value:
        raise WalletFrozenError()
    if wallet.status != WalletStatus.ACTIVE.value:
        raise WalletNotActiveError()
    return wallet


async def _by_key(session: AsyncSession, user_id: uuid.UUID, key: str) -> Transaction | None:
    txn: Transaction | None = await session.scalar(
        select(Transaction).where(
            Transaction.initiator_user_id == user_id, Transaction.idempotency_key == key
        )
    )
    return txn


async def _warnings(
    session: AsyncSession, ctx: FinanceContext, sender: Wallet, receiver: Wallet, amount: int
) -> list[str]:
    """Hints for the review screen; they never block (risk rules decide at confirm)."""
    to_receiver = (
        Transaction.source_wallet_id == sender.id,
        Transaction.destination_wallet_id == receiver.id,
        Transaction.status.in_(COUNTED_STATUSES),
    )
    warnings = []
    if not await session.scalar(select(exists().where(*to_receiver))):
        warnings.append("NEW_RECIPIENT")
    since = ctx.clock.now() - timedelta(minutes=10)
    duplicate = exists().where(
        *to_receiver, Transaction.amount_minor == amount, Transaction.created_at >= since
    )
    if await session.scalar(select(duplicate)):
        warnings.append("POSSIBLE_DUPLICATE")
    return warnings


async def _create_quote(
    session: AsyncSession,
    ctx: FinanceContext,
    *,
    user_id: uuid.UUID,
    txn_type: TxnType,
    sender: Wallet,
    receiver: Wallet,
    receiver_party: dict[str, str],
    amount: Money,
    note: str,
    key: str,
    payment_request: PaymentRequest | None = None,
    extra: dict[str, Any] | None = None,
) -> Transaction:
    now = ctx.clock.now()
    tier = await kyc_tier_of(session, user_id)
    breach = await check_limits(
        session,
        wallet=sender,
        txn_type=txn_type,
        direction=LimitDirection.OUT,
        amount=amount,
        user_id=user_id,
        kyc_tier=tier,
        now=now,
        timezone=ctx.settings.business_timezone,
    )
    if breach is not None:
        raise LimitExceededError(breach)
    fee = await fee_for(
        session, ctx.clock, txn_type=txn_type.value, amount=amount, subject=Subject(user_id, tier)
    )
    warnings = await _warnings(session, ctx, sender, receiver, amount.amount_minor)
    txn = await service.create(
        session,
        ctx.clock,
        txn_type=txn_type,
        amount=amount,
        fee=fee.fee,
        initiator_user_id=user_id,
        actor=Actor("USER", user_id),
        source_wallet_id=sender.id,
        destination_wallet_id=receiver.id,
        description=note,
        idempotency_key=key,
        counterparty_snapshot=receiver_party,
        extra={
            "source_party": await _payer_party(session, sender),
            "warnings": warnings,
            "fee_breakdown": fee.breakdown,
            **(extra or {}),
        },
    )
    expires = now + timedelta(minutes=ctx.settings.spend_quote_minutes)
    if payment_request is not None:
        txn.payment_request_id = payment_request.id
        expires = min(expires, payment_request.expires_at)
    txn.fee_rule_id = fee.rule_id
    txn.fee_rule_version = fee.rule_version
    txn.quote_expires_at = expires
    await session.flush()
    return txn


async def quote_transfer(
    session: AsyncSession,
    ctx: FinanceContext,
    *,
    user_id: uuid.UUID,
    target: RecipientTarget,
    amount: Money,
    note: str,
    key: str,
) -> Transaction:
    existing = await _by_key(session, user_id, key)
    if existing is not None:  # second layer behind the idempotency store
        raise IdempotencyKeyReusedError()
    sender = await _sender_wallet(session, ctx, user_id)
    if target.recipient_id is not None:
        saved = await get_saved(session, user_id, target.recipient_id)
        receiver = await get_wallet(session, saved.wallet_id)
    else:
        receiver = await find_recipient(
            session, requester_id=user_id, wallet_number=target.wallet_number, email=target.email
        )
    view = await describe_wallet(session, receiver)
    if not view.active:
        raise RecipientUnavailableError()
    return await _create_quote(
        session,
        ctx,
        user_id=user_id,
        txn_type=TxnType.TRANSFER,
        sender=sender,
        receiver=receiver,
        receiver_party=_party(view),
        amount=amount,
        note=clean_note(note),
        key=key,
    )


async def quote_request(
    session: AsyncSession,
    ctx: FinanceContext,
    *,
    user_id: uuid.UUID,
    code: str,
    amount: Money | None,
    key: str,
) -> Transaction:
    if await _by_key(session, user_id, key) is not None:
        raise IdempotencyKeyReusedError()
    request = await get_by_code(session, ctx.identity.signing_key, code)
    to_pay = ensure_payable(request, payer_id=user_id, amount=amount, now=ctx.clock.now())
    sender = await _sender_wallet(session, ctx, user_id)
    receiver = await get_wallet(session, request.destination_wallet_id)
    if request.issuer_type == IssuerType.MERCHANT.value:
        merchant = await merchant_by_wallet(session, receiver.id)
        if merchant is None or merchant.status != MerchantStatus.ACTIVE.value:
            raise MerchantNotActiveError()
        view = await describe_wallet(session, receiver, merchant.display_name)
        txn_type = TxnType.PAYMENT
        extra = {"merchant_id": str(merchant.id), "mcc": merchant.mcc}
    else:
        view = await describe_wallet(session, receiver)
        txn_type = TxnType.TRANSFER
        extra = {}
    if not view.active:
        raise RecipientUnavailableError()
    return await _create_quote(
        session,
        ctx,
        user_id=user_id,
        txn_type=txn_type,
        sender=sender,
        receiver=receiver,
        receiver_party=_party(view),
        amount=to_pay,
        note=request.description,
        key=key,
        payment_request=request,
        extra={**extra, "payment_request_code": request.code},
    )


async def owned_spend(session: AsyncSession, user_id: uuid.UUID, reference: str) -> Transaction:
    txn = await service.get_by_reference(session, reference)
    if txn.initiator_user_id != user_id or txn.type not in SPEND_TYPES:
        raise service.TransactionNotFoundError()
    return txn


async def check_pin(
    ctx: FinanceContext,
    *,
    user_id: uuid.UUID,
    reference: str,
    pin: str,
    meta: RequestMeta,
) -> None:
    """PIN gate of a confirm (own short transactions; no money lock is held meanwhile).

    A quote that is no longer CREATED skips the check: ``confirm_locked`` then answers with
    the stored state (same key) or 409 (another key). A wrong PIN keeps the quote in CREATED
    and the failure counter commits on its own (§5.4).
    """
    async with ctx.db.transaction() as session:
        txn = await owned_spend(session, user_id, reference)
    if txn.status == TxnStatus.CREATED.value:
        await verify_pin(ctx.identity, user_id=user_id, pin=pin, meta=meta)


async def confirm(
    session: AsyncSession,
    ctx: FinanceContext,
    *,
    user_id: uuid.UUID,
    reference: str,
    key: str,
) -> Transaction:
    """Run after ``check_pin``, inside the idempotent money transaction."""
    txn = await owned_spend(session, user_id, reference)
    return await confirm_locked(session, ctx, user_id=user_id, transaction_id=txn.id, key=key)


async def cancel(
    session: AsyncSession, ctx: FinanceContext, *, user_id: uuid.UUID, reference: str
) -> Transaction:
    txn = await owned_spend(session, user_id, reference)
    txn = await service.lock(session, txn.id)
    if txn.status != TxnStatus.CREATED.value:
        raise TxnInvalidStateError()
    return await service.transition(
        session,
        ctx.clock,
        txn,
        TxnStatus.CANCELLED,
        actor=Actor("USER", user_id),
        reason_code="USER_CANCELLED",
    )


async def expire_quotes(ctx: FinanceContext, *, batch: int = 200) -> int:
    """Worker job: CREATED transfers/payments whose quote ran out become EXPIRED."""
    async with ctx.db.transaction() as session:
        rows = (
            await session.scalars(
                select(Transaction)
                .where(
                    Transaction.type.in_(SPEND_TYPES),
                    Transaction.status == TxnStatus.CREATED.value,
                    Transaction.quote_expires_at <= ctx.clock.now(),
                )
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for txn in rows:
            await service.transition(
                session,
                ctx.clock,
                txn,
                TxnStatus.EXPIRED,
                actor=service.SYSTEM,
                reason_code="QUOTE_EXPIRED",
                changes={"failure_code": "QUOTE_EXPIRED"},
            )
        return len(rows)
