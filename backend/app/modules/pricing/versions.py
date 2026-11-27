"""New versions of fee and limit rules through maker-checker (owner decision, Phase 8).

A maker proposes new values for an existing rule; the row is stored as a DRAFT with the next
version number. Approval retires the ACTIVE version and activates the draft in the same DB
transaction; rejection, cancellation and expiry mark it REJECTED. Old versions are kept, so
every fee ever charged can be traced to the rule version that produced it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError, register_titles
from app.core.ids import new_id
from app.modules.audit.public import AuditEntry, record
from app.modules.pricing.models import FeeRule, LimitRule

register_titles(
    {
        "PRICING_RULE_NOT_FOUND": {
            "vi": "Không tìm thấy quy tắc phí/hạn mức",
            "en": "Fee or limit rule not found",
        },
        "PRICING_RULE_DRAFT_EXISTS": {
            "vi": "Quy tắc này đã có một phiên bản đang chờ duyệt",
            "en": "This rule already has a version waiting for approval",
        },
        "PRICING_RULE_UNCHANGED": {
            "vi": "Phiên bản mới không khác phiên bản đang áp dụng",
            "en": "The new version does not change anything",
        },
    }
)

RuleKind = Literal["FEE", "LIMIT"]
MAX_MINOR = 10**15  # far above any simulated amount; keeps values inside BIGINT


class PricingRuleNotFoundError(NotFoundError):
    code = "PRICING_RULE_NOT_FOUND"


class PricingRuleDraftExistsError(ConflictError):
    code = "PRICING_RULE_DRAFT_EXISTS"


class PricingRuleUnchangedError(ConflictError):
    code = "PRICING_RULE_UNCHANGED"


@dataclass(frozen=True, slots=True)
class FeeValues:
    fixed_minor: int
    bps: int
    min_fee_minor: int
    max_fee_minor: int | None

    def validate(self) -> None:
        problems = []
        if not 0 <= self.fixed_minor <= MAX_MINOR:
            problems.append("fixed_minor")
        if not 0 <= self.bps <= 10_000:  # noqa: PLR2004 - 100 %
            problems.append("bps")
        if not 0 <= self.min_fee_minor <= MAX_MINOR:
            problems.append("min_fee_minor")
        if self.max_fee_minor is not None and not (
            self.min_fee_minor <= self.max_fee_minor <= MAX_MINOR
        ):
            problems.append("max_fee_minor")
        if problems:
            raise ValidationFailedError(f"invalid values: {', '.join(problems)}")


@dataclass(frozen=True, slots=True)
class LimitValues:
    max_amount_minor: int | None
    max_count: int | None
    min_amount_minor: int | None

    def validate(self) -> None:
        problems = [
            name
            for name, value in (
                ("max_amount_minor", self.max_amount_minor),
                ("max_count", self.max_count),
                ("min_amount_minor", self.min_amount_minor),
            )
            if value is not None and not 0 <= value <= MAX_MINOR
        ]
        if (
            self.min_amount_minor is not None
            and self.max_amount_minor is not None
            and self.min_amount_minor > self.max_amount_minor
        ):
            problems.append("min_amount_minor")
        if problems:
            raise ValidationFailedError(f"invalid values: {', '.join(problems)}")


def fee_values(rule: FeeRule) -> dict[str, int | None]:
    return {
        "fixed_minor": rule.fixed_minor,
        "bps": rule.bps,
        "min_fee_minor": rule.min_fee_minor,
        "max_fee_minor": rule.max_fee_minor,
    }


def limit_values(rule: LimitRule) -> dict[str, int | None]:
    return {
        "max_amount_minor": rule.max_amount_minor,
        "max_count": rule.max_count,
        "min_amount_minor": rule.min_amount_minor,
    }


async def list_fee_rules(session: AsyncSession) -> list[FeeRule]:
    rows = await session.scalars(
        select(FeeRule)
        .where(FeeRule.status.in_(("ACTIVE", "DRAFT")))
        .order_by(FeeRule.txn_type, FeeRule.code, FeeRule.version)
    )
    return list(rows.all())


async def list_limit_rules(session: AsyncSession) -> list[LimitRule]:
    rows = await session.scalars(
        select(LimitRule)
        .where(LimitRule.status.in_(("ACTIVE", "DRAFT")))
        .order_by(LimitRule.txn_type, LimitRule.code, LimitRule.version)
    )
    return list(rows.all())


async def _active[R: (FeeRule, LimitRule)](session: AsyncSession, model: type[R], code: str) -> R:
    row = await session.scalar(
        select(model).where(model.code == code, model.status == "ACTIVE").with_for_update()
    )
    if row is None:
        raise PricingRuleNotFoundError()
    return row


async def _ensure_no_draft(
    session: AsyncSession, model: type[FeeRule] | type[LimitRule], code: str
) -> None:
    draft = await session.scalar(
        select(model.id).where(model.code == code, model.status == "DRAFT")
    )
    if draft is not None:
        raise PricingRuleDraftExistsError()


async def _next_version(session: AsyncSession, model: type[FeeRule | LimitRule], code: str) -> int:
    latest = await session.scalar(select(func.max(model.version)).where(model.code == code))
    return int(latest or 0) + 1


async def propose_fee(
    session: AsyncSession, clock: Clock, code: str, values: FeeValues, *, maker_id: uuid.UUID
) -> tuple[FeeRule, FeeRule]:
    """Returns (active, draft)."""
    values.validate()
    active = await _active(session, FeeRule, code)
    await _ensure_no_draft(session, FeeRule, code)
    if fee_values(active) == {
        "fixed_minor": values.fixed_minor,
        "bps": values.bps,
        "min_fee_minor": values.min_fee_minor,
        "max_fee_minor": values.max_fee_minor,
    }:
        raise PricingRuleUnchangedError()
    draft = FeeRule(
        id=new_id(),
        code=active.code,
        txn_type=active.txn_type,
        currency=active.currency,
        scope=active.scope,
        scope_value=active.scope_value,
        fixed_minor=values.fixed_minor,
        bps=values.bps,
        min_fee_minor=values.min_fee_minor,
        max_fee_minor=values.max_fee_minor,
        effective_from=clock.now(),  # replaced by the activation time
        effective_to=None,
        version=await _next_version(session, FeeRule, code),
        status="DRAFT",
        created_by=maker_id,
    )
    session.add(draft)
    await session.flush()
    return active, draft


async def propose_limit(
    session: AsyncSession, clock: Clock, code: str, values: LimitValues, *, maker_id: uuid.UUID
) -> tuple[LimitRule, LimitRule]:
    values.validate()
    active = await _active(session, LimitRule, code)
    await _ensure_no_draft(session, LimitRule, code)
    if limit_values(active) == {
        "max_amount_minor": values.max_amount_minor,
        "max_count": values.max_count,
        "min_amount_minor": values.min_amount_minor,
    }:
        raise PricingRuleUnchangedError()
    draft = LimitRule(
        id=new_id(),
        code=active.code,
        txn_type=active.txn_type,
        direction=active.direction,
        scope=active.scope,
        scope_value=active.scope_value,
        period=active.period,
        currency=active.currency,
        max_amount_minor=values.max_amount_minor,
        max_count=values.max_count,
        min_amount_minor=values.min_amount_minor,
        version=await _next_version(session, LimitRule, code),
        status="DRAFT",
        created_by=maker_id,
    )
    session.add(draft)
    await session.flush()
    _ = clock
    return active, draft


async def _lock_rule(
    session: AsyncSession, kind: RuleKind, rule_id: uuid.UUID
) -> FeeRule | LimitRule | None:
    if kind == "FEE":
        return await session.scalar(select(FeeRule).where(FeeRule.id == rule_id).with_for_update())
    return await session.scalar(select(LimitRule).where(LimitRule.id == rule_id).with_for_update())


async def activate(
    session: AsyncSession,
    clock: Clock,
    kind: RuleKind,
    rule_id: uuid.UUID,
    *,
    checker_id: uuid.UUID,
) -> str | None:
    """Approval executor. Returns a failure code instead of raising (the caller commits)."""
    draft = await _lock_rule(session, kind, rule_id)
    if draft is None or draft.status != "DRAFT":
        return "RULE_NOT_PENDING"
    now = clock.now()
    if isinstance(draft, FeeRule):
        await session.execute(
            update(FeeRule)
            .where(FeeRule.code == draft.code, FeeRule.status == "ACTIVE")
            .values(status="RETIRED", effective_to=now)
        )
        await session.flush()
        draft.effective_from = now
        values = fee_values(draft)
    else:
        await session.execute(
            update(LimitRule)
            .where(LimitRule.code == draft.code, LimitRule.status == "ACTIVE")
            .values(status="RETIRED")
        )
        await session.flush()
        values = limit_values(draft)
    draft.status = "ACTIVE"
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="pricing.rule_activated",
            actor_type="STAFF",
            actor_id=checker_id,
            target_type="fee_rule" if kind == "FEE" else "limit_rule",
            target_id=str(draft.id),
            after={"code": draft.code, "version": draft.version, **values},
        ),
    )
    return None


async def reject(session: AsyncSession, kind: RuleKind, rule_id: uuid.UUID) -> None:
    draft = await _lock_rule(session, kind, rule_id)
    if draft is not None and draft.status == "DRAFT":
        draft.status = "REJECTED"
        await session.flush()
