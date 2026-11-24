"""CSV export of the customer's history (PRD FR-TXN-08, api-spec §6).

``POST /transactions/exports`` records a job; the worker builds the file; the customer
downloads it while it is fresh (24 hours). The file lives in the database row (it is small and
only the owner can read it); a job past its expiry loses its content.

Spreadsheet formula injection: text cells (counterparty, description) that start with ``=``,
``+``, ``-``, ``@``, tab or carriage return are prefixed with an apostrophe.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import Clock
from app.core.db import Base, Database
from app.core.errors import (
    ConflictError,
    FieldError,
    NotFoundError,
    ValidationFailedError,
    register_titles,
)
from app.core.ids import new_id
from app.modules.identity.public import User
from app.modules.transactions import service
from app.modules.transactions.categories import default_category, overrides_for
from app.modules.transactions.models import Transaction

register_titles(
    {
        "EXPORT_NOT_FOUND": {"vi": "Không tìm thấy tệp xuất", "en": "Export not found"},
        "EXPORT_NOT_READY": {"vi": "Tệp xuất chưa sẵn sàng", "en": "The export is not ready"},
    }
)

_FOREIGN_KEY_TARGETS = (User,)

MAX_DAYS = 366
MAX_ROWS = 10_000
RETENTION = timedelta(hours=24)
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
HEADERS = {
    "vi": [
        "Mã giao dịch",
        "Thời gian",
        "Loại",
        "Chiều",
        "Trạng thái",
        "Số tiền",
        "Phí",
        "Thay đổi trên ví",
        "Tiền tệ",
        "Đối tác",
        "Nội dung",
        "Danh mục",
    ],
    "en": [
        "Reference",
        "Time",
        "Type",
        "Direction",
        "Status",
        "Amount",
        "Fee",
        "Wallet change",
        "Currency",
        "Counterparty",
        "Description",
        "Category",
    ],
}


class ExportNotFoundError(NotFoundError):
    code = "EXPORT_NOT_FOUND"


class ExportNotReadyError(ConflictError):
    code = "EXPORT_NOT_READY"


class ExportJob(Base):
    __tablename__ = "export_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'READY', 'FAILED', 'EXPIRED')", name="status_valid"),
        CheckConstraint("date_to >= date_from", name="range_valid"),
        Index("ix_export_jobs_status_created", "status", "created_at"),
        Index("ix_export_jobs_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"))
    wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.wallets.id"))
    status: Mapped[str] = mapped_column(String(10))
    date_from: Mapped[date]
    date_to: Mapped[date]
    locale: Mapped[str] = mapped_column(String(2))
    row_count: Mapped[int | None]
    truncated: Mapped[bool] = mapped_column(default=False)
    content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    expires_at: Mapped[datetime]


def safe_text(value: str) -> str:
    return "'" + value if value.startswith(_FORMULA_START) else value


def check_range(date_from: date, date_to: date) -> None:
    if date_to < date_from:
        raise ValidationFailedError(errors=[FieldError("date_to", "RANGE", "before date_from")])
    if (date_to - date_from).days >= MAX_DAYS:
        raise ValidationFailedError(errors=[FieldError("date_to", "RANGE", "at most 366 days")])


async def create_job(
    session: AsyncSession,
    clock: Clock,
    *,
    user_id: uuid.UUID,
    wallet_id: uuid.UUID,
    date_from: date,
    date_to: date,
    locale: str,
) -> ExportJob:
    check_range(date_from, date_to)
    now = clock.now()
    job = ExportJob(
        id=new_id(),
        user_id=user_id,
        wallet_id=wallet_id,
        status="PENDING",
        date_from=date_from,
        date_to=date_to,
        locale=locale if locale in HEADERS else "vi",
        row_count=None,
        truncated=False,
        content=None,
        created_at=now,
        finished_at=None,
        expires_at=now + RETENTION,
    )
    session.add(job)
    await session.flush()
    return job


async def get_job(session: AsyncSession, user_id: uuid.UUID, job_id: uuid.UUID) -> ExportJob:
    job = await session.get(ExportJob, job_id)
    if job is None or job.user_id != user_id:
        raise ExportNotFoundError()  # someone else's export does not exist for them
    return job


def _local_bounds(job: ExportJob, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(job.date_from, time.min, tz)
    end = datetime.combine(job.date_to + timedelta(days=1), time.min, tz)
    return start, end


async def build_csv(session: AsyncSession, job: ExportJob, timezone: str) -> tuple[str, int, bool]:
    tz = ZoneInfo(timezone)
    start, end = _local_bounds(job, tz)
    conditions = service.history_conditions(
        job.wallet_id, service.HistoryFilter(created_from=start, created_to=end)
    )
    rows = list(
        (
            await session.scalars(
                select(Transaction)
                .where(*conditions)
                .order_by(Transaction.created_at, Transaction.id)
                .limit(MAX_ROWS + 1)
            )
        ).all()
    )
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    overrides = await overrides_for(session, job.user_id, [t.id for t in rows])
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(HEADERS[job.locale])
    for txn in rows:
        outgoing = txn.source_wallet_id == job.wallet_id
        party = (
            (txn.counterparty_snapshot if outgoing else txn.extra.get("source_party"))
            or txn.counterparty_snapshot
            or {}
        )
        change = txn.amount_minor + (txn.fee_minor if outgoing else 0)
        writer.writerow(
            [
                txn.reference,
                txn.created_at.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S"),
                txn.type,
                "OUT" if outgoing else "IN",
                txn.status,
                txn.amount_minor,
                txn.fee_minor if outgoing else 0,
                -change if outgoing else change,
                txn.currency,
                safe_text(str(party.get("name", ""))),
                safe_text(txn.description),
                overrides.get(txn.id) or default_category(txn, outgoing),
            ]
        )
    return out.getvalue(), len(rows), truncated


async def process_pending(db: Database, clock: Clock, timezone: str, *, batch: int = 5) -> int:
    """Worker job: build pending exports (and drop the content of expired ones)."""
    done = 0
    async with db.transaction() as session:
        await session.execute(
            update(ExportJob)
            .where(ExportJob.expires_at <= clock.now(), ExportJob.status != "EXPIRED")
            .values(status="EXPIRED", content=None)
        )
    for _ in range(batch):
        async with db.transaction() as session:
            job = await session.scalar(
                select(ExportJob)
                .where(ExportJob.status == "PENDING")
                .order_by(ExportJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                break
            content, count, truncated = await build_csv(session, job, timezone)
            job.content = content
            job.row_count = count
            job.truncated = truncated
            job.status = "READY"
            job.finished_at = clock.now()
            done += 1
    return done
