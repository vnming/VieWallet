"""Wallet lifecycle: open (with its ledger account), look up, lock, freeze, unfreeze."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import AppError, ConflictError, NotFoundError, register_titles
from app.core.ids import new_id
from app.core.money import Money
from app.modules.audit.public import AuditEntry, record
from app.modules.ledger.public import AccountCode, balance_of, open_account
from app.modules.wallet.domain import (
    FreezeSource,
    WalletStatus,
    WalletType,
    generate_wallet_number,
    is_valid_wallet_number,
)
from app.modules.wallet.models import Wallet

register_titles(
    {
        "WALLET_NOT_FOUND": {"vi": "Không tìm thấy ví", "en": "Wallet not found"},
        "WALLET_NOT_ACTIVE": {"vi": "Ví đang bị khóa", "en": "Wallet is not active"},
        "WALLET_STATE_CONFLICT": {
            "vi": "Trạng thái ví không cho phép thao tác này",
            "en": "The wallet's status does not allow this action",
        },
    }
)

DEFAULT_CURRENCY = "VND"
_NUMBER_ATTEMPTS = 5


class WalletNotFoundError(NotFoundError):
    code = "WALLET_NOT_FOUND"


class WalletNotActiveError(AppError):
    code = "WALLET_NOT_ACTIVE"
    status = 422


class WalletStateConflictError(ConflictError):
    code = "WALLET_STATE_CONFLICT"


@dataclass(frozen=True, slots=True)
class WalletView:
    wallet: Wallet
    balance: Money


async def find_for_owner(
    session: AsyncSession,
    owner_user_id: uuid.UUID,
    currency: str = DEFAULT_CURRENCY,
    wallet_type: WalletType = WalletType.MAIN,
) -> Wallet | None:
    wallet: Wallet | None = await session.scalar(
        select(Wallet).where(
            Wallet.owner_user_id == owner_user_id,
            Wallet.currency == currency,
            Wallet.wallet_type == wallet_type.value,
        )
    )
    return wallet


async def ensure_wallet(
    session: AsyncSession,
    clock: Clock,
    owner_user_id: uuid.UUID,
    currency: str = DEFAULT_CURRENCY,
    wallet_type: WalletType = WalletType.MAIN,
) -> Wallet:
    """Return the user's wallet of that type, opening it (and its ledger account) if needed.

    Runs in a savepoint, so a concurrent opener or a wallet-number collision never leaves an
    orphan ledger account behind.
    """
    existing = await find_for_owner(session, owner_user_id, currency, wallet_type)
    if existing is not None:
        return existing
    for _ in range(_NUMBER_ATTEMPTS):
        try:
            async with session.begin_nested():
                wallet_id = new_id()
                account = await open_account(
                    session,
                    clock,
                    code=AccountCode.CUSTOMER_WALLET,
                    currency=currency,
                    owner_ref=str(wallet_id),
                    name=f"Wallet {wallet_id}",
                )
                now = clock.now()
                wallet = Wallet(
                    id=wallet_id,
                    owner_user_id=owner_user_id,
                    wallet_number=generate_wallet_number(),
                    wallet_type=wallet_type.value,
                    currency=currency,
                    status=WalletStatus.ACTIVE.value,
                    ledger_account_id=account.id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(wallet)
                await session.flush()
        except IntegrityError:
            existing = await find_for_owner(session, owner_user_id, currency, wallet_type)
            if existing is not None:  # a concurrent request opened it first
                return existing
            continue  # wallet number collision: try another number
        await record(
            session,
            clock,
            AuditEntry(
                action="wallet.opened",
                actor_type="SYSTEM",
                target_type="wallet",
                target_id=str(wallet.id),
                details={"currency": currency, "wallet_type": wallet_type.value},
            ),
        )
        return wallet
    raise RuntimeError("could not allocate a unique wallet number")


async def get_wallet(session: AsyncSession, wallet_id: uuid.UUID) -> Wallet:
    wallet = await session.get(Wallet, wallet_id)
    if wallet is None:
        raise WalletNotFoundError()
    return wallet


async def get_by_number(session: AsyncSession, number: str) -> Wallet:
    if not is_valid_wallet_number(number):
        raise WalletNotFoundError()
    wallet: Wallet | None = await session.scalar(
        select(Wallet).where(Wallet.wallet_number == number)
    )
    if wallet is None:
        raise WalletNotFoundError()
    return wallet


async def lock_wallets(
    session: AsyncSession, wallet_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Wallet]:
    """Lock wallet rows in id order (status checks must hold until commit)."""
    rows = (
        await session.scalars(
            select(Wallet)
            .where(Wallet.id.in_(sorted(set(wallet_ids))))
            .order_by(Wallet.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    found = {w.id: w for w in rows}
    if len(found) != len(set(wallet_ids)):
        raise WalletNotFoundError()
    return found


async def view(session: AsyncSession, wallet: Wallet) -> WalletView:
    return WalletView(wallet, await balance_of(session, wallet.ledger_account_id))


def ensure_active(wallet: Wallet) -> None:
    if wallet.status != WalletStatus.ACTIVE.value:
        raise WalletNotActiveError()


async def freeze(
    session: AsyncSession,
    clock: Clock,
    wallet_id: uuid.UUID,
    *,
    source: FreezeSource,
    actor_id: uuid.UUID | None,
    reason: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> Wallet:
    wallet = (await lock_wallets(session, [wallet_id]))[wallet_id]
    before = {"status": wallet.status, "freeze_source": wallet.freeze_source}
    if wallet.status == WalletStatus.FROZEN.value:
        # A staff/system freeze may take over a self-freeze (so the customer cannot lift it).
        if source is FreezeSource.SELF or wallet.freeze_source != FreezeSource.SELF.value:
            raise WalletStateConflictError()
    elif wallet.status != WalletStatus.ACTIVE.value:
        raise WalletStateConflictError()
    now = clock.now()
    wallet.status = WalletStatus.FROZEN.value
    wallet.freeze_source = source.value
    wallet.status_reason = reason[:200]
    wallet.frozen_by = actor_id
    wallet.frozen_at = now
    wallet.updated_at = now
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="wallet.frozen",
            actor_type="USER" if source is FreezeSource.SELF else source.value,
            actor_id=actor_id,
            target_type="wallet",
            target_id=str(wallet.id),
            reason=reason,
            before=before,
            after={"status": wallet.status, "freeze_source": wallet.freeze_source},
            ip=ip,
            user_agent=user_agent,
        ),
    )
    return wallet


async def unfreeze(
    session: AsyncSession,
    clock: Clock,
    wallet_id: uuid.UUID,
    *,
    by_owner: bool,
    actor_id: uuid.UUID | None,
    reason: str,
    approval_request_id: uuid.UUID | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> Wallet:
    """Owners lift only their own freeze; staff unfreezes go through approval (operations)."""
    wallet = (await lock_wallets(session, [wallet_id]))[wallet_id]
    if wallet.status != WalletStatus.FROZEN.value:
        raise WalletStateConflictError()
    if by_owner and wallet.freeze_source != FreezeSource.SELF.value:
        raise WalletStateConflictError("frozen by staff: contact support")
    before = {"status": wallet.status, "freeze_source": wallet.freeze_source}
    wallet.status = WalletStatus.ACTIVE.value
    wallet.freeze_source = None
    wallet.status_reason = None
    wallet.frozen_by = None
    wallet.frozen_at = None
    wallet.updated_at = clock.now()
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="wallet.unfrozen",
            actor_type="USER" if by_owner else "STAFF",
            actor_id=actor_id,
            target_type="wallet",
            target_id=str(wallet.id),
            reason=reason,
            before=before,
            after={"status": wallet.status},
            details={"approval_request_id": str(approval_request_id)}
            if approval_request_id
            else None,
            ip=ip,
            user_agent=user_agent,
        ),
    )
    return wallet
