"""Internal reconciliation (financial-processing.md §10): invariants I1-I5 over the ledger."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.ids import new_id
from app.core.outbox import publish
from app.modules.audit.public import AuditEntry, record
from app.modules.ledger.public import (
    AccountBalance,
    AccountCode,
    InvariantViolation,
    Journal,
    LedgerAccount,
    check_invariants,
)
from app.modules.operations.models import ReconciliationItem, ReconciliationRun
from app.modules.transactions.public import held_totals

logger = structlog.get_logger(__name__)


async def hold_violations(session: AsyncSession) -> list[InvariantViolation]:
    """I5: HOLD balance per currency equals Σ held amounts of transactions."""
    held = await held_totals(session)
    rows = await session.execute(
        select(LedgerAccount.currency, func.sum(AccountBalance.balance_minor))
        .join(AccountBalance, AccountBalance.account_id == LedgerAccount.id)
        .where(LedgerAccount.code == AccountCode.HOLD.value)
        .group_by(LedgerAccount.currency)
    )
    balances = {currency: int(total) for currency, total in rows.all()}
    return [
        InvariantViolation("I5", f"currency:{c}", str(held.get(c, 0)), str(balances.get(c, 0)))
        for c in sorted(set(held) | set(balances))
        if held.get(c, 0) != balances.get(c, 0)
    ]


@dataclass(frozen=True, slots=True)
class RunResult:
    run: ReconciliationRun
    items: list[ReconciliationItem]


async def run_internal(
    session: AsyncSession, clock: Clock, *, triggered_by: uuid.UUID | None
) -> RunResult:
    started = clock.now()
    violations = [*await check_invariants(session), *await hold_violations(session)]
    stats = {
        "journals": int(await session.scalar(select(func.count()).select_from(Journal)) or 0),
        "accounts": int(await session.scalar(select(func.count()).select_from(LedgerAccount)) or 0),
    }
    run = ReconciliationRun(
        id=new_id(),
        kind="INTERNAL",
        status="SUCCEEDED",
        stats=stats | {"checked": ["I1", "I2", "I3", "I4", "I5"]},
        violations=len(violations),
        triggered_by=triggered_by,
        started_at=started,
        finished_at=clock.now(),
    )
    session.add(run)
    await session.flush()
    items = [
        ReconciliationItem(
            id=new_id(),
            run_id=run.id,
            type="INVARIANT_VIOLATION",
            invariant=v.invariant,
            subject=v.subject[:120],
            expected=v.expected[:64],
            actual=v.actual[:64],
            status="OPEN",
            created_at=started,
        )
        for v in violations
    ]
    session.add_all(items)
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="reconciliation.internal_run",
            actor_type="STAFF" if triggered_by else "SYSTEM",
            actor_id=triggered_by,
            target_type="reconciliation_run",
            target_id=str(run.id),
            result="SUCCESS" if not violations else "FAILED",
            details={"violations": len(violations)},
        ),
    )
    if violations:
        logger.error(
            "ledger_invariant_violation",
            run_id=str(run.id),
            invariants=sorted({v.invariant for v in violations}),
            count=len(violations),
        )
        await publish(
            session,
            clock,
            topic="reconciliation.violation_found",
            aggregate_type="reconciliation_run",
            aggregate_id=str(run.id),
            payload={"run_id": str(run.id), "violations": len(violations)},
        )
    return RunResult(run, items)


@dataclass(frozen=True, slots=True)
class RunPage:
    items: list[ReconciliationRun]
    total: int


async def list_runs(session: AsyncSession, *, page: int, page_size: int) -> RunPage:
    total = await session.scalar(select(func.count()).select_from(ReconciliationRun))
    rows = await session.scalars(
        select(ReconciliationRun)
        .order_by(ReconciliationRun.started_at.desc(), ReconciliationRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return RunPage(list(rows.all()), int(total or 0))


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> RunResult | None:
    run = await session.get(ReconciliationRun, run_id)
    if run is None:
        return None
    items = await session.scalars(
        select(ReconciliationItem)
        .where(ReconciliationItem.run_id == run_id)
        .order_by(ReconciliationItem.invariant, ReconciliationItem.subject)
    )
    return RunResult(run, list(items.all()))
