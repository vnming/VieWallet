"""Recovery cases: money flows that need a human (financial-processing.md §11).

Created from ``transaction.recovery_needed`` events (amount/status mismatch between provider
and ledger, operations unresolved after 72 hours). Nothing here moves money; Finance resolves
a case with the normal tools (adjustment, reversal) through maker-checker.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Index, String, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import Clock
from app.core.db import Base, Database
from app.core.ids import new_id
from app.core.outbox import HandlerRegistry, OutboxMessage
from app.modules.audit.public import AuditEntry, record

RECOVERY_TOPIC = "transaction.recovery_needed"


class RecoveryCase(Base):
    __tablename__ = "recovery_cases"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN', 'INVESTIGATING', 'RESOLVED')", name="status_valid"),
        Index("uq_recovery_cases_dedup", "transaction_id", "reason", unique=True),
        Index("ix_recovery_cases_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    transaction_id: Mapped[uuid.UUID]
    provider_operation_id: Mapped[uuid.UUID | None]
    reason: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(15), default="OPEN")
    assigned_to: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


async def open_case(db: Database, clock: Clock, message: OutboxMessage) -> None:
    payload = message.payload
    now = clock.now()
    operation = payload.get("operation_id")
    async with db.transaction() as session:
        inserted = await session.scalar(
            insert(RecoveryCase)
            .values(
                id=new_id(),
                transaction_id=uuid.UUID(str(payload["transaction_id"])),
                provider_operation_id=uuid.UUID(str(operation)) if operation else None,
                reason=str(payload["reason"]),
                detail=str(payload.get("detail", ""))[:300],
                status="OPEN",
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["transaction_id", "reason"])
            .returning(RecoveryCase.id)
        )
        if inserted is not None:
            await record(
                session,
                clock,
                AuditEntry(
                    action="operations.recovery_case_opened",
                    actor_type="SYSTEM",
                    target_type="recovery_case",
                    target_id=str(inserted),
                    details={
                        "reason": payload["reason"],
                        "transaction_id": payload["transaction_id"],
                    },
                ),
            )


async def list_cases(
    db: Database, *, status: str | None, page: int, page_size: int
) -> tuple[list[RecoveryCase], int]:
    conditions = [RecoveryCase.status == status] if status else []
    async with db.transaction() as session:
        total = await session.scalar(
            select(func.count()).select_from(RecoveryCase).where(*conditions)
        )
        rows = await session.scalars(
            select(RecoveryCase)
            .where(*conditions)
            .order_by(RecoveryCase.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows.all()), int(total or 0)


def register_recovery_handlers(registry: HandlerRegistry, db: Database, clock: Clock) -> None:
    async def handler(message: OutboxMessage) -> None:
        await open_case(db, clock, message)

    registry.register(RECOVERY_TOPIC, handler)
