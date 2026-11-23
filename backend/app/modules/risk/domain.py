"""Risk engine (security.md §8): pure rules over a context assembled in the money transaction.

Every rule reads its knobs (score, thresholds, BLOCK or not, enabled) from a ``RuleConfig``.
Defaults live here; the ``risk_rules`` table holds the active version of each rule, changed
only through maker-checker (Phase 6). Each hit carries evidence, so an analyst (and the AI
later) can explain a decision without re-running anything.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

SCORE_CAP = 100
REVIEW_THRESHOLD = 70


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
    median_outgoing_90d_minor: int | None = None  # None: no history yet
    first_time_counterparty: bool = False
    # The user.
    account_created_at: datetime | None = None
    flags: frozenset[str] = frozenset()
    timezone: str = "Asia/Ho_Chi_Minh"
    # Security events.
    credentials_changed_at: datetime | None = None
    # Withdrawals: when the receiving bank account was added, and the required wait.
    destination_added_at: datetime | None = None
    destination_cooldown: timedelta = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class RuleConfig:
    code: str
    score: int
    block: bool = False
    enabled: bool = True
    params: Mapping[str, Any] = field(default_factory=dict)
    version: int = 1

    def param(self, name: str) -> int:
        return int(self.params[name])


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
    rules_version: str = "code"


RuleFn = Callable[[RiskContext, RuleConfig], dict[str, str] | None]


def _hit(cfg: RuleConfig, evidence: dict[str, str], *, block: bool | None = None) -> RuleHit:
    return RuleHit(cfg.code, cfg.score, cfg.block if block is None else block, evidence)


# ---------------------------------------------------------------- rules (evidence or None)


def velocity_count(ctx: RiskContext, cfg: RuleConfig) -> dict[str, str] | None:
    count = ctx.outgoing_count_10m + 1
    return {"count_10m": str(count)} if count > cfg.param("max_count") else None


def velocity_amount(ctx: RiskContext, cfg: RuleConfig) -> dict[str, str] | None:
    if ctx.daily_limit_minor is None or ctx.daily_limit_minor <= 0:
        return None
    total = ctx.outgoing_amount_1h_minor + ctx.amount_minor
    # total / daily_limit > percent / 100, in integers
    if total * 100 > ctx.daily_limit_minor * cfg.param("percent_of_daily"):
        return {"amount_1h": str(total), "daily_limit": str(ctx.daily_limit_minor)}
    return None


def amount_spike(ctx: RiskContext, cfg: RuleConfig) -> dict[str, str] | None:
    median = ctx.median_outgoing_90d_minor
    if median is None or median <= 0 or ctx.amount_minor <= cfg.param("min_amount"):
        return None
    if ctx.amount_minor > median * cfg.param("multiple"):
        return {"amount": str(ctx.amount_minor), "median_90d": str(median)}
    return None


def new_recipient_large(ctx: RiskContext, cfg: RuleConfig) -> dict[str, str] | None:
    if ctx.first_time_counterparty and ctx.amount_minor > cfg.param("min_amount"):
        return {"amount": str(ctx.amount_minor)}
    return None


def credential_change_cooldown(ctx: RiskContext, cfg: RuleConfig) -> dict[str, str] | None:
    changed = ctx.credentials_changed_at
    if changed is None or ctx.now - changed >= timedelta(hours=cfg.param("hours")):
        return None
    if ctx.amount_minor <= cfg.param("min_amount"):
        return None
    return {"changed_at": changed.isoformat()}


def new_destination_cooldown(ctx: RiskContext, cfg: RuleConfig) -> dict[str, str] | None:
    added = ctx.destination_added_at
    if added is None or ctx.now - added >= ctx.destination_cooldown:
        return None
    return {"added_at": added.isoformat()}


def repeated_failures(ctx: RiskContext, cfg: RuleConfig) -> dict[str, str] | None:
    if ctx.failed_count_30m >= cfg.param("min_failures"):
        return {"failed_30m": str(ctx.failed_count_30m)}
    return None


def duplicate_pattern(ctx: RiskContext, cfg: RuleConfig) -> dict[str, str] | None:
    if ctx.counterparty_wallet_id is not None and ctx.same_amount_same_counterparty_2m > 0:
        return {"same_in_2m": str(ctx.same_amount_same_counterparty_2m)}
    return None


def account_flag_watchlist(ctx: RiskContext, cfg: RuleConfig) -> dict[str, str] | None:
    return {"flag": "WATCHLIST"} if "WATCHLIST" in ctx.flags else None


def new_account_large(ctx: RiskContext, cfg: RuleConfig) -> dict[str, str] | None:
    created = ctx.account_created_at
    if created is None or ctx.amount_minor <= cfg.param("min_amount"):
        return None
    if ctx.now - created < timedelta(days=cfg.param("days")):
        return {"account_created_at": created.isoformat(), "amount": str(ctx.amount_minor)}
    return None


def night_large(ctx: RiskContext, cfg: RuleConfig) -> dict[str, str] | None:
    hour = ctx.now.astimezone(ZoneInfo(ctx.timezone)).hour
    if cfg.param("from_hour") <= hour < cfg.param("to_hour") and ctx.amount_minor > cfg.param(
        "min_amount"
    ):
        return {"local_hour": str(hour), "amount": str(ctx.amount_minor)}
    return None


RULE_FUNCTIONS: dict[str, RuleFn] = {
    "VELOCITY_COUNT": velocity_count,
    "VELOCITY_AMOUNT": velocity_amount,
    "AMOUNT_SPIKE": amount_spike,
    "NEW_RECIPIENT_LARGE": new_recipient_large,
    "CREDENTIAL_CHANGE_COOLDOWN": credential_change_cooldown,
    "NEW_DESTINATION_COOLDOWN": new_destination_cooldown,
    "REPEATED_FAILURES": repeated_failures,
    "DUPLICATE_PATTERN": duplicate_pattern,
    "ACCOUNT_FLAG_WATCHLIST": account_flag_watchlist,
    "NEW_ACCOUNT_LARGE": new_account_large,
    "NIGHT_LARGE": night_large,
}

# security.md §8.2. The withdrawal BLOCK of the credential cooldown is a fixed product rule.
DEFAULT_CONFIGS: tuple[RuleConfig, ...] = (
    RuleConfig("VELOCITY_COUNT", 40, params={"max_count": 10}),
    RuleConfig("VELOCITY_AMOUNT", 30, params={"percent_of_daily": 50}),
    RuleConfig("AMOUNT_SPIKE", 30, params={"multiple": 5, "min_amount": 2_000_000}),
    RuleConfig("NEW_RECIPIENT_LARGE", 30, params={"min_amount": 5_000_000}),
    RuleConfig("CREDENTIAL_CHANGE_COOLDOWN", 50, params={"hours": 24, "min_amount": 2_000_000}),
    RuleConfig("NEW_DESTINATION_COOLDOWN", 0, block=True),
    RuleConfig("REPEATED_FAILURES", 30, params={"min_failures": 3}),
    RuleConfig("DUPLICATE_PATTERN", 40),
    RuleConfig("ACCOUNT_FLAG_WATCHLIST", 50),
    RuleConfig("NEW_ACCOUNT_LARGE", 30, params={"days": 7, "min_amount": 5_000_000}),
    RuleConfig("NIGHT_LARGE", 20, params={"from_hour": 0, "to_hour": 5, "min_amount": 10_000_000}),
)
DEFAULT_RULES = DEFAULT_CONFIGS  # name kept for callers of the Phase 3 interface


def validate_config(cfg: RuleConfig) -> None:
    """Reject proposals the engine could not run (unknown rule, missing/negative knobs)."""
    if cfg.code not in RULE_FUNCTIONS:
        raise ValueError(f"unknown rule {cfg.code}")
    if not 0 <= cfg.score <= SCORE_CAP:
        raise ValueError("score must be between 0 and 100")
    default = next(c for c in DEFAULT_CONFIGS if c.code == cfg.code)
    missing = set(default.params) - set(cfg.params)
    extra = set(cfg.params) - set(default.params)
    if missing or extra:
        raise ValueError(f"params must be exactly {sorted(default.params)}")
    for name, value in cfg.params.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")


def rules_fingerprint(configs: Sequence[RuleConfig]) -> str:
    """Short, stable id of the rule set used for one assessment (stored with it)."""
    text = ",".join(sorted(f"{c.code}@{c.version}:{int(c.enabled)}" for c in configs))
    return "rs-" + hashlib.sha256(text.encode()).hexdigest()[:12]


def evaluate(ctx: RiskContext, rules: Sequence[RuleConfig] = DEFAULT_CONFIGS) -> RiskResult:
    hits: list[RuleHit] = []
    for cfg in rules:
        if not cfg.enabled:
            continue
        evidence = RULE_FUNCTIONS[cfg.code](ctx, cfg)
        if evidence is None:
            continue
        block = cfg.block
        if cfg.code == "CREDENTIAL_CHANGE_COOLDOWN" and ctx.txn_type == "WITHDRAWAL":
            block = True
        hits.append(_hit(cfg, evidence, block=block))
    score = min(sum(h.score for h in hits), SCORE_CAP)
    if any(h.block for h in hits):
        decision = Decision.BLOCK
    elif score >= REVIEW_THRESHOLD:
        decision = Decision.REVIEW
    else:
        decision = Decision.ALLOW
    return RiskResult(score, decision, tuple(hits), rules_fingerprint(rules))
