"""Identity tables (database.md §3.1)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id

USER_TYPES = ("CUSTOMER", "STAFF")
USER_STATUSES = ("PENDING_VERIFICATION", "ACTIVE", "RESTRICTED", "SUSPENDED", "CLOSED")
AUDIENCES = ("WEB", "ADMIN")
SESSION_STATES = ("ACTIVE", "MFA_PENDING")
TOKEN_PURPOSES = ("VERIFY_EMAIL", "RESET_PASSWORD", "RESET_PIN", "CHANGE_EMAIL")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in("user_type", USER_TYPES), name="user_type_valid"),
        CheckConstraint(_in("status", USER_STATUSES), name="status_valid"),
        CheckConstraint("locale IN ('vi', 'en')", name="locale_valid"),
        CheckConstraint("theme IN ('SYSTEM', 'LIGHT', 'DARK')", name="theme_valid"),
        CheckConstraint("phone IS NULL OR phone ~ '^\\+84[0-9]{9}$'", name="phone_format"),
        CheckConstraint("kyc_tier IN (0, 1)", name="kyc_tier_valid"),
        Index("ix_users_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    email_verified_at: Mapped[datetime | None]
    full_name: Mapped[str] = mapped_column(String(120))
    display_name: Mapped[str | None] = mapped_column(String(60))
    user_type: Mapped[str] = mapped_column(String(20))
    # Simulated phone number (E.164, never verified by SMS: there is no real carrier).
    phone: Mapped[str | None] = mapped_column(String(16), unique=True)
    theme: Mapped[str] = mapped_column(String(10), default="SYSTEM")
    privacy_mode: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(30))
    locale: Mapped[str] = mapped_column(String(5), default="vi")
    # KYC tier (0 = unverified, 1 = verified). Set by the KYC flow from Phase 6; until then
    # only the development/simulation CLI ``set_kyc_tier`` changes it.
    kyc_tier: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    last_login_at: Mapped[datetime | None]


class Credential(Base):
    __tablename__ = "credentials"
    __table_args__ = (CheckConstraint("failed_attempts >= 0", name="failed_attempts_non_negative"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app.users.id", ondelete="CASCADE"), primary_key=True
    )
    password_hash: Mapped[str] = mapped_column(Text)
    password_changed_at: Mapped[datetime]
    failed_attempts: Mapped[int] = mapped_column(default=0)
    last_failed_at: Mapped[datetime | None]
    locked_until: Mapped[datetime | None]
    lock_count: Mapped[int] = mapped_column(default=0)


class UserSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(_in("audience", AUDIENCES), name="audience_valid"),
        CheckConstraint(_in("state", SESSION_STATES), name="state_valid"),
        Index("ix_sessions_user_active", "user_id", "revoked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id", ondelete="CASCADE"))
    audience: Mapped[str] = mapped_column(String(10))
    state: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    idle_expires_at: Mapped[datetime]
    absolute_expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    revoke_reason: Mapped[str | None] = mapped_column(String(40))
    mfa_verified_at: Mapped[datetime | None]
    mfa_failed_attempts: Mapped[int] = mapped_column(default=0)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class LoginAttempt(Base):
    """Append-only login history (successes and failures)."""

    __tablename__ = "login_history"
    __table_args__ = (
        CheckConstraint(_in("audience", AUDIENCES), name="audience_valid"),
        Index("ix_login_history_user_at", "user_id", "at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID | None]
    email_hash: Mapped[str] = mapped_column(String(64))
    audience: Mapped[str] = mapped_column(String(10))
    result: Mapped[str] = mapped_column(String(30))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    at: Mapped[datetime]


class VerificationToken(Base):
    """One-time codes and links: e-mail verification, password/PIN reset, e-mail change."""

    __tablename__ = "verification_tokens"
    __table_args__ = (
        CheckConstraint(_in("purpose", TOKEN_PURPOSES), name="purpose_valid"),
        Index("ix_verification_tokens_user_purpose", "user_id", "purpose"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(20))
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    target_email: Mapped[str | None] = mapped_column(CITEXT)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None]


class TransactionPin(Base):
    __tablename__ = "transaction_pins"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app.users.id", ondelete="CASCADE"), primary_key=True
    )
    pin_hash: Mapped[str] = mapped_column(Text)
    failed_attempts: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None]
    lock_count: Mapped[int] = mapped_column(default=0)
    lock_window_started_at: Mapped[datetime | None]
    requires_reset: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_at: Mapped[datetime]
    reset_at: Mapped[datetime | None]


class MfaFactor(Base):
    __tablename__ = "mfa_factors"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app.users.id", ondelete="CASCADE"), primary_key=True
    )
    secret_encrypted: Mapped[str] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None]
    last_used_step: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime]


class MfaRecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app.users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None]
