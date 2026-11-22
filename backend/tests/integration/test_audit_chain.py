"""Audit log: append-only storage and hash-chain tamper evidence (FR-OPS-05, T8)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.clock import FixedClock
from app.core.db import Database, sqlstate_of
from app.modules.audit.public import AuditEntry, AuditLog, record, verify_chain


async def add_entries(db: Database, clock: FixedClock, n: int, action: str) -> list[uuid.UUID]:
    ids = []
    for i in range(n):
        async with db.transaction() as session:
            row = await record(
                session,
                clock,
                AuditEntry(
                    action=action,
                    actor_type="SYSTEM",
                    details={"i": i, "password": "must-not-be-stored"},
                ),
            )
            ids.append(row.id)
    return ids


async def test_chain_is_valid_and_sensitive_details_are_redacted(
    db: Database, clock: FixedClock
) -> None:
    ids = await add_entries(db, clock, 3, "test.chain")
    async with db.transaction() as session:
        rows = (
            await session.scalars(
                select(AuditLog).where(AuditLog.id.in_(ids)).order_by(AuditLog.seq)
            )
        ).all()
        result = await verify_chain(session)
    assert result.ok
    assert result.checked >= 3
    assert rows[1].prev_hash == rows[0].hash
    assert rows[2].prev_hash == rows[1].hash
    assert all(r.details and r.details["password"] == "***" for r in rows)


async def test_concurrent_writers_keep_a_linear_chain(db: Database, clock: FixedClock) -> None:
    async def write(i: int) -> None:
        async with db.transaction() as session:
            await record(
                session,
                clock,
                AuditEntry(action="test.concurrent", actor_type="SYSTEM", details={"i": i}),
            )

    await asyncio.gather(*(write(i) for i in range(25)))
    async with db.transaction() as session:
        assert (await verify_chain(session)).ok


async def test_tampering_is_detected(
    db: Database, migrator_db: Database, clock: FixedClock
) -> None:
    (target,) = await add_entries(db, clock, 1, "test.tamper")
    # Simulate an attacker with owner rights: bypass the trigger, change a row, then undo it
    # (the table is append-only, so the test must leave it as it found it).
    async with migrator_db.transaction() as session:
        original = await session.scalar(select(AuditLog.action).where(AuditLog.id == target))
        await session.execute(
            text("ALTER TABLE app.audit_logs DISABLE TRIGGER audit_logs_no_update_delete")
        )
        await session.execute(
            text("UPDATE app.audit_logs SET action = 'forged' WHERE id = :id"), {"id": target}
        )
    try:
        async with db.transaction() as session:
            result = await verify_chain(session)
            seq = await session.scalar(select(AuditLog.seq).where(AuditLog.id == target))
        assert not result.ok
        assert result.first_broken_seq == seq
    finally:
        async with migrator_db.transaction() as session:
            await session.execute(
                text("UPDATE app.audit_logs SET action = :a WHERE id = :id"),
                {"a": original, "id": target},
            )
            await session.execute(
                text("ALTER TABLE app.audit_logs ENABLE TRIGGER audit_logs_no_update_delete")
            )
    async with db.transaction() as session:
        assert (await verify_chain(session)).ok


@pytest.mark.parametrize(
    "statement", ["UPDATE app.audit_logs SET action = 'x'", "DELETE FROM app.audit_logs"]
)
async def test_app_role_cannot_modify_audit_logs(db: Database, statement: str) -> None:
    with pytest.raises(ProgrammingError) as exc_info:
        async with db.transaction() as session:
            await session.execute(text(statement))
    assert sqlstate_of(exc_info.value) == "42501"


async def test_owner_is_blocked_by_trigger(migrator_db: Database) -> None:
    with pytest.raises(DBAPIError) as exc_info:
        async with migrator_db.transaction() as session:
            # TRUNCATE fires the statement-level trigger even when the table is empty.
            await session.execute(text("TRUNCATE app.login_history"))
    assert sqlstate_of(exc_info.value) == "VW001"
