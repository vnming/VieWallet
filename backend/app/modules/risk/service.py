"""Risk services: assessment (in the money transaction), rule versions, flags, alerts.

Nothing here moves money or changes a transaction; the transactions module decides what a
REVIEW/BLOCK means for the money, and operations runs the maker-checker of rule changes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError, register_titles
from app.core.ids import new_id
from app.modules.audit.public import AuditEntry, record
from app.modules.risk.domain import (
    DEFAULT_CONFIGS,
    RiskContext,
    RiskResult,
    RuleConfig,
    evaluate,
    validate_config,
)
from app.modules.risk.models import FLAGS, AccountRiskFlag, RiskAlert, RiskAssessment, RiskRule

register_titles(
    {
        "RISK_RULE_NOT_FOUND": {"vi": "Không tìm thấy rule rủi ro", "en": "Risk rule not found"},
        "RISK_RULE_INVALID": {
            "vi": "Tham số rule rủi ro không hợp lệ",
            "en": "Invalid risk rule parameters",
        },
        "RISK_ALERT_NOT_FOUND": {
            "vi": "Không tìm thấy cảnh báo rủi ro",
            "en": "Risk alert not found",
        },
        "RISK_ALERT_CLOSED": {"vi": "Cảnh báo đã đóng", "en": "The alert is already closed"},
        "RISK_FLAG_EXISTS": {"vi": "Tài khoản đã có cờ này", "en": "The account already has it"},
        "RISK_FLAG_NOT_FOUND": {"vi": "Tài khoản không có cờ này", "en": "Flag not set"},
    }
)


class RiskRuleNotFoundError(NotFoundError):
    code = "RISK_RULE_NOT_FOUND"


class RiskRuleInvalidError(ValidationFailedError):
    code = "RISK_RULE_INVALID"


class RiskAlertNotFoundError(NotFoundError):
    code = "RISK_ALERT_NOT_FOUND"


class RiskAlertClosedError(ConflictError):
    code = "RISK_ALERT_CLOSED"


class RiskFlagExistsError(ConflictError):
    code = "RISK_FLAG_EXISTS"


class RiskFlagNotFoundError(NotFoundError):
    code = "RISK_FLAG_NOT_FOUND"


# ---------------------------------------------------------------- rules


def _config(row: RiskRule) -> RuleConfig:
    return RuleConfig(
        code=row.code,
        score=row.score,
        block=row.block,
        enabled=row.enabled,
        params=dict(row.params),
        version=row.version,
    )


async def active_configs(session: AsyncSession) -> tuple[RuleConfig, ...]:
    """ACTIVE versions from the table, code defaults for rules not seeded (yet)."""
    rows = (await session.scalars(select(RiskRule).where(RiskRule.status == "ACTIVE"))).all()
    by_code = {r.code: _config(r) for r in rows}
    return tuple(by_code.get(d.code, d) for d in DEFAULT_CONFIGS)


async def assess(
    session: AsyncSession,
    ctx: RiskContext,
    *,
    transaction_id: uuid.UUID | None,
    rules: tuple[RuleConfig, ...] | None = None,
) -> RiskResult:
    configs = await active_configs(session) if rules is None else rules
    result = evaluate(ctx, configs)
    assessment = RiskAssessment(
        id=new_id(),
        transaction_id=transaction_id,
        user_id=ctx.user_id,
        score=result.score,
        decision=result.decision.value,
        matched_rules=[
            {"code": h.code, "score": h.score, "block": h.block, "evidence": h.evidence}
            for h in result.hits
        ],
        rules_version=result.rules_version,
        at=ctx.now,
    )
    session.add(assessment)
    await session.flush()
    return result


async def seed_rules(session: AsyncSession, clock: Clock) -> None:
    """Version 1 of every rule, ACTIVE, from the code defaults. Idempotent."""
    for cfg in DEFAULT_CONFIGS:
        await session.execute(
            insert(RiskRule)
            .values(
                id=new_id(),
                code=cfg.code,
                version=1,
                description="default (security.md §8.2)",
                score=cfg.score,
                block=cfg.block,
                enabled=cfg.enabled,
                params=dict(cfg.params),
                status="ACTIVE",
                created_by=None,
                created_at=clock.now(),
                activated_at=clock.now(),
            )
            .on_conflict_do_nothing(index_elements=["code", "version"])
        )


async def list_rules(session: AsyncSession) -> list[RiskRule]:
    rows = await session.scalars(select(RiskRule).order_by(RiskRule.code, RiskRule.version.desc()))
    return list(rows.all())


@dataclass(frozen=True, slots=True)
class RuleProposal:
    code: str
    score: int
    block: bool
    enabled: bool
    params: dict[str, Any]
    description: str


async def propose_version(
    session: AsyncSession, clock: Clock, proposal: RuleProposal, *, maker_id: uuid.UUID
) -> RiskRule:
    """A PENDING next version; becomes ACTIVE only when a second person approves."""
    try:
        validate_config(
            RuleConfig(
                proposal.code,
                proposal.score,
                proposal.block,
                proposal.enabled,
                proposal.params,
            )
        )
    except ValueError as exc:
        raise RiskRuleInvalidError(str(exc)) from exc
    latest = await session.scalar(
        select(func.max(RiskRule.version)).where(RiskRule.code == proposal.code)
    )
    row = RiskRule(
        id=new_id(),
        code=proposal.code,
        version=int(latest or 0) + 1,
        description=proposal.description[:200],
        score=proposal.score,
        block=proposal.block,
        enabled=proposal.enabled,
        params=proposal.params,
        status="PENDING",
        created_by=maker_id,
        created_at=clock.now(),
        activated_at=None,
    )
    session.add(row)
    await session.flush()
    return row


async def _pending(session: AsyncSession, rule_id: uuid.UUID) -> RiskRule:
    row = await session.scalar(select(RiskRule).where(RiskRule.id == rule_id).with_for_update())
    if row is None:
        raise RiskRuleNotFoundError()
    return row


async def activate_version(
    session: AsyncSession, clock: Clock, rule_id: uuid.UUID, *, checker_id: uuid.UUID
) -> str | None:
    """Approval executor. Returns a failure code instead of raising (the caller commits)."""
    row = await _pending(session, rule_id)
    if row.status != "PENDING":
        return "RULE_NOT_PENDING"
    await session.execute(
        update(RiskRule)
        .where(RiskRule.code == row.code, RiskRule.status == "ACTIVE")
        .values(status="RETIRED")
    )
    await session.flush()
    row.status = "ACTIVE"
    row.activated_at = clock.now()
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="risk.rule_activated",
            actor_type="STAFF",
            actor_id=checker_id,
            target_type="risk_rule",
            target_id=str(row.id),
            after={"code": row.code, "version": row.version, "score": row.score},
        ),
    )
    return None


async def reject_version(session: AsyncSession, rule_id: uuid.UUID) -> None:
    row = await _pending(session, rule_id)
    if row.status == "PENDING":
        row.status = "REJECTED"
        await session.flush()


# ---------------------------------------------------------------- flags


async def flags_of(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> frozenset[str]:
    rows = await session.scalars(
        select(AccountRiskFlag.flag).where(
            AccountRiskFlag.user_id == user_id,
            AccountRiskFlag.removed_at.is_(None),
            or_(AccountRiskFlag.expires_at.is_(None), AccountRiskFlag.expires_at > now),
        )
    )
    return frozenset(rows.all())


async def list_flags(session: AsyncSession, user_id: uuid.UUID) -> list[AccountRiskFlag]:
    rows = await session.scalars(
        select(AccountRiskFlag)
        .where(AccountRiskFlag.user_id == user_id)
        .order_by(AccountRiskFlag.created_at.desc())
    )
    return list(rows.all())


async def set_flag(
    session: AsyncSession,
    clock: Clock,
    *,
    user_id: uuid.UUID,
    flag: str,
    reason: str,
    actor_id: uuid.UUID | None,
    expires_at: datetime | None = None,
) -> AccountRiskFlag:
    if flag not in FLAGS:
        raise RiskRuleInvalidError(f"flag must be one of {FLAGS}")
    if flag in await flags_of(session, user_id, clock.now()):
        raise RiskFlagExistsError()
    row = AccountRiskFlag(
        id=new_id(),
        user_id=user_id,
        flag=flag,
        reason=reason[:300],
        set_by=actor_id,
        created_at=clock.now(),
        expires_at=expires_at,
        removed_at=None,
        removed_by=None,
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="risk.flag_set",
            actor_type="STAFF" if actor_id else "SYSTEM",
            actor_id=actor_id,
            target_type="user",
            target_id=str(user_id),
            reason=reason,
            details={"flag": flag},
        ),
    )
    return row


async def remove_flag(
    session: AsyncSession,
    clock: Clock,
    *,
    user_id: uuid.UUID,
    flag: str,
    actor_id: uuid.UUID,
    reason: str,
) -> None:
    result = await session.execute(
        update(AccountRiskFlag)
        .where(
            AccountRiskFlag.user_id == user_id,
            AccountRiskFlag.flag == flag,
            AccountRiskFlag.removed_at.is_(None),
        )
        .values(removed_at=clock.now(), removed_by=actor_id)
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise RiskFlagNotFoundError()
    await record(
        session,
        clock,
        AuditEntry(
            action="risk.flag_removed",
            actor_type="STAFF",
            actor_id=actor_id,
            target_type="user",
            target_id=str(user_id),
            reason=reason,
            details={"flag": flag},
        ),
    )


# ---------------------------------------------------------------- alerts


@dataclass(frozen=True, slots=True)
class NewAlert:
    user_id: uuid.UUID
    type: str
    severity: str
    dedup_key: str
    detail: dict[str, Any]
    transaction_id: uuid.UUID | None = None


async def raise_alert(session: AsyncSession, clock: Clock, alert: NewAlert) -> bool:
    """Insert once per dedup key. Returns True when a new alert was created."""
    inserted = await session.scalar(
        insert(RiskAlert)
        .values(
            id=new_id(),
            user_id=alert.user_id,
            transaction_id=alert.transaction_id,
            assessment_id=None,
            type=alert.type,
            severity=alert.severity,
            detail=alert.detail,
            dedup_key=alert.dedup_key[:120],
            status="OPEN",
            assigned_to=None,
            resolution_note=None,
            created_at=clock.now(),
            resolved_at=None,
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"])
        .returning(RiskAlert.id)
    )
    return inserted is not None


async def list_alerts(
    session: AsyncSession, *, status: str | None, page: int, page_size: int
) -> tuple[list[RiskAlert], int]:
    conditions: list[ColumnElement[bool]] = [RiskAlert.status == status] if status else []
    total = await session.scalar(select(func.count()).select_from(RiskAlert).where(*conditions))
    rows = await session.scalars(
        select(RiskAlert)
        .where(*conditions)
        .order_by(RiskAlert.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(rows.all()), int(total or 0)


async def resolve_alert(
    session: AsyncSession,
    clock: Clock,
    alert_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    outcome: str,
    note: str,
) -> RiskAlert:
    row = await session.scalar(select(RiskAlert).where(RiskAlert.id == alert_id).with_for_update())
    if row is None:
        raise RiskAlertNotFoundError()
    if row.status in ("RESOLVED", "FALSE_POSITIVE"):
        raise RiskAlertClosedError()
    row.status = outcome
    row.assigned_to = actor_id
    row.resolution_note = note[:500]
    row.resolved_at = clock.now()
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="risk.alert_resolved",
            actor_type="STAFF",
            actor_id=actor_id,
            target_type="risk_alert",
            target_id=str(row.id),
            reason=note,
            details={"outcome": outcome, "type": row.type},
        ),
    )
    return row
