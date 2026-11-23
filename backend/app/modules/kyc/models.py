"""KYC tables (database.md §3.2). Simulated identity checks: fictional data only."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id
from app.modules.identity.public import User

# Tables referenced by foreign keys must be registered in this process's metadata too.
_FOREIGN_KEY_TARGETS = (User,)


class ProfileStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class SubmissionStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DocumentType(StrEnum):
    NATIONAL_ID = "NATIONAL_ID"
    PASSPORT = "PASSPORT"
    DRIVER_LICENSE = "DRIVER_LICENSE"


class DocumentKind(StrEnum):
    FRONT = "FRONT"
    BACK = "BACK"
    SELFIE = "SELFIE"


class RejectReason(StrEnum):
    BLURRY_IMAGE = "BLURRY_IMAGE"
    MISMATCH = "MISMATCH"
    EXPIRED_DOCUMENT = "EXPIRED_DOCUMENT"
    SUSPECTED_FRAUD = "SUSPECTED_FRAUD"
    OTHER = "OTHER"


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class KycProfile(Base):
    __tablename__ = "kyc_profiles"
    __table_args__ = (
        CheckConstraint(_in("status", [s.value for s in ProfileStatus]), name="status_valid"),
        CheckConstraint("tier IN (0, 1)", name="tier_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"), unique=True)
    status: Mapped[str] = mapped_column(String(15))
    tier: Mapped[int] = mapped_column(SmallInteger, default=0)
    approved_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    current_submission_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class KycSubmission(Base):
    __tablename__ = "kyc_submissions"
    __table_args__ = (
        CheckConstraint(_in("status", [s.value for s in SubmissionStatus]), name="status_valid"),
        CheckConstraint(
            _in("document_type", [t.value for t in DocumentType]), name="document_type_valid"
        ),
        Index("ix_kyc_submissions_queue", "status", "submitted_at"),
        Index("ix_kyc_submissions_user", "user_id", "created_at"),
        Index("ix_kyc_submissions_document_hash", "document_number_hash"),
        # At most one open submission per person.
        Index(
            "uq_kyc_submissions_open",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('DRAFT', 'PENDING', 'IN_REVIEW')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.kyc_profiles.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"))
    full_name: Mapped[str] = mapped_column(String(120))
    date_of_birth: Mapped[date] = mapped_column(Date)
    document_type: Mapped[str] = mapped_column(String(20))
    document_number_enc: Mapped[str] = mapped_column(String(200))
    document_number_masked: Mapped[str] = mapped_column(String(20))
    document_number_hash: Mapped[str] = mapped_column(String(64))
    address: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(15))
    submitted_at: Mapped[datetime | None]
    claimed_by: Mapped[uuid.UUID | None]
    claimed_at: Mapped[datetime | None]
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class KycDocument(Base):
    __tablename__ = "kyc_documents"
    __table_args__ = (
        UniqueConstraint("submission_id", "kind"),
        CheckConstraint(_in("kind", [k.value for k in DocumentKind]), name="kind_valid"),
        CheckConstraint(
            "content_type IN ('image/jpeg', 'image/png', 'application/pdf')",
            name="content_type_valid",
        ),
        CheckConstraint("size_bytes > 0 AND size_bytes <= 5242880", name="size_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    submission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.kyc_submissions.id"))
    kind: Mapped[str] = mapped_column(String(10))
    storage_key: Mapped[str] = mapped_column(String(80), unique=True)
    content_type: Mapped[str] = mapped_column(String(30))
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]


class KycReview(Base):
    """One decision per row (append-only: trigger + grants)."""

    __tablename__ = "kyc_reviews"
    __table_args__ = (
        CheckConstraint("decision IN ('APPROVE', 'REJECT')", name="decision_valid"),
        CheckConstraint(
            f"reason_code IS NULL OR {_in('reason_code', [r.value for r in RejectReason])}",
            name="reason_valid",
        ),
        CheckConstraint("decision = 'APPROVE' OR reason_code IS NOT NULL", name="reject_reason"),
        Index("ix_kyc_reviews_submission", "submission_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    submission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.kyc_submissions.id"))
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"))
    decision: Mapped[str] = mapped_column(String(10))
    reason_code: Mapped[str | None] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(String(500))
    at: Mapped[datetime]
