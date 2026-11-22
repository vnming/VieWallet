"""Public interface of the risk module (ADR-0001)."""

from app.modules.risk.domain import Decision, RiskContext, RiskResult, RuleHit, evaluate
from app.modules.risk.models import RiskAssessment
from app.modules.risk.service import assess

__all__ = [
    "Decision",
    "RiskAssessment",
    "RiskContext",
    "RiskResult",
    "RuleHit",
    "assess",
    "evaluate",
]
