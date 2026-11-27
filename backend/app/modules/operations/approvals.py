"""Maker-checker workflow core.

A maker proposes an exact action (``payload``, hashed). A different staff member with the
action's approve permission approves it; the action executes in the same DB transaction as
the decision, so an approval is never "approved but not applied". Rejection, cancellation by
the maker and expiry run the action's compensating hook (for example cancelling the pending
adjustment transaction).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError, register_titles
from app.core.idempotency import request_fingerprint
from app.core.ids import new_id
from app.modules.access.public import assign_role, holders_of, revoke_role, roles_for_user
from app.modules.audit.public import AuditEntry, record
from app.modules.operations.models import ActionType, ApprovalRequest, ApprovalStatus
from app.modules.pricing.public import activate_rule_version, reject_rule_version
from app.modules.risk.public import activate_version, reject_version
from app.modules.transactions.public import SYSTEM, Actor, adjustment, reversal, staff_refund
from app.modules.wallet.public import WalletStateConflictError, unfreeze

register_titles(
    {
        "APPROVAL_NOT_FOUND": {
            "vi": "Không tìm thấy yêu cầu phê duyệt",
            "en": "Approval not found",
        },
        "APPROVAL_NOT_PENDING": {
            "vi": "Yêu cầu không còn chờ phê duyệt",
            "en": "The request is no longer pending",
        },
        "APPROVAL_SELF_DECISION": {
            "vi": "Bạn không thể tự duyệt yêu cầu do mình tạo",
            "en": "You cannot decide on a request you made",
        },
        "APPROVAL_EXPIRED": {"vi": "Yêu cầu phê duyệt đã hết hạn", "en": "The request has expired"},
        "APPROVAL_DUPLICATE": {
            "vi": "Đã có yêu cầu đang chờ duyệt cho đối tượng này",
            "en": "A pending request already exists for this target",
        },
        "APPROVAL_SUPER_ADMIN_ONLY": {
            "vi": "Chỉ Super Admin được duyệt thay đổi role Super Admin",
            "en": "Only a Super Admin can decide on Super Admin role changes",
        },
    }
)

APPROVAL_TTL = timedelta(hours=72)

# Permission a checker needs, per action type.
APPROVE_PERMISSION: dict[ActionType, str] = {
    ActionType.ADJUSTMENT: "adjustment:approve:any",
    ActionType.WALLET_UNFREEZE: "wallet:unfreeze:any",
    ActionType.REVERSAL: "reversal:approve:any",
    ActionType.RISK_RULE_CHANGE: "risk_rule:approve:any",
    ActionType.REFUND: "refund:approve:any",
    ActionType.FEE_RULE_CHANGE: "config:approve:any",
    ActionType.LIMIT_RULE_CHANGE: "config:approve:any",
    ActionType.ROLE_CHANGE: "role:assign:any",
}

SUPER_ADMIN = "SUPER_ADMIN"


class ApprovalNotFoundError(NotFoundError):
    code = "APPROVAL_NOT_FOUND"


class ApprovalNotPendingError(ConflictError):
    code = "APPROVAL_NOT_PENDING"


class SelfApprovalError(ForbiddenError):
    code = "APPROVAL_SELF_DECISION"


class ApprovalExpiredError(ConflictError):
    code = "APPROVAL_EXPIRED"


class DuplicateApprovalError(ConflictError):
    code = "APPROVAL_DUPLICATE"


class SuperAdminOnlyError(ForbiddenError):
    code = "APPROVAL_SUPER_ADMIN_ONLY"


@dataclass(frozen=True, slots=True)
class StaffActor:
    id: uuid.UUID
    roles: tuple[str, ...] = ()
    ip: str | None = None
    user_agent: str | None = None


async def _audit(
    session: AsyncSession,
    clock: Clock,
    request: ApprovalRequest,
    action: str,
    actor: StaffActor | None,
    *,
    result: str = "SUCCESS",
    reason: str | None = None,
) -> None:
    await record(
        session,
        clock,
        AuditEntry(
            action=action,
            actor_type="STAFF" if actor else "SYSTEM",
            actor_id=actor.id if actor else None,
            actor_roles=actor.roles if actor else (),
            target_type="approval_request",
            target_id=str(request.id),
            result=result,
            reason=reason,
            details={
                "action_type": request.action_type,
                "target_type": request.target_type,
                "target_id": request.target_id,
                "status": request.status,
                "failure_code": request.failure_code,
            },
            ip=actor.ip if actor else None,
            user_agent=actor.user_agent if actor else None,
        ),
    )


async def propose(
    session: AsyncSession,
    clock: Clock,
    *,
    action_type: ActionType,
    target_type: str,
    target_id: str,
    payload: Mapping[str, Any],
    maker: StaffActor,
    reason: str,
) -> ApprovalRequest:
    pending = await session.scalar(
        select(ApprovalRequest.id).where(
            ApprovalRequest.action_type == action_type.value,
            ApprovalRequest.target_type == target_type,
            ApprovalRequest.target_id == target_id,
            ApprovalRequest.status == ApprovalStatus.PENDING.value,
        )
    )
    if pending is not None:
        raise DuplicateApprovalError()
    now = clock.now()
    request = ApprovalRequest(
        id=new_id(),
        action_type=action_type.value,
        target_type=target_type,
        target_id=target_id,
        payload=dict(payload),
        payload_hash=request_fingerprint(payload),
        maker_id=maker.id,
        maker_reason=reason[:500],
        status=ApprovalStatus.PENDING.value,
        created_at=now,
        expires_at=now + APPROVAL_TTL,
    )
    session.add(request)
    await session.flush()
    await _audit(session, clock, request, "approval.proposed", maker, reason=reason)
    return request


async def _lock(session: AsyncSession, approval_id: uuid.UUID) -> ApprovalRequest:
    request = await session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if request is None:
        raise ApprovalNotFoundError()
    return request


async def get(session: AsyncSession, approval_id: uuid.UUID) -> ApprovalRequest:
    request = await session.get(ApprovalRequest, approval_id)
    if request is None:
        raise ApprovalNotFoundError()
    return request


async def _compensate(
    session: AsyncSession, clock: Clock, request: ApprovalRequest, actor: Actor, reason: str
) -> None:
    """Undo the 'proposed' side effects of an action that will not execute."""
    if request.action_type == ActionType.ADJUSTMENT.value:
        await adjustment.cancel(
            session, clock, uuid.UUID(request.target_id), actor=actor, reason_code=reason
        )
    elif request.action_type == ActionType.RISK_RULE_CHANGE.value:
        await reject_version(session, uuid.UUID(str(request.payload["rule_id"])))
    elif request.action_type == ActionType.REVERSAL.value:
        await reversal.cancel(
            session,
            clock,
            uuid.UUID(str(request.payload["reversal_transaction_id"])),
            actor=actor,
            reason_code=reason,
        )
    elif request.action_type == ActionType.REFUND.value:
        await staff_refund.cancel(
            session, clock, uuid.UUID(request.target_id), actor=actor, reason_code=reason
        )
    elif request.action_type == ActionType.FEE_RULE_CHANGE.value:
        await reject_rule_version(session, "FEE", uuid.UUID(str(request.payload["rule_id"])))
    elif request.action_type == ActionType.LIMIT_RULE_CHANGE.value:
        await reject_rule_version(session, "LIMIT", uuid.UUID(str(request.payload["rule_id"])))


@dataclass(frozen=True, slots=True)
class Decided:
    request: ApprovalRequest
    error: AppError | None = None


async def _expire_if_due(session: AsyncSession, clock: Clock, request: ApprovalRequest) -> bool:
    if request.expires_at > clock.now():
        return False
    request.status = ApprovalStatus.EXPIRED.value
    request.decided_at = clock.now()
    await _compensate(session, clock, request, SYSTEM, "APPROVAL_EXPIRED")
    await _audit(session, clock, request, "approval.expired", None)
    return True


async def _execute(
    session: AsyncSession, clock: Clock, request: ApprovalRequest, checker: StaffActor
) -> str | None:
    """Run the approved action; returns a business failure code instead of raising."""
    failure: str | None = None
    if request.action_type == ActionType.ADJUSTMENT.value:
        outcome = await adjustment.execute(
            session,
            clock,
            uuid.UUID(request.target_id),
            checker_id=checker.id,
            approval_request_id=request.id,
        )
        failure = outcome.failure_code
    elif request.action_type == ActionType.REVERSAL.value:
        reversed_outcome = await reversal.execute(
            session,
            clock,
            uuid.UUID(str(request.payload["reversal_transaction_id"])),
            checker_id=checker.id,
            approval_request_id=request.id,
        )
        failure = reversed_outcome.failure_code
    elif request.action_type == ActionType.RISK_RULE_CHANGE.value:
        failure = await activate_version(
            session, clock, uuid.UUID(str(request.payload["rule_id"])), checker_id=checker.id
        )
    elif request.action_type == ActionType.REFUND.value:
        refunded = await staff_refund.execute(
            session,
            clock,
            uuid.UUID(request.target_id),
            checker_id=checker.id,
            approval_request_id=request.id,
        )
        failure = refunded.failure_code
    elif request.action_type in (
        ActionType.FEE_RULE_CHANGE.value,
        ActionType.LIMIT_RULE_CHANGE.value,
    ):
        failure = await activate_rule_version(
            session,
            clock,
            "FEE" if request.action_type == ActionType.FEE_RULE_CHANGE.value else "LIMIT",
            uuid.UUID(str(request.payload["rule_id"])),
            checker_id=checker.id,
        )
    elif request.action_type == ActionType.ROLE_CHANGE.value:
        failure = await _execute_role_change(session, clock, request, checker)
    elif request.action_type == ActionType.WALLET_UNFREEZE.value:
        try:
            async with session.begin_nested():
                await unfreeze(
                    session,
                    clock,
                    uuid.UUID(request.target_id),
                    by_owner=False,
                    actor_id=checker.id,
                    reason=request.maker_reason,
                    approval_request_id=request.id,
                    ip=checker.ip,
                    user_agent=checker.user_agent,
                )
        except WalletStateConflictError:
            failure = "WALLET_NOT_FROZEN"
    else:  # pragma: no cover - guarded by the CHECK constraint
        raise ValueError(request.action_type)
    return failure


async def approve(
    session: AsyncSession,
    clock: Clock,
    approval_id: uuid.UUID,
    *,
    checker: StaffActor,
    note: str | None,
) -> Decided:
    """Approve and execute. Returns ``Decided.error`` for outcomes the caller must commit
    before raising (expiry); raises for requests it does not change."""
    request = await _lock(session, approval_id)
    if request.status != ApprovalStatus.PENDING.value:
        raise ApprovalNotPendingError()
    if request.maker_id == checker.id:
        await _audit(session, clock, request, "approval.self_decision", checker, result="DENIED")
        return Decided(request, SelfApprovalError())
    if (refused := await _role_change_refusal(session, request, checker)) is not None:
        await _audit(session, clock, request, "approval.refused", checker, result="DENIED")
        return Decided(request, refused)
    if await _expire_if_due(session, clock, request):
        return Decided(request, ApprovalExpiredError())
    if request.payload_hash != request_fingerprint(request.payload):
        raise RuntimeError("approval payload does not match its hash")

    now = clock.now()
    request.status = ApprovalStatus.APPROVED.value
    request.checker_id = checker.id
    request.checker_note = (note or "")[:500] or None
    request.decided_at = now
    await session.flush()  # the DB CHECK maker <> checker applies here as well

    failure = await _execute(session, clock, request, checker)

    request.status = (ApprovalStatus.FAILED if failure else ApprovalStatus.EXECUTED).value
    request.failure_code = failure
    request.executed_at = clock.now()
    await session.flush()
    await _audit(
        session,
        clock,
        request,
        "approval.executed" if failure is None else "approval.execution_failed",
        checker,
        result="SUCCESS" if failure is None else "FAILED",
        reason=note,
    )
    return Decided(request)


async def _role_change_refusal(
    session: AsyncSession, request: ApprovalRequest, checker: StaffActor
) -> AppError | None:
    """Nobody decides on their own roles; only a Super Admin decides on Super Admin."""
    if request.action_type != ActionType.ROLE_CHANGE.value:
        return None
    if str(checker.id) == str(request.payload["user_id"]):
        return SelfApprovalError("you cannot decide on a change to your own roles")
    if request.payload["role"] == SUPER_ADMIN and SUPER_ADMIN not in await roles_for_user(
        session, checker.id
    ):
        return SuperAdminOnlyError()
    return None


async def _execute_role_change(
    session: AsyncSession, clock: Clock, request: ApprovalRequest, checker: StaffActor
) -> str | None:
    user_id = uuid.UUID(str(request.payload["user_id"]))
    role = str(request.payload["role"])
    granting = request.payload["op"] == "GRANT"
    held = await roles_for_user(session, user_id)
    if granting and role in held:
        return "ROLE_ALREADY_HELD"
    if not granting and role not in held:
        return "ROLE_NOT_HELD"
    if not granting and role == SUPER_ADMIN:
        holders = await holders_of(session, frozenset({SUPER_ADMIN}))
        if set(holders) == {user_id}:
            return "LAST_SUPER_ADMIN"
    if granting:
        await assign_role(session, clock, user_id=user_id, role_code=role, granted_by=checker.id)
    else:
        await revoke_role(session, user_id=user_id, role_code=role)
    await record(
        session,
        clock,
        AuditEntry(
            action="access.role_granted" if granting else "access.role_revoked",
            actor_type="STAFF",
            actor_id=checker.id,
            actor_roles=checker.roles,
            target_type="user",
            target_id=str(user_id),
            before={"roles": sorted(held)},
            after={"roles": sorted(await roles_for_user(session, user_id))},
            details={"approval_request_id": str(request.id), "maker_id": str(request.maker_id)},
            ip=checker.ip,
            user_agent=checker.user_agent,
        ),
    )
    return None


async def reject(
    session: AsyncSession, clock: Clock, approval_id: uuid.UUID, *, checker: StaffActor, note: str
) -> Decided:
    request = await _lock(session, approval_id)
    if request.status != ApprovalStatus.PENDING.value:
        raise ApprovalNotPendingError()
    if request.maker_id == checker.id:
        return Decided(request, SelfApprovalError("use cancel for your own request"))
    if (refused := await _role_change_refusal(session, request, checker)) is not None:
        return Decided(request, refused)
    if await _expire_if_due(session, clock, request):
        return Decided(request, ApprovalExpiredError())
    request.status = ApprovalStatus.REJECTED.value
    request.checker_id = checker.id
    request.checker_note = note[:500]
    request.decided_at = clock.now()
    await session.flush()
    await _compensate(session, clock, request, Actor("STAFF", checker.id), "APPROVAL_REJECTED")
    await _audit(session, clock, request, "approval.rejected", checker, reason=note)
    return Decided(request)


async def cancel(
    session: AsyncSession, clock: Clock, approval_id: uuid.UUID, *, maker: StaffActor
) -> ApprovalRequest:
    request = await _lock(session, approval_id)
    if request.status != ApprovalStatus.PENDING.value:
        raise ApprovalNotPendingError()
    if request.maker_id != maker.id:
        raise ForbiddenError("only the maker can cancel a request")
    request.status = ApprovalStatus.CANCELLED.value
    request.decided_at = clock.now()
    await session.flush()
    await _compensate(session, clock, request, Actor("STAFF", maker.id), "APPROVAL_CANCELLED")
    await _audit(session, clock, request, "approval.cancelled", maker)
    return request


async def expire_due(session: AsyncSession, clock: Clock, *, batch: int = 100) -> int:
    """Worker job: expire pending requests past their deadline."""
    rows = (
        await session.scalars(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.status == ApprovalStatus.PENDING.value,
                ApprovalRequest.expires_at <= clock.now(),
            )
            .order_by(ApprovalRequest.expires_at)
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for request in rows:
        await _expire_if_due(session, clock, request)
    return len(rows)


@dataclass(frozen=True, slots=True)
class ApprovalPage:
    items: list[ApprovalRequest]
    total: int


async def list_requests(
    session: AsyncSession,
    *,
    action_types: list[str],
    status: ApprovalStatus | None,
    page: int,
    page_size: int,
) -> ApprovalPage:
    conditions: list[ColumnElement[bool]] = [ApprovalRequest.action_type.in_(action_types)]
    if status is not None:
        conditions.append(ApprovalRequest.status == status.value)
    total = await session.scalar(
        select(func.count()).select_from(ApprovalRequest).where(*conditions)
    )
    rows = await session.scalars(
        select(ApprovalRequest)
        .where(*conditions)
        .order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return ApprovalPage(list(rows.all()), int(total or 0))
