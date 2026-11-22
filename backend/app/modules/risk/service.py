"""Evaluate and record (the assessment row commits with the money transaction)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.modules.risk.domain import DEFAULT_RULES, RiskContext, RiskResult, Rule, evaluate
from app.modules.risk.models import RiskAssessment


async def assess(
    session: AsyncSession,
    ctx: RiskContext,
    *,
    transaction_id: uuid.UUID | None,
    rules: tuple[Rule, ...] = DEFAULT_RULES,
) -> RiskResult:
    result = evaluate(ctx, rules)
    session.add(
        RiskAssessment(
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
    )
    await session.flush()
    return result
