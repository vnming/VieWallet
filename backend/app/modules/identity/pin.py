"""Transaction PIN (FR-PIN): Argon2id over HMAC(pepper, PIN), lockout after 5 wrong tries.

``verify_pin`` is the entry point later phases use at the confirm step of money-out
transactions. It runs in its own transaction so failed attempts are committed even when the
caller aborts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.identity.context import EMAIL_CODE_TTL, IdentityServices, RequestMeta
from app.modules.identity.domain import pin_policy
from app.modules.identity.errors import (
    PasswordIncorrectError,
    PinAlreadySetError,
    PinInvalidError,
    PinLockedError,
    PinNotSetError,
    PinWeakError,
    VerificationCodeInvalidError,
)
from app.modules.identity.models import Credential, TransactionPin, User
from app.modules.identity.tokens import consume_code, issue_code
from app.modules.notifications.public import request_email


@dataclass(frozen=True, slots=True)
class PinStatus:
    is_set: bool
    locked_until: datetime | None
    requires_reset: bool
    reset_at: datetime | None


def _state(row: TransactionPin) -> pin_policy.PinState:
    return pin_policy.PinState(
        row.failed_attempts,
        row.locked_until,
        row.lock_count,
        row.lock_window_started_at,
        row.requires_reset,
    )


def _apply(row: TransactionPin, state: pin_policy.PinState) -> None:
    row.failed_attempts = state.failed_attempts
    row.locked_until = state.locked_until
    row.lock_count = state.lock_count
    row.lock_window_started_at = state.lock_window_started_at
    row.requires_reset = state.requires_reset


def _ensure_acceptable(pin: str) -> None:
    if not pin_policy.is_well_formed(pin) or pin_policy.is_weak(pin):
        raise PinWeakError()


async def _check_password(
    session: AsyncSession, ctx: IdentityServices, user_id: uuid.UUID, password: str
) -> User:
    user = await session.get(User, user_id)
    credential = await session.get(Credential, user_id)
    if user is None or credential is None:
        raise NotFoundError()
    if not await ctx.hasher.averify(credential.password_hash, password):
        raise PasswordIncorrectError()
    return user


async def pin_status(session: AsyncSession, ctx: IdentityServices, user_id: uuid.UUID) -> PinStatus:
    row = await session.get(TransactionPin, user_id)
    if row is None:
        return PinStatus(is_set=False, locked_until=None, requires_reset=False, reset_at=None)
    now = ctx.clock.now()
    locked_until = row.locked_until if row.locked_until and row.locked_until > now else None
    return PinStatus(True, locked_until, row.requires_reset, row.reset_at)


async def set_pin(
    ctx: IdentityServices, *, user_id: uuid.UUID, password: str, pin: str, meta: RequestMeta
) -> None:
    _ensure_acceptable(pin)
    async with ctx.db.transaction() as session:
        user = await _check_password(session, ctx, user_id, password)
        if await session.get(TransactionPin, user_id) is not None:
            raise PinAlreadySetError()
        session.add(
            TransactionPin(
                user_id=user_id,
                pin_hash=await ctx.hasher.ahash(ctx.pin_input(pin)),
                changed_at=ctx.clock.now(),
            )
        )
        await request_email(
            session,
            ctx.clock,
            ctx.cipher,
            to=user.email,
            template="pin_changed",
            locale=user.locale,
            params={"name": user.full_name},
            aggregate_id=str(user_id),
        )
        await ctx.audit(
            session,
            action="identity.pin_set",
            actor_type="USER",
            actor_id=user_id,
            target_id=user_id,
            meta=meta,
        )


async def verify_pin(
    ctx: IdentityServices, *, user_id: uuid.UUID, pin: str, meta: RequestMeta
) -> None:
    """Raise PinNotSet / PinLocked / PinInvalid(remaining) or return when the PIN is correct."""
    error: Exception | None = None
    async with ctx.db.transaction() as session:
        row = await session.get(TransactionPin, user_id, with_for_update=True)
        if row is None:
            raise PinNotSetError()
        now = ctx.clock.now()
        state = _state(row)
        if pin_policy.is_locked(state, now):
            retry = None if state.requires_reset or row.locked_until is None else row.locked_until
            headers = (
                {"Retry-After": str(max(int((retry - now).total_seconds()), 1))} if retry else None
            )
            raise PinLockedError(requires_reset=state.requires_reset, headers=headers)
        if pin_policy.is_well_formed(pin) and await ctx.hasher.averify(
            row.pin_hash, ctx.pin_input(pin)
        ):
            _apply(row, pin_policy.register_success(state))
            return
        outcome = pin_policy.register_failure(state, now)
        _apply(row, outcome.state)
        if outcome.locked:
            user = await session.get(User, user_id)
            assert user is not None  # noqa: S101
            detail = {
                "vi": "Vui lòng đặt lại PIN bằng mật khẩu và mã email."
                if outcome.state.requires_reset
                else "Thử lại sau 30 phút.",
                "en": "Please reset the PIN with your password and an e-mail code."
                if outcome.state.requires_reset
                else "Try again in 30 minutes.",
            }
            await request_email(
                session,
                ctx.clock,
                ctx.cipher,
                to=user.email,
                template="pin_locked",
                locale=user.locale,
                params={"name": user.full_name, "detail": detail.get(user.locale, detail["vi"])},
                aggregate_id=str(user_id),
            )
            await ctx.audit(
                session,
                action="identity.pin_locked",
                actor_type="SYSTEM",
                actor_id=None,
                target_id=user_id,
                meta=meta,
                details={"requires_reset": outcome.state.requires_reset},
            )
            error = PinLockedError(requires_reset=outcome.state.requires_reset)
        else:
            error = PinInvalidError(outcome.remaining_attempts)
    # Raised after commit so the failure counter is persisted.
    raise error


async def change_pin(
    ctx: IdentityServices, *, user_id: uuid.UUID, current_pin: str, new_pin: str, meta: RequestMeta
) -> None:
    _ensure_acceptable(new_pin)
    await verify_pin(ctx, user_id=user_id, pin=current_pin, meta=meta)
    async with ctx.db.transaction() as session:
        row = await session.get(TransactionPin, user_id, with_for_update=True)
        user = await session.get(User, user_id)
        assert row is not None  # noqa: S101 - FK guarantees
        assert user is not None  # noqa: S101
        row.pin_hash = await ctx.hasher.ahash(ctx.pin_input(new_pin))
        row.changed_at = ctx.clock.now()
        await request_email(
            session,
            ctx.clock,
            ctx.cipher,
            to=user.email,
            template="pin_changed",
            locale=user.locale,
            params={"name": user.full_name},
            aggregate_id=str(user_id),
        )
        await ctx.audit(
            session,
            action="identity.pin_changed",
            actor_type="USER",
            actor_id=user_id,
            target_id=user_id,
            meta=meta,
        )


async def request_pin_reset(
    ctx: IdentityServices, *, user_id: uuid.UUID, password: str, meta: RequestMeta
) -> None:
    async with ctx.db.transaction() as session:
        user = await _check_password(session, ctx, user_id, password)
        if await session.get(TransactionPin, user_id) is None:
            raise PinNotSetError()
        code = await issue_code(
            session, ctx, user_id=user_id, purpose="RESET_PIN", ttl=EMAIL_CODE_TTL
        )
        await request_email(
            session,
            ctx.clock,
            ctx.cipher,
            to=user.email,
            template="pin_reset_code",
            locale=user.locale,
            params={
                "name": user.full_name,
                "minutes": str(int(EMAIL_CODE_TTL.total_seconds() // 60)),
            },
            secret_params={"code": code},
            aggregate_id=str(user_id),
        )


async def confirm_pin_reset(
    ctx: IdentityServices, *, user_id: uuid.UUID, code: str, new_pin: str, meta: RequestMeta
) -> None:
    _ensure_acceptable(new_pin)
    done = False
    async with ctx.db.transaction() as session:
        token = await consume_code(session, ctx, user_id=user_id, purpose="RESET_PIN", code=code)
        if token is not None:
            row = await session.get(TransactionPin, user_id, with_for_update=True)
            user = await session.get(User, user_id)
            assert row is not None  # noqa: S101 - FK guarantees
            assert user is not None  # noqa: S101
            now = ctx.clock.now()
            row.pin_hash = await ctx.hasher.ahash(ctx.pin_input(new_pin))
            row.changed_at = now
            row.reset_at = now  # risk rules apply a cooldown after a reset (security.md §8)
            _apply(row, pin_policy.PinState(0, None, 0, None, False))
            await request_email(
                session,
                ctx.clock,
                ctx.cipher,
                to=user.email,
                template="pin_changed",
                locale=user.locale,
                params={"name": user.full_name},
                aggregate_id=str(user_id),
            )
            await ctx.audit(
                session,
                action="identity.pin_reset",
                actor_type="USER",
                actor_id=user_id,
                target_id=user_id,
                meta=meta,
            )
            done = True
    if not done:
        raise VerificationCodeInvalidError()
