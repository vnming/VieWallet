"""Reconciliation against the provider's daily statement (FR-OPS-03, FT-28).

The statement is fetched before any DB transaction opens (no external I/O while holding a
connection or locks). Then every statement line is matched to ``provider_operations`` by the
operation key, and every internal operation submitted that business day must appear in the
statement. Differences become reconciliation items; matching lines produce nothing. Items never
move money: Finance fixes a real difference with an adjustment or reversal (maker-checker) and
records the outcome on the item.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import ConflictError, NotFoundError, register_titles
from app.core.ids import new_id
from app.core.outbox import publish
from app.modules.audit.public import AuditEntry, record
from app.modules.operations.models import ReconciliationItem, ReconciliationRun
from app.modules.providers.public import OPEN_STATUSES, ProviderOperation, StatementLine

register_titles(
    {
        "RECONCILIATION_ITEM_NOT_FOUND": {
            "vi": "Không tìm thấy mục đối soát",
            "en": "Reconciliation item not found",
        },
        "RECONCILIATION_ITEM_CLOSED": {
            "vi": "Mục đối soát đã đóng",
            "en": "The reconciliation item is already closed",
        },
    }
)

BUSINESS_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
ITEM_STATUSES = ("OPEN", "INVESTIGATING", "RESOLVED", "WRITTEN_OFF")
CLOSED = ("RESOLVED", "WRITTEN_OFF")


class ReconciliationItemNotFoundError(NotFoundError):
    code = "RECONCILIATION_ITEM_NOT_FOUND"


class ReconciliationItemClosedError(ConflictError):
    code = "RECONCILIATION_ITEM_CLOSED"


def day_bounds(business_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(business_date, time.min, BUSINESS_TZ).astimezone(UTC)
    return start, start + timedelta(days=1)


def _provider_view(status: str) -> str:
    """Internal status as the provider would report it."""
    return "PENDING" if status in OPEN_STATUSES else status


@dataclass(frozen=True, slots=True)
class Difference:
    type: str
    subject: str
    expected: str
    actual: str


def compare(
    lines: list[StatementLine], internal: dict[str, ProviderOperation], submitted_today: set[str]
) -> list[Difference]:
    """Pure comparison (unit-tested). ``internal`` is keyed by operation key."""
    found: list[Difference] = []
    seen: set[str] = set()
    for line in lines:
        seen.add(line.operation_key)
        op = internal.get(line.operation_key)
        subject = f"{line.kind}:{line.provider_reference or line.operation_key}"
        if op is None:
            found.append(Difference("MISSING_INTERNAL", subject, "-", str(line.amount_minor)))
            continue
        if op.amount_minor != line.amount_minor or op.currency != line.currency:
            found.append(
                Difference(
                    "AMOUNT_MISMATCH",
                    subject,
                    f"{op.amount_minor} {op.currency}",
                    f"{line.amount_minor} {line.currency}",
                )
            )
        elif _provider_view(op.status) != line.status:
            found.append(Difference("STATUS_MISMATCH", subject, op.status, line.status))
    for key in sorted(submitted_today - seen):
        op = internal[key]
        found.append(
            Difference(
                "MISSING_PROVIDER",
                f"{op.operation}:{op.provider_reference or key}",
                f"{op.amount_minor} {op.currency}",
                "-",
            )
        )
    return found


async def run_provider(
    session: AsyncSession,
    clock: Clock,
    *,
    business_date: date,
    lines: list[StatementLine],
    provider_code: str,
    triggered_by: uuid.UUID | None,
) -> tuple[ReconciliationRun, list[ReconciliationItem]]:
    started = clock.now()
    start, end = day_bounds(business_date)
    keys = [line.operation_key for line in lines]
    valid_ids = []
    for key in keys:
        try:
            valid_ids.append(uuid.UUID(key))
        except ValueError:
            continue
    rows = (
        await session.scalars(
            select(ProviderOperation).where(
                ProviderOperation.provider_code == provider_code,
                or_(
                    ProviderOperation.id.in_(valid_ids),
                    and_(
                        ProviderOperation.submitted_at >= start,
                        ProviderOperation.submitted_at < end,
                    ),
                ),
            )
        )
    ).all()
    internal = {str(op.id): op for op in rows}
    submitted_today = {
        str(op.id)
        for op in rows
        if op.submitted_at is not None and start <= op.submitted_at < end and op.status != "CREATED"
    }
    differences = compare(lines, internal, submitted_today)
    run = ReconciliationRun(
        id=new_id(),
        kind="PROVIDER",
        status="SUCCEEDED",
        stats={
            "business_date": business_date.isoformat(),
            "provider": provider_code,
            "statement_lines": len(lines),
            "internal_operations": len(submitted_today),
            "matched": len(lines) - sum(d.type != "MISSING_PROVIDER" for d in differences),
        },
        violations=len(differences),
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
            type=d.type,
            invariant=None,
            subject=d.subject[:120],
            expected=d.expected[:64],
            actual=d.actual[:64],
            status="OPEN",
            created_at=started,
        )
        for d in differences
    ]
    session.add_all(items)
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="reconciliation.provider_run",
            actor_type="STAFF" if triggered_by else "SYSTEM",
            actor_id=triggered_by,
            target_type="reconciliation_run",
            target_id=str(run.id),
            result="SUCCESS" if not differences else "FAILED",
            details={"business_date": business_date.isoformat(), "differences": len(differences)},
        ),
    )
    if differences:
        await publish(
            session,
            clock,
            topic="reconciliation.violation_found",
            aggregate_type="reconciliation_run",
            aggregate_id=str(run.id),
            payload={"run_id": str(run.id), "violations": len(differences)},
        )
    return run, items


async def update_item(
    session: AsyncSession,
    clock: Clock,
    item_id: uuid.UUID,
    *,
    status: str,
    note: str,
    actor_id: uuid.UUID,
) -> ReconciliationItem:
    item = await session.scalar(
        select(ReconciliationItem).where(ReconciliationItem.id == item_id).with_for_update()
    )
    if item is None:
        raise ReconciliationItemNotFoundError()
    if item.status in CLOSED:
        raise ReconciliationItemClosedError()
    before = item.status
    item.status = status
    item.resolution_note = note[:500]
    item.updated_by = actor_id
    item.updated_at = clock.now()
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="reconciliation.item_updated",
            actor_type="STAFF",
            actor_id=actor_id,
            target_type="reconciliation_item",
            target_id=str(item.id),
            before={"status": before},
            after={"status": status},
            reason=note,
        ),
    )
    return item


async def open_items(session: AsyncSession, *, limit: int = 100) -> list[ReconciliationItem]:
    rows = await session.scalars(
        select(ReconciliationItem)
        .where(ReconciliationItem.status.in_(("OPEN", "INVESTIGATING")))
        .order_by(ReconciliationItem.created_at.desc())
        .limit(limit)
    )
    return list(rows.all())
