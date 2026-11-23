"""Simulator behaviour: charges, payouts, checkout, scheduled actions, signed callbacks."""

from __future__ import annotations

import hashlib
import json
import random
import secrets
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sim.config import SimSettings
from sim.db import SimClock, SimDatabase
from sim.models import SimCallback, SimLog, SimOperation, SimSetting
from sim.scenarios import Mode, Scenario, scenario_for
from sim.signing import signature

logger = structlog.get_logger("sim")

BUSINESS_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
CALLBACK_BACKOFF = (10, 30, 120, 600, 3600)
INVALID_SIGNATURE = "v1=" + "0" * 64


class SimError(Exception):
    status = 400
    code = "BAD_REQUEST"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.code)
        self.detail = detail or self.code


class ProviderUnavailable(SimError):
    status = 503
    code = "PROVIDER_UNAVAILABLE"


class KeyConflict(SimError):
    status = 409
    code = "OPERATION_KEY_CONFLICT"


class NotFound(SimError):
    status = 404
    code = "NOT_FOUND"


class InvalidState(SimError):
    status = 409
    code = "INVALID_STATE"


class ConnectionReset(Exception):
    """The API layer drops the connection without a complete response (scenario 444)."""


def public_status(op: SimOperation) -> str:
    return "PENDING" if op.status in {"AWAITING_CUSTOMER", "PROCESSING"} else op.status


def operation_view(op: SimOperation, settings: SimSettings) -> dict[str, Any]:
    view: dict[str, Any] = {
        "operation_key": op.operation_key,
        "provider_reference": op.provider_reference,
        "kind": op.kind,
        "status": public_status(op),
        "amount": str(op.amount_minor),
        "currency": op.currency,
        "reference": op.reference,
        "failure_code": op.failure_code,
    }
    if op.kind == "CHARGE" and op.status == "AWAITING_CUSTOMER":
        view["checkout_url"] = f"{settings.public_base_url}/checkout/{op.provider_reference}"
    return view


def request_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CreateResult:
    body: dict[str, Any]
    delay_seconds: float = 0.0


Sender = Callable[[str, bytes, dict[str, str]], Awaitable[int]]


