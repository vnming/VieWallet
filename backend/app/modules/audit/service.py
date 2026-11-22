"""Audit recording and hash-chain verification.

Each entry stores ``hash = SHA-256(prev_hash || canonical JSON of the entry)``. Entries are
appended under a transaction-scoped advisory lock, so the chain is linear even with
concurrent writers. Any later change to a row (impossible for the app role, blocked by a
trigger for everyone else) breaks the chain and is reported by ``verify_chain``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.context import get_correlation_id
from app.core.ids import new_id
from app.core.logging import redact
from app.modules.audit.models import AuditLog

GENESIS_HASH = "0" * 64
# Arbitrary constant identifying the audit chain lock (pg_advisory_xact_lock key).
_CHAIN_LOCK_KEY = 0x5657_4155_4449_5400 & 0x7FFF_FFFF_FFFF_FFFF


@dataclass(frozen=True, slots=True)
class AuditEntry:
    action: str
    actor_type: str
    actor_id: uuid.UUID | None = None
    actor_roles: tuple[str, ...] = ()
    target_type: str | None = None
    target_id: str | None = None
    result: str = "SUCCESS"
    reason: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    details: dict[str, Any] | None = None
    ip: str | None = None
    user_agent: str | None = None
    correlation_id: str | None = field(default=None)


def _canonical(row: AuditLog) -> str:
    payload = {
        "id": str(row.id),
        "at": row.at.isoformat(),
        "actor_type": row.actor_type,
        "actor_id": str(row.actor_id) if row.actor_id else None,
        "actor_roles": list(row.actor_roles),
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "result": row.result,
        "reason": row.reason,
        "before": row.before,
        "after": row.after,
        "details": row.details,
        "ip": row.ip,
        "user_agent": row.user_agent,
        "correlation_id": row.correlation_id,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(prev_hash: str, row: AuditLog) -> str:
    return hashlib.sha256((prev_hash + _canonical(row)).encode()).hexdigest()


async def record(session: AsyncSession, clock: Clock, entry: AuditEntry) -> AuditLog:
    """Append an audit entry inside the caller's transaction."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CHAIN_LOCK_KEY})
    prev_hash = await session.scalar(select(AuditLog.hash).order_by(AuditLog.seq.desc()).limit(1))
    row = AuditLog(
        id=new_id(),
        at=clock.now(),
        actor_type=entry.actor_type,
        actor_id=entry.actor_id,
        actor_roles=sorted(entry.actor_roles),
        action=entry.action,
        target_type=entry.target_type,
        target_id=entry.target_id,
        result=entry.result,
        reason=entry.reason,
        before=redact(entry.before) if entry.before else None,
        after=redact(entry.after) if entry.after else None,
        details=redact(entry.details) if entry.details else None,
        ip=entry.ip,
        user_agent=(entry.user_agent or "")[:300] or None,
        correlation_id=entry.correlation_id or get_correlation_id(),
        prev_hash=prev_hash or GENESIS_HASH,
    )
    row.hash = compute_hash(row.prev_hash, row)
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True, slots=True)
class ChainVerification:
    ok: bool
    checked: int
    first_broken_seq: int | None


async def verify_chain(session: AsyncSession, *, batch_size: int = 1000) -> ChainVerification:
    expected_prev = GENESIS_HASH
    checked = 0
    last_seq = 0
    while True:
        rows = (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.seq > last_seq)
                .order_by(AuditLog.seq)
                .limit(batch_size)
            )
        ).all()
        if not rows:
            return ChainVerification(ok=True, checked=checked, first_broken_seq=None)
        for row in rows:
            if row.prev_hash != expected_prev or compute_hash(row.prev_hash, row) != row.hash:
                return ChainVerification(ok=False, checked=checked, first_broken_seq=row.seq)
            expected_prev = row.hash
            checked += 1
            last_seq = row.seq


@dataclass(frozen=True, slots=True)
class AuditPage:
    items: list[AuditLog]
    total: int


async def list_entries(
    session: AsyncSession,
    *,
    action: str | None = None,
    actor_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> AuditPage:
    conditions = []
    if action:
        conditions.append(AuditLog.action == action)
    if actor_id:
        conditions.append(AuditLog.actor_id == actor_id)
    if target_type:
        conditions.append(AuditLog.target_type == target_type)
    if target_id:
        conditions.append(AuditLog.target_id == target_id)
    total = int(
        await session.scalar(select(func.count()).select_from(AuditLog).where(*conditions)) or 0
    )
    items = (
        await session.scalars(
            select(AuditLog)
            .where(*conditions)
            .order_by(AuditLog.seq.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return AuditPage(items=list(items), total=total)
