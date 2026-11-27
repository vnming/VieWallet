"""Admin console aggregates (Phase 8): dashboard, operations queue, system health, simulator
control panel and provider reconciliation.

These views read several modules, so they live in the HTTP composition layer and use only each
module's public interface. Every number is computed here, on the backend (FR-OPS, design rule:
KPIs come from the backend). Sections the caller has no permission for are left out.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from app.api.deps import StateDep
from app.core.errors import AppError, register_titles
from app.core.jobs import ScheduledJob
from app.core.outbox import OutboxEvent
from app.modules.audit.public import AuditEntry, record
from app.modules.identity.public import (
    IdentityDep,
    MetaDep,
    Principal,
    StaffPrincipal,
    User,
    require_permission,
)
from app.modules.kyc.public import KycSubmission
from app.modules.operations.public import (
    APPROVE_PERMISSION,
    ITEM_STATUSES,
    ApprovalRequest,
    ApprovalStatus,
    ReconciliationItem,
    RecoveryCase,
    open_items,
    run_provider,
    update_item,
)
from app.modules.providers.public import (
    SIMULATOR,
    ProviderError,
    ProviderOperation,
    ProviderServices,
    SimulatorControl,
)
from app.modules.risk.public import RiskAlert
from app.modules.support.public import Ticket
from app.modules.transactions.public import Transaction, TxnStatus, TxnType, review

logger = structlog.get_logger(__name__)

register_titles(
    {
        "SIMULATOR_PANEL_DISABLED": {
            "vi": "Bảng điều khiển simulator đang tắt ở môi trường này",
            "en": "The simulator control panel is disabled in this environment",
        },
        "PROVIDER_UNAVAILABLE": {
            "vi": "Nhà cung cấp thanh toán tạm thời không phản hồi",
            "en": "The payment provider is temporarily unavailable",
        },
    }
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-console"])

BUSINESS_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
STUCK_AFTER = timedelta(minutes=30)
WORKER_STALE_AFTER = timedelta(minutes=2)
PROVIDER_FAILURE_WINDOW = timedelta(minutes=15)
OPEN_RECOVERY = ("OPEN", "INVESTIGATING")
OPEN_TICKETS = ("OPEN",)
KYC_WAITING = ("PENDING", "IN_REVIEW")

CAN_RECONCILE = require_permission("reconciliation:manage:any")
CAN_READ_TRANSACTIONS = require_permission("transaction:read:any")
CAN_MANAGE_SIMULATOR = require_permission("simulator:manage:any")


class SimulatorPanelDisabledError(AppError):
    code = "SIMULATOR_PANEL_DISABLED"
    status = 404


class ProviderUnavailableError(AppError):
    code = "PROVIDER_UNAVAILABLE"
    status = 503


def _today_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(now.astimezone(BUSINESS_TZ).date(), time.min, BUSINESS_TZ)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


async def _count(session: Any, model: Any, *conditions: Any) -> int:
    return int(
        await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
    )


# ---------------------------------------------------------------- dashboard


class TypeVolume(BaseModel):
    type: str
    count: int
    amount: str


class TransactionKpis(BaseModel):
    completed_today: list[TypeVolume]
    failed_today: int
    in_progress: int


class UserKpis(BaseModel):
    customers: int
    new_today: int


class Queues(BaseModel):
    approvals: int | None = None
    risk_holds: int | None = None
    risk_alerts: int | None = None
    kyc_reviews: int | None = None
    tickets: int | None = None
    reconciliation_items: int | None = None
    recovery_cases: int | None = None


class DashboardOut(BaseModel):
    generated_at: datetime
    business_date: date
    transactions: TransactionKpis | None
    users: UserKpis | None
    queues: Queues


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(principal: StaffPrincipal, ctx: IdentityDep) -> DashboardOut:
    perms = principal.permissions
    now = ctx.clock.now()
    start, end = _today_bounds(now)
    queues = Queues()
    transactions: TransactionKpis | None = None
    users: UserKpis | None = None
    async with ctx.db.transaction() as session:
        if perms & {"transaction:read:any", "report:read:any"}:
            rows = await session.execute(
                select(Transaction.type, func.count(), func.sum(Transaction.amount_minor))
                .where(
                    Transaction.status.in_(
                        (
                            TxnStatus.COMPLETED.value,
                            TxnStatus.PARTIALLY_REFUNDED.value,
                            TxnStatus.REFUNDED.value,
                        )
                    ),
                    Transaction.confirmed_at >= start,
                    Transaction.confirmed_at < end,
                )
                .group_by(Transaction.type)
                .order_by(Transaction.type)
            )
            transactions = TransactionKpis(
                completed_today=[
                    TypeVolume(type=t, count=int(c), amount=str(int(a or 0)))
                    for t, c, a in rows.all()
                ],
                failed_today=await _count(
                    session,
                    Transaction,
                    Transaction.status == TxnStatus.FAILED.value,
                    Transaction.updated_at >= start,
                    Transaction.updated_at < end,
                ),
                in_progress=await _count(
                    session,
                    Transaction,
                    Transaction.status.in_((TxnStatus.PENDING.value, TxnStatus.PROCESSING.value)),
                ),
            )
        if "user:read:any" in perms:
            users = UserKpis(
                customers=await _count(session, User, User.user_type == "CUSTOMER"),
                new_today=await _count(
                    session,
                    User,
                    User.user_type == "CUSTOMER",
                    User.created_at >= start,
                    User.created_at < end,
                ),
            )
        approvable = [a.value for a, p in APPROVE_PERMISSION.items() if p in perms]
        if approvable:
            queues.approvals = await _count(
                session,
                ApprovalRequest,
                ApprovalRequest.status == ApprovalStatus.PENDING.value,
                ApprovalRequest.action_type.in_(approvable),
            )
        if "risk_hold:decide:any" in perms:
            queues.risk_holds = len(await review.list_holds(session, limit=500))
        if "risk_alert:manage:any" in perms:
            queues.risk_alerts = await _count(session, RiskAlert, RiskAlert.status == "OPEN")
        if "kyc:review:any" in perms:
            queues.kyc_reviews = await _count(
                session, KycSubmission, KycSubmission.status.in_(KYC_WAITING)
            )
        if "ticket:manage:any" in perms:
            queues.tickets = await _count(session, Ticket, Ticket.status.in_(OPEN_TICKETS))
        if "reconciliation:manage:any" in perms:
            queues.reconciliation_items = await _count(
                session,
                ReconciliationItem,
                ReconciliationItem.status.in_(("OPEN", "INVESTIGATING")),
            )
        if "transaction:read:any" in perms:
            queues.recovery_cases = await _count(
                session, RecoveryCase, RecoveryCase.status.in_(OPEN_RECOVERY)
            )
    return DashboardOut(
        generated_at=now,
        business_date=now.astimezone(BUSINESS_TZ).date(),
        transactions=transactions,
        users=users,
        queues=queues,
    )


# ---------------------------------------------------------------- operations queue


class QueueTransaction(BaseModel):
    reference: str
    type: str
    status: str
    amount: str
    currency: str
    created_at: datetime
    age_minutes: int


class QueueOperation(BaseModel):
    id: str
    transaction_id: uuid.UUID
    operation: str
    status: str
    amount: str
    inquiry_count: int
    submitted_at: datetime | None
    next_inquiry_at: datetime | None


class QueueCase(BaseModel):
    id: uuid.UUID
    transaction_id: uuid.UUID
    reason: str
    detail: str
    status: str
    created_at: datetime


class QueueApproval(BaseModel):
    id: uuid.UUID
    action_type: str
    target_id: str
    maker_reason: str
    created_at: datetime
    expires_at: datetime


class OperationsQueueOut(BaseModel):
    stuck_transactions: list[QueueTransaction]
    unknown_provider_operations: list[QueueOperation]
    recovery_cases: list[QueueCase]
    pending_money_approvals: list[QueueApproval]


@router.get("/operations-queue", response_model=OperationsQueueOut)
async def operations_queue(
    ctx: IdentityDep, _principal: Principal = CAN_READ_TRANSACTIONS
) -> OperationsQueueOut:
    now = ctx.clock.now()
    async with ctx.db.transaction() as session:
        stuck = (
            await session.scalars(
                select(Transaction)
                .where(
                    Transaction.type.in_((TxnType.DEPOSIT.value, TxnType.WITHDRAWAL.value)),
                    Transaction.status.in_((TxnStatus.PENDING.value, TxnStatus.PROCESSING.value)),
                    Transaction.created_at < now - STUCK_AFTER,
                )
                .order_by(Transaction.created_at)
                .limit(100)
            )
        ).all()
        unknown = (
            await session.scalars(
                select(ProviderOperation)
                .where(ProviderOperation.status.in_(("UNKNOWN", "SUBMITTED")))
                .order_by(ProviderOperation.submitted_at)
                .limit(100)
            )
        ).all()
        cases = (
            await session.scalars(
                select(RecoveryCase)
                .where(RecoveryCase.status.in_(OPEN_RECOVERY))
                .order_by(RecoveryCase.created_at)
                .limit(100)
            )
        ).all()
        approvals = (
            await session.scalars(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.status == ApprovalStatus.PENDING.value,
                    ApprovalRequest.action_type.in_(("REFUND", "REVERSAL", "ADJUSTMENT")),
                )
                .order_by(ApprovalRequest.created_at)
                .limit(100)
            )
        ).all()
    return OperationsQueueOut(
        stuck_transactions=[
            QueueTransaction(
                reference=t.reference,
                type=t.type,
                status=t.status,
                amount=str(t.amount_minor),
                currency=t.currency,
                created_at=t.created_at,
                age_minutes=int((now - t.created_at).total_seconds() // 60),
            )
            for t in stuck
        ],
        unknown_provider_operations=[
            QueueOperation(
                id=str(o.id),
                transaction_id=o.transaction_id,
                operation=o.operation,
                status=o.status,
                amount=str(o.amount_minor),
                inquiry_count=o.inquiry_count,
                submitted_at=o.submitted_at,
                next_inquiry_at=o.next_inquiry_at,
            )
            for o in unknown
        ],
        recovery_cases=[
            QueueCase(
                id=c.id,
                transaction_id=c.transaction_id,
                reason=c.reason,
                detail=c.detail,
                status=c.status,
                created_at=c.created_at,
            )
            for c in cases
        ],
        pending_money_approvals=[
            QueueApproval(
                id=a.id,
                action_type=a.action_type,
                target_id=a.target_id,
                maker_reason=a.maker_reason,
                created_at=a.created_at,
                expires_at=a.expires_at,
            )
            for a in approvals
        ],
    )


# ---------------------------------------------------------------- system health

Health = Literal["UP", "DEGRADED", "DOWN", "NOT_CONFIGURED"]


class JobHealth(BaseModel):
    name: str
    last_status: str | None
    last_finished_at: datetime | None
    last_error: str | None


class OutboxHealth(BaseModel):
    pending: int
    failed: int
    oldest_pending_seconds: int | None


class ProviderHealth(BaseModel):
    code: str
    status: Health
    mode: str | None
    unknown_operations: int
    recent_unavailable_failures: int


class ComponentHealth(BaseModel):
    database: Health
    redis: Health
    worker: Health
    push: Health
    ai: Health


class SystemHealthOut(BaseModel):
    checked_at: datetime
    environment: str
    version: str
    components: ComponentHealth
    outbox: OutboxHealth
    provider: ProviderHealth
    jobs: list[JobHealth]


def sim_control_of(request: Request) -> SimulatorControl | None:
    control: SimulatorControl | None = getattr(request.app.state, "sim_control", None)
    return control


SimControlDep = Annotated[SimulatorControl | None, Depends(sim_control_of)]


async def _redis_ok(state: Any) -> bool:
    try:
        async with asyncio.timeout(2):
            await state.redis.ping()
    except Exception:
        return False
    return True


async def _simulator_status(control: SimulatorControl | None) -> tuple[bool | None, str | None]:
    if control is None:
        return None, None
    healthy = await control.health()
    mode: str | None = None
    if healthy:
        try:
            mode = await control.get_mode()
        except ProviderError, Exception:
            mode = None
    return healthy, mode


@router.get("/system-health", response_model=SystemHealthOut)
async def system_health(
    _principal: StaffPrincipal, state: StateDep, control: SimControlDep
) -> SystemHealthOut:
    now = state.clock.now()
    database: Health = "UP"
    try:
        async with asyncio.timeout(2), state.db.transaction() as session:
            await session.execute(text("SELECT 1"))
            jobs = (await session.scalars(select(ScheduledJob).order_by(ScheduledJob.name))).all()
            pending = await _count(session, OutboxEvent, OutboxEvent.status == "PENDING")
            failed = await _count(session, OutboxEvent, OutboxEvent.status == "FAILED")
            oldest = await session.scalar(
                select(func.min(OutboxEvent.created_at)).where(OutboxEvent.status == "PENDING")
            )
            unknown = await _count(
                session, ProviderOperation, ProviderOperation.status == "UNKNOWN"
            )
            unavailable = await _count(
                session,
                Transaction,
                Transaction.failure_code == "PROVIDER_UNAVAILABLE",
                Transaction.updated_at >= now - PROVIDER_FAILURE_WINDOW,
            )
    except Exception as exc:
        logger.warning("system_health_database_failed", error=type(exc).__name__)
        database, jobs, pending, failed, oldest, unknown, unavailable = "DOWN", [], 0, 0, None, 0, 0
    finished = [j.last_finished_at for j in jobs if j.last_finished_at is not None]
    worker: Health = (
        "DOWN"
        if not finished or now - max(finished) > WORKER_STALE_AFTER
        else ("DEGRADED" if any(j.last_status == "FAILED" for j in jobs) else "UP")
    )
    healthy, mode = await _simulator_status(control)
    provider: Health
    if healthy is False or mode == "OUTAGE":
        provider = "DOWN"
    elif mode == "DEGRADED" or unavailable > 0 or unknown > 0:
        provider = "DEGRADED"
    else:
        provider = "UP"
    return SystemHealthOut(
        checked_at=now,
        environment=state.settings.environment.value,
        version=state.settings.app_version,
        components=ComponentHealth(
            database=database,
            redis="UP" if await _redis_ok(state) else "DOWN",
            worker=worker,
            push="NOT_CONFIGURED",  # Phase 9
            ai="NOT_CONFIGURED",  # Phase 11
        ),
        outbox=OutboxHealth(
            pending=pending,
            failed=failed,
            oldest_pending_seconds=int((now - oldest).total_seconds()) if oldest else None,
        ),
        provider=ProviderHealth(
            code=SIMULATOR,
            status=provider,
            mode=mode,
            unknown_operations=unknown,
            recent_unavailable_failures=unavailable,
        ),
        jobs=[
            JobHealth(
                name=j.name,
                last_status=j.last_status,
                last_finished_at=j.last_finished_at,
                last_error=j.last_error,
            )
            for j in jobs
        ],
    )


# ---------------------------------------------------------------- simulator control panel


class SimulatorOut(BaseModel):
    mode: str
    operations: list[dict[str, Any]]


class ModeIn(BaseModel):
    mode: Literal["NORMAL", "DEGRADED", "OUTAGE"]
    reason: str = Field(min_length=5, max_length=300)


class SimActionIn(BaseModel):
    action: Literal["SUCCEED", "FAIL", "EXPIRE", "RETURN", "RESEND_CALLBACK"]
    reason: str = Field(min_length=5, max_length=300)


def _control(control: SimulatorControl | None) -> SimulatorControl:
    if control is None:
        raise SimulatorPanelDisabledError()
    return control


async def _audit_sim(
    ctx: Any, principal: Principal, meta: Any, *, action: str, details: dict[str, Any], reason: str
) -> None:
    async with ctx.db.transaction() as session:
        await record(
            session,
            ctx.clock,
            AuditEntry(
                action=action,
                actor_type="STAFF",
                actor_id=principal.user_id,
                target_type="simulator",
                target_id=SIMULATOR,
                reason=reason,
                details=details,
                ip=meta.ip,
                user_agent=meta.user_agent,
            ),
        )


@router.get("/simulator", response_model=SimulatorOut)
async def simulator_panel(
    control: SimControlDep, _principal: Principal = CAN_MANAGE_SIMULATOR
) -> SimulatorOut:
    panel = _control(control)
    try:
        return SimulatorOut(mode=await panel.get_mode(), operations=await panel.operations())
    except ProviderError as exc:
        raise ProviderUnavailableError() from exc


@router.put("/simulator/mode", response_model=SimulatorOut)
async def set_simulator_mode(
    body: ModeIn,
    control: SimControlDep,
    ctx: IdentityDep,
    meta: MetaDep,
    principal: Principal = CAN_MANAGE_SIMULATOR,
) -> SimulatorOut:
    panel = _control(control)
    try:
        before = await panel.get_mode()
        mode = await panel.set_mode(body.mode)
        operations = await panel.operations()
    except ProviderError as exc:
        raise ProviderUnavailableError() from exc
    await _audit_sim(
        ctx,
        principal,
        meta,
        action="simulator.mode_changed",
        details={"before": before, "after": mode},
        reason=body.reason,
    )
    return SimulatorOut(mode=mode, operations=operations)


@router.post("/simulator/operations/{operation_key}/actions")
async def simulator_action(
    *,
    operation_key: Annotated[str, Field(max_length=64)],
    body: SimActionIn,
    control: SimControlDep,
    ctx: IdentityDep,
    meta: MetaDep,
    principal: Principal = CAN_MANAGE_SIMULATOR,
) -> dict[str, Any]:
    panel = _control(control)
    try:
        result = await panel.act(operation_key, body.action)
    except ProviderError as exc:
        raise ProviderUnavailableError() from exc
    await _audit_sim(
        ctx,
        principal,
        meta,
        action="simulator.operation_action",
        details={"operation_key": operation_key, "action": body.action},
        reason=body.reason,
    )
    return result


# ---------------------------------------------------------------- provider reconciliation


class ProviderRunIn(BaseModel):
    business_date: date


class ReconItemOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    type: str
    invariant: str | None
    subject: str
    expected: str
    actual: str
    status: str
    resolution_note: str | None
    created_at: datetime
    updated_at: datetime | None


class ProviderRunOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    violations: int
    stats: dict[str, Any]
    started_at: datetime
    items: list[ReconItemOut]


class ItemUpdateIn(BaseModel):
    status: Literal["INVESTIGATING", "RESOLVED", "WRITTEN_OFF"]
    note: str = Field(min_length=5, max_length=500)


def _item_out(item: ReconciliationItem) -> ReconItemOut:
    return ReconItemOut(
        id=item.id,
        run_id=item.run_id,
        type=item.type,
        invariant=item.invariant,
        subject=item.subject,
        expected=item.expected,
        actual=item.actual,
        status=item.status,
        resolution_note=item.resolution_note,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def providers_of(request: Request) -> ProviderServices:
    services: ProviderServices = request.app.state.providers
    return services


@router.post("/reconciliation/provider-runs", response_model=ProviderRunOut, status_code=201)
async def run_provider_reconciliation(
    body: ProviderRunIn,
    request: Request,
    ctx: IdentityDep,
    principal: Principal = CAN_RECONCILE,
) -> ProviderRunOut:
    provider = providers_of(request).get(SIMULATOR)
    try:  # external I/O first, outside any DB transaction
        lines = await provider.get_statement(body.business_date)
    except ProviderError as exc:
        raise ProviderUnavailableError() from exc
    async with ctx.db.transaction() as session:
        run, items = await run_provider(
            session,
            ctx.clock,
            business_date=body.business_date,
            lines=lines,
            provider_code=SIMULATOR,
            triggered_by=principal.user_id,
        )
        return ProviderRunOut(
            id=run.id,
            kind=run.kind,
            status=run.status,
            violations=run.violations,
            stats=run.stats,
            started_at=run.started_at,
            items=[_item_out(i) for i in items],
        )


@router.get("/reconciliation/items", response_model=list[ReconItemOut])
async def reconciliation_items(
    ctx: IdentityDep,
    _principal: Principal = CAN_RECONCILE,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ReconItemOut]:
    async with ctx.db.transaction() as session:
        return [_item_out(i) for i in await open_items(session, limit=limit)]


@router.post("/reconciliation/items/{item_id}", response_model=ReconItemOut)
async def update_reconciliation_item(
    item_id: uuid.UUID,
    body: ItemUpdateIn,
    ctx: IdentityDep,
    principal: Principal = CAN_RECONCILE,
) -> ReconItemOut:
    assert body.status in ITEM_STATUSES  # noqa: S101 - Literal above
    async with ctx.db.transaction() as session:
        item = await update_item(
            session,
            ctx.clock,
            item_id,
            status=body.status,
            note=body.note,
            actor_id=principal.user_id,
        )
        return _item_out(item)
