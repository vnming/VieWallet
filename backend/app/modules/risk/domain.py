"""Risk engine (security.md §8): pure rules over a context assembled in the money transaction.

Phase 3 ships the interface and the first rules (velocity, duplicate pattern, credential-change
cooldown, repeated failures). The full catalogue and admin tooling arrive in Phase 6.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

RULES_VERSION = "2026.11-p3"
SCORE_CAP = 100
REVIEW_THRESHOLD = 70
COOLDOWN_AMOUNT_MINOR = 2_000_000
VELOCITY_COUNT_LIMIT = 10
REPEATED_FAILURE_LIMIT = 3


class Decision(StrEnum):
    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Everything the rules need, loaded by the caller while it holds the wallet lock."""

    user_id: uuid.UUID
    txn_type: str
    amount_minor: int
    currency: str
    now: datetime
    counterparty_wallet_id: uuid.UUID | None = None
    # Recent activity of the paying wallet.
    outgoing_count_10m: int = 0
    outgoing_amount_1h_minor: int = 0
    daily_limit_minor: int | None = None
    failed_count_30m: int = 0
    same_amount_same_counterparty_2m: int = 0
    # Security events.
    credentials_changed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RuleHit:
    code: str
    score: int
    block: bool
    evidence: dict[str, str]


@dataclass(frozen=True, slots=True)
class RiskResult:
    score: int
    decision: Decision
    hits: tuple[RuleHit, ...] = field(default_factory=tuple)
    rules_version: str = RULES_VERSION


Rule = Callable[[RiskContext], RuleHit | None]


def velocity_count(ctx: RiskContext) -> RuleHit | None:
    if ctx.outgoing_count_10m + 1 > VELOCITY_COUNT_LIMIT:
        return RuleHit("VELOCITY_COUNT", 40, False, {"count_10m": str(ctx.outgoing_count_10m + 1)})
    return None


def velocity_amount(ctx: RiskContext) -> RuleHit | None:
    if ctx.daily_limit_minor is None or ctx.daily_limit_minor <= 0:
        return None
    total = ctx.outgoing_amount_1h_minor + ctx.amount_minor
    if total * 2 > ctx.daily_limit_minor:  # > 50 % of the daily limit, in integers
        return RuleHit(
            "VELOCITY_AMOUNT",
            30,
            False,
            {"amount_1h": str(total), "daily_limit": str(ctx.daily_limit_minor)},
        )
    return None


def credential_change_cooldown(ctx: RiskContext) -> RuleHit | None:
    changed = ctx.credentials_changed_at
    if changed is None or ctx.now - changed >= timedelta(hours=24):
        return None
    if ctx.amount_minor <= COOLDOWN_AMOUNT_MINOR:
        return None
    return RuleHit(
        "CREDENTIAL_CHANGE_COOLDOWN",
        50,
        ctx.txn_type == "WITHDRAWAL",
        {"changed_at": changed.isoformat()},
    )


def repeated_failures(ctx: RiskContext) -> RuleHit | None:
    if ctx.failed_count_30m >= REPEATED_FAILURE_LIMIT:
        return RuleHit("REPEATED_FAILURES", 30, False, {"failed_30m": str(ctx.failed_count_30m)})
    return None


def duplicate_pattern(ctx: RiskContext) -> RuleHit | None:
    if ctx.counterparty_wallet_id is not None and ctx.same_amount_same_counterparty_2m > 0:
        return RuleHit(
            "DUPLICATE_PATTERN",
            40,
            False,
            {"same_in_2m": str(ctx.same_amount_same_counterparty_2m)},
        )
    return None


DEFAULT_RULES: tuple[Rule, ...] = (
    velocity_count,
    velocity_amount,
    credential_change_cooldown,
    repeated_failures,
    duplicate_pattern,
)


def evaluate(ctx: RiskContext, rules: Sequence[Rule] = DEFAULT_RULES) -> RiskResult:
    hits = tuple(hit for rule in rules if (hit := rule(ctx)) is not None)
    score = min(sum(h.score for h in hits), SCORE_CAP)
    if any(h.block for h in hits):
        decision = Decision.BLOCK
    elif score >= REVIEW_THRESHOLD:
        decision = Decision.REVIEW
    else:
        decision = Decision.ALLOW
    return RiskResult(score, decision, hits)