class Simulator:
    def __init__(
        self, db: SimDatabase, settings: SimSettings, clock: SimClock, sender: Sender
    ) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock
        self.sender = sender

    # ------------------------------------------------------------------ settings & logs

    async def mode(self, session: AsyncSession) -> Mode:
        row = await session.get(SimSetting, "mode")
        return Mode(row.value["mode"]) if row else Mode.NORMAL

    async def set_mode(self, mode: Mode) -> None:
        async with self.db.transaction() as session:
            await session.execute(
                insert(SimSetting)
                .values(key="mode", value={"mode": mode.value})
                .on_conflict_do_update(index_elements=["key"], set_={"value": {"mode": mode.value}})
            )

    async def log(
        self,
        session: AsyncSession,
        *,
        direction: str,
        method: str,
        path: str,
        status_code: int | None,
        operation_key: str | None = None,
        detail: str | None = None,
    ) -> None:
        session.add(
            SimLog(
                id=uuid.uuid4(),
                at=self.clock.now(),
                direction=direction,
                method=method,
                path=path[:200],
                status_code=status_code,
                operation_key=operation_key,
                detail=detail[:300] if detail else None,
            )
        )

    # ------------------------------------------------------------------ create

    async def _gate(self, session: AsyncSession, scenario: Scenario) -> float:
        """Failures that happen before anything is created. Returns extra latency."""
        mode = await self.mode(session)
        if mode is Mode.OUTAGE or scenario is Scenario.UNAVAILABLE:
            raise ProviderUnavailable()
        if scenario is Scenario.CONNECTION_RESET:
            raise ConnectionReset()
        if mode is Mode.DEGRADED:
            if random.random() < 0.2:  # noqa: S311 - simulation, not security
                raise ProviderUnavailable("degraded")
            return 1 + random.random() * 4  # noqa: S311
        return 0.0

    async def _create(
        self, kind: str, payload: Mapping[str, Any], *, destination: dict[str, Any] | None
    ) -> CreateResult:
        key = str(payload["operation_key"])
        amount = int(str(payload["amount"]))
        fingerprint = request_hash({**payload, "kind": kind})
        scenario = scenario_for(amount)
        async with self.db.transaction() as session:
            existing = await session.scalar(
                select(SimOperation).where(SimOperation.operation_key == key)
            )
            if existing is not None:
                if existing.request_hash != fingerprint:
                    raise KeyConflict()
                return CreateResult(operation_view(existing, self.settings))
            extra_latency = await self._gate(session, scenario)
            now = self.clock.now()
            op = SimOperation(
                id=uuid.uuid4(),
                operation_key=key,
                provider_reference=f"SIM{kind[0]}{secrets.token_hex(8).upper()}",
                kind=kind,
                status="AWAITING_CUSTOMER" if kind == "CHARGE" else "PROCESSING",
                scenario=scenario.value,
                amount_minor=amount,
                currency=str(payload["currency"]),
                reference=str(payload["reference"]),
                method=str(payload.get("method") or "") or None,
                destination=destination,
                callback_url=str(payload["callback_url"]),
                return_url=str(payload.get("return_url") or "") or None,
                request_hash=fingerprint,
                business_date=now.astimezone(BUSINESS_TZ).date(),
                created_at=now,
                updated_at=now,
            )
            if scenario is Scenario.FAIL:
                op.status = "FAILED"
                op.failure_code = "INSUFFICIENT_FUNDS" if kind == "CHARGE" else "ACCOUNT_CLOSED"
                op.resolved_at = now
            elif kind == "CHARGE":
                op.next_action = "EXPIRE"
                op.next_action_at = now + timedelta(hours=self.settings.charge_ttl_hours)
            else:
                delay = (
                    self.settings.pending_delay_seconds
                    if scenario is Scenario.DELAYED
                    else self.settings.payout_delay_seconds
                )
                op.next_action = "SUCCEED"
                op.next_action_at = now + timedelta(seconds=delay)
            # Concurrent requests with the same key: the unique constraint picks one winner.
            inserted = await session.scalar(
                insert(SimOperation)
                .values(**{c.key: getattr(op, c.key) for c in SimOperation.__table__.columns})
                .on_conflict_do_nothing(index_elements=["operation_key"])
                .returning(SimOperation.id)
            )
            if inserted is None:
                winner = await session.scalar(
                    select(SimOperation).where(SimOperation.operation_key == key)
                )
                assert winner is not None  # noqa: S101
                if winner.request_hash != fingerprint:
                    raise KeyConflict()
                return CreateResult(operation_view(winner, self.settings))
            stored = await session.get(SimOperation, inserted)
            assert stored is not None  # noqa: S101
            op = stored
            delay = extra_latency
            if scenario is Scenario.SLOW_RESPONSE:
                delay += self.settings.slow_response_seconds
            return CreateResult(operation_view(op, self.settings), delay)

    async def create_charge(self, payload: Mapping[str, Any]) -> CreateResult:
        return await self._create("CHARGE", payload, destination=None)

    async def create_payout(self, payload: Mapping[str, Any]) -> CreateResult:
        destination = dict(payload.get("destination") or {})
        return await self._create("PAYOUT", payload, destination=destination)

    async def get_operation(self, key: str) -> dict[str, Any]:
        async with self.db.transaction() as session:
            op = await session.scalar(select(SimOperation).where(SimOperation.operation_key == key))
            if op is None:
                raise NotFound()
            return operation_view(op, self.settings)

    async def by_reference(self, session: AsyncSession, reference: str) -> SimOperation:
        op = await session.scalar(
            select(SimOperation)
            .where(SimOperation.provider_reference == reference)
            .with_for_update()
        )
        if op is None:
            raise NotFound()
        return op

    # ------------------------------------------------------------------ transitions

    async def _resolve(
        self,
        session: AsyncSession,
        op: SimOperation,
        status: str,
        event_type: str,
        *,
        failure_code: str | None = None,
    ) -> None:
        now = self.clock.now()
        op.status = status
        op.failure_code = failure_code
        op.updated_at = now
        op.resolved_at = now
        op.next_action = None
        op.next_action_at = None
        if status == "SUCCEEDED" and op.kind == "PAYOUT" and op.scenario == Scenario.RETURNED:
            op.next_action = "RETURN"
            op.next_action_at = now + timedelta(seconds=self.settings.return_delay_seconds)
        await self._enqueue_callbacks(session, op, event_type)

    async def _enqueue_callbacks(
        self, session: AsyncSession, op: SimOperation, event_type: str
    ) -> None:
        now = self.clock.now()
        event_id = f"evt_{uuid.uuid4().hex}"
        payload = {
            "event_id": event_id,
            "type": event_type,
            "provider_reference": op.provider_reference,
            "operation_key": op.operation_key,
            "amount": str(op.amount_minor),
            "currency": op.currency,
            "failure_code": op.failure_code,
            "occurred_at": now.isoformat(),
        }
        later = now + timedelta(seconds=self.settings.followup_callback_seconds)
        plan: list[tuple[datetime, bool]] = [(now, True)]
        if op.scenario == Scenario.DUPLICATE_CALLBACKS:
            plan = [(now, True), (now, True), (now, True), (later, True)]
        elif op.scenario == Scenario.BAD_SIGNATURE_FIRST:
            plan = [(now, False), (later, True)]
        for at, valid in plan:
            session.add(
                SimCallback(
                    id=uuid.uuid4(),
                    event_id=event_id,
                    operation_id=op.id,
                    type=event_type,
                    payload=payload,
                    valid_signature=valid,
                    status="PENDING",
                    attempts=0,
                    next_attempt_at=at,
                    created_at=now,
                )
            )
        await session.flush()

    async def checkout_decision(self, reference: str, *, approve: bool) -> SimOperation:
        async with self.db.transaction() as session:
            op = await self.by_reference(session, reference)
            if op.kind != "CHARGE" or op.status != "AWAITING_CUSTOMER":
                raise InvalidState()
            if not approve:
                await self._resolve(
                    session, op, "FAILED", "charge.failed", failure_code="CUSTOMER_DECLINED"
                )
            elif op.scenario == Scenario.DELAYED:
                op.status = "PROCESSING"
                op.updated_at = self.clock.now()
                op.next_action = "SUCCEED"
                op.next_action_at = self.clock.now() + timedelta(
                    seconds=self.settings.pending_delay_seconds
                )
            else:
                await self._resolve(session, op, "SUCCEEDED", "charge.succeeded")
            return op

    async def admin_action(self, key: str, action: str) -> dict[str, Any]:
        async with self.db.transaction() as session:
            op = await session.scalar(
                select(SimOperation).where(SimOperation.operation_key == key).with_for_update()
            )
            if op is None:
                raise NotFound()
            prefix = op.kind.lower()
            if action == "SUCCEED" and op.status in {"AWAITING_CUSTOMER", "PROCESSING"}:
                await self._resolve(session, op, "SUCCEEDED", f"{prefix}.succeeded")
            elif action == "FAIL" and op.status in {"AWAITING_CUSTOMER", "PROCESSING"}:
                await self._resolve(
                    session, op, "FAILED", f"{prefix}.failed", failure_code="FORCED"
                )
            elif action == "EXPIRE" and op.kind == "CHARGE" and op.status == "AWAITING_CUSTOMER":
                await self._resolve(session, op, "EXPIRED", "charge.expired")
            elif action == "RETURN" and op.kind == "PAYOUT" and op.status == "SUCCEEDED":
                await self._resolve(session, op, "RETURNED", "payout.returned")
            elif action == "RESEND_CALLBACK":
                last = await session.scalar(
                    select(SimCallback)
                    .where(SimCallback.operation_id == op.id, SimCallback.valid_signature)
                    .order_by(SimCallback.created_at.desc())
                    .limit(1)
                )
                if last is None:
                    raise InvalidState("no callback to resend")
                session.add(
                    SimCallback(
                        id=uuid.uuid4(),
                        event_id=last.event_id,
                        operation_id=op.id,
                        type=last.type,
                        payload=last.payload,
                        valid_signature=True,
                        status="PENDING",
                        attempts=0,
                        next_attempt_at=self.clock.now(),
                        created_at=self.clock.now(),
                    )
                )
            else:
                raise InvalidState(f"{action} not allowed in status {op.status}")
            return operation_view(op, self.settings)

    # ------------------------------------------------------------------ background work

    async def run_due_actions(self, *, batch: int = 50) -> int:
        async with self.db.transaction() as session:
            ops = (
                await session.scalars(
                    select(SimOperation)
                    .where(
                        SimOperation.next_action.is_not(None),
                        SimOperation.next_action_at <= self.clock.now(),
                    )
                    .order_by(SimOperation.next_action_at)
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for op in ops:
                prefix = op.kind.lower()
                if op.next_action == "SUCCEED":
                    await self._resolve(session, op, "SUCCEEDED", f"{prefix}.succeeded")
                elif op.next_action == "EXPIRE":
                    await self._resolve(session, op, "EXPIRED", "charge.expired")
                elif op.next_action == "RETURN":
                    await self._resolve(session, op, "RETURNED", "payout.returned")
            return len(ops)

    async def deliver_due_callbacks(self, *, batch: int = 20) -> int:
        now = self.clock.now()
        lease = timedelta(seconds=self.settings.callback_timeout_seconds * 3)
        async with self.db.transaction() as session:
            rows = (
                await session.scalars(
                    select(SimCallback)
                    .where(SimCallback.status == "PENDING", SimCallback.next_attempt_at <= now)
                    .order_by(SimCallback.next_attempt_at, SimCallback.created_at)
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            claimed = []
            for row in rows:
                row.next_attempt_at = now + lease  # lease: no lock is held during the HTTP call
                op = await session.get(SimOperation, row.operation_id)
                assert op is not None  # noqa: S101
                claimed.append((row.id, op.callback_url, dict(row.payload), row.valid_signature))
        for callback_id, url, payload, valid in claimed:
            body = json.dumps(payload, separators=(",", ":")).encode()
            timestamp = str(int(self.clock.now().timestamp()))
            headers = {
                "Content-Type": "application/json",
                "X-Sim-Timestamp": timestamp,
                "X-Sim-Event-Id": str(payload["event_id"]),
                "X-Sim-Signature": signature(self.settings.webhook_secret, timestamp, body)
                if valid
                else INVALID_SIGNATURE,
            }
            try:
                status_code = await self.sender(url, body, headers)
            except Exception as exc:
                logger.warning("sim_callback_failed", error=type(exc).__name__)
                status_code = 0
            await self._record_delivery(callback_id, status_code, valid, url, payload)
        return len(claimed)

    async def _record_delivery(
        self,
        callback_id: uuid.UUID,
        status_code: int,
        valid: bool,
        url: str,
        payload: dict[str, Any],
    ) -> None:
        now = self.clock.now()
        async with self.db.transaction() as session:
            row = await session.get(SimCallback, callback_id, with_for_update=True)
            assert row is not None  # noqa: S101
            row.attempts += 1
            row.last_response_code = status_code or None
            if 200 <= status_code < 300:
                row.status = "DELIVERED"
                row.delivered_at = now
            elif not valid or row.attempts > len(CALLBACK_BACKOFF):
                row.status = "GIVEN_UP"  # a forged-looking callback is not retried
            else:
                row.next_attempt_at = now + timedelta(seconds=CALLBACK_BACKOFF[row.attempts - 1])
            await self.log(
                session,
                direction="OUT",
                method="POST",
                path=url,
                status_code=status_code or None,
                operation_key=str(payload["operation_key"]),
                detail=f"{payload['type']} valid_signature={valid}",
            )

    async def run_once(self) -> int:
        return await self.run_due_actions() + await self.deliver_due_callbacks()

    # ------------------------------------------------------------------ statements & admin

    async def statement(self, business_date: date) -> list[dict[str, Any]]:
        async with self.db.transaction() as session:
            ops = (
                await session.scalars(
                    select(SimOperation)
                    .where(SimOperation.business_date == business_date)
                    .order_by(SimOperation.created_at)
                )
            ).all()
        lines = []
        for op in ops:
            amount = op.amount_minor
            if op.scenario == Scenario.STATEMENT_MISMATCH:
                amount += 1000  # injected discrepancy for reconciliation tests
            lines.append(
                {
                    "provider_reference": op.provider_reference,
                    "operation_key": op.operation_key,
                    "kind": op.kind,
                    "status": public_status(op),
                    "amount": str(amount),
                    "currency": op.currency,
                    "reference": op.reference,
                }
            )
        return lines

    async def list_operations(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.db.transaction() as session:
            ops = (
                await session.scalars(
                    select(SimOperation).order_by(SimOperation.created_at.desc()).limit(limit)
                )
            ).all()
            return [operation_view(op, self.settings) | {"scenario": op.scenario} for op in ops]

    async def list_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self.db.transaction() as session:
            rows = (
                await session.scalars(select(SimLog).order_by(SimLog.at.desc()).limit(limit))
            ).all()
            return [
                {
                    "at": r.at.isoformat(),
                    "direction": r.direction,
                    "method": r.method,
                    "path": r.path,
                    "status_code": r.status_code,
                    "operation_key": r.operation_key,
                    "detail": r.detail,
                }
                for r in rows
            ]
