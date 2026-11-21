"""ORM models owned by the shared kernel (reference data)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class CurrencyRecord(Base):
    __tablename__ = "currencies"
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Z]{3}$'", name="code_format"),
        CheckConstraint("exponent BETWEEN 0 AND 4", name="exponent_range"),
    )

    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    exponent: Mapped[int] = mapped_column(SmallInteger)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
