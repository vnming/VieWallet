"""Risk assessment log (append-only)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (
        CheckConstraint("decision IN ('ALLOW', 'REVIEW', 'BLOCK')", name="decision_valid"),
        CheckConstraint("score BETWEEN 0 AND 100", name="score_range"),
        Index("ix_risk_assessments_transaction", "transaction_id"),
        Index("ix_risk_assessments_user_at", "user_id", "at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    transaction_id: Mapped[uuid.UUID | None]
    user_id: Mapped[uuid.UUID]
    score: Mapped[int] = mapped_column(SmallInteger)
    decision: Mapped[str] = mapped_column(String(10))
    matched_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    rules_version: Mapped[str] = mapped_column(String(30))
    at: Mapped[datetime]
