"""Simulated KYC lifecycle (security.md §9).

Customer: draft (fictional data) -> upload FRONT/BACK/SELFIE -> submit (PENDING).
Reviewer (``kyc:review:any``): claim (IN_REVIEW) -> APPROVE (tier 1 for 2 years) or REJECT with
a reason; the customer may submit again (at most 5 submissions per 30 days).

Every state change is audited and announced on the outbox (``kyc.status_changed``) for the
in-app inbox. Opening a document needs the permission and a short-lived signed link, and is
audited too.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.crypto import DataCipher, hmac_sha256_hex
from app.core.errors import (
    AppError,
    ConflictError,
    FieldError,
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
    register_titles,
)
from app.core.ids import new_id
from app.core.outbox import publish
from app.modules.audit.public import AuditEntry, record
from app.modules.identity.public import User, set_tier_for_user
from app.modules.kyc.documents import CleanFile, DocumentStore
from app.modules.kyc.models import (
    DocumentKind,
    DocumentType,
    KycDocument,
    KycProfile,
    KycReview,
    KycSubmission,
    ProfileStatus,
    RejectReason,
    SubmissionStatus,
)
from app.modules.risk.public import RiskFlagExistsError, set_flag

register_titles(
    {
        "KYC_NOT_FOUND": {"vi": "Không tìm thấy hồ sơ KYC", "en": "KYC submission not found"},
        "KYC_ALREADY_VERIFIED": {
            "vi": "Tài khoản đã được xác minh",
            "en": "The account is already verified",
        },
        "KYC_IN_REVIEW": {
            "vi": "Hồ sơ đang chờ duyệt",
            "en": "A submission is already waiting for review",
        },
        "KYC_INVALID_STATE": {
            "vi": "Trạng thái hồ sơ không cho phép thao tác này",
            "en": "The submission's status does not allow this action",
        },
        "KYC_DOCUMENTS_MISSING": {
            "vi": "Cần đủ ảnh giấy tờ và ảnh chân dung",
            "en": "Document photos and a selfie are required",
        },
        "KYC_TOO_MANY_ATTEMPTS": {
            "vi": "Đã nộp 5 lần trong 30 ngày, vui lòng thử lại sau",
            "en": "5 submissions in 30 days; please try again later",
        },
        "KYC_CLAIMED_BY_OTHER": {
            "vi": "Hồ sơ đang được người khác xử lý",
            "en": "Another reviewer is handling this submission",
        },
        "KYC_LINK_EXPIRED": {
            "vi": "Liên kết xem giấy tờ đã hết hạn",
            "en": "The document link expired",
        },
    }
)

APPROVAL_VALIDITY = timedelta(days=730)
MAX_SUBMISSIONS = 5
SUBMISSION_WINDOW = timedelta(days=30)
MIN_AGE_YEARS = 18
MAX_AGE_YEARS = 120
MIN_NAME_WORDS = 2
OPEN = (
    SubmissionStatus.DRAFT.value,
    SubmissionStatus.PENDING.value,
    SubmissionStatus.IN_REVIEW.value,
)
TOPIC = "kyc.status_changed"
_NUMBER_FORMATS = {
    DocumentType.NATIONAL_ID: re.compile(r"^\d{12}$"),
    DocumentType.DRIVER_LICENSE: re.compile(r"^\d{12}$"),
    DocumentType.PASSPORT: re.compile(r"^[A-Z]\d{7}$"),
}


class KycNotFoundError(NotFoundError):
    code = "KYC_NOT_FOUND"


class KycAlreadyVerifiedError(ConflictError):
    code = "KYC_ALREADY_VERIFIED"


class KycInReviewError(ConflictError):
    code = "KYC_IN_REVIEW"


class KycInvalidStateError(ConflictError):
    code = "KYC_INVALID_STATE"


class KycDocumentsMissingError(ValidationFailedError):
    code = "KYC_DOCUMENTS_MISSING"


class KycTooManyAttemptsError(AppError):
    code = "KYC_TOO_MANY_ATTEMPTS"
    status = 429


class KycClaimedByOtherError(ConflictError):
    code = "KYC_CLAIMED_BY_OTHER"


class KycLinkExpiredError(ForbiddenError):
    code = "KYC_LINK_EXPIRED"


@dataclass(frozen=True, slots=True)
class KycKeys:
    cipher: DataCipher  # document numbers at rest
    lookup_key: bytes  # keyed hash of document numbers (duplicates without plaintext)
    link_key: bytes  # short-lived document links


@dataclass(frozen=True, slots=True)
class SubmissionInput:
    full_name: str
    date_of_birth: date
    document_type: DocumentType
    document_number: str
    address: str


def mask_document_number(number: str) -> str:
    return "•" * max(len(number) - 4, 0) + number[-4:]


def required_kinds(document_type: DocumentType) -> set[DocumentKind]:
    kinds = {DocumentKind.FRONT, DocumentKind.SELFIE}
    if document_type is not DocumentType.PASSPORT:
        kinds.add(DocumentKind.BACK)
    return kinds


def _validate(data: SubmissionInput, today: date) -> None:
    errors = []
    number = data.document_number.strip().upper()
    if not _NUMBER_FORMATS[data.document_type].match(number):
        errors.append(FieldError("document_number", "FORMAT", "does not match the document type"))
    years = (
        today.year
        - data.date_of_birth.year
        - ((today.month, today.day) < (data.date_of_birth.month, data.date_of_birth.day))
    )
    if years < MIN_AGE_YEARS or years > MAX_AGE_YEARS:
        errors.append(FieldError("date_of_birth", "AGE", "must be 18 or older"))
    if len(data.full_name.split()) < MIN_NAME_WORDS:
        errors.append(FieldError("full_name", "TOO_SHORT", "family and given name"))
    if errors:
        raise ValidationFailedError(errors=errors)


async def _event(
    session: AsyncSession,
    clock: Clock,
    user_id: uuid.UUID,
    status: str,
    *,
    reason_code: str | None = None,
) -> None:
    locale = await session.scalar(select(User.locale).where(User.id == user_id))
    await publish(
        session,
        clock,
        topic=TOPIC,
        aggregate_type="kyc_profile",
        aggregate_id=str(user_id),
        payload={
            "user_id": str(user_id),
            "locale": locale or "vi",
            "status": status,
            "reason_code": reason_code,
        },
    )


async def _audit(
    session: AsyncSession,
    clock: Clock,
    action: str,
    submission: KycSubmission,
    *,
    actor_type: str,
    actor_id: uuid.UUID | None,
    reason: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    await record(
        session,
        clock,
        AuditEntry(
            action=action,
            actor_type=actor_type,
            actor_id=actor_id,
            target_type="kyc_submission",
            target_id=str(submission.id),
            reason=reason,
            details={"status": submission.status, **(details or {})},
        ),
    )


# ---------------------------------------------------------------- customer side


async def profile_of(session: AsyncSession, user_id: uuid.UUID) -> KycProfile | None:
    profile: KycProfile | None = await session.scalar(
        select(KycProfile).where(KycProfile.user_id == user_id)
    )
    return profile


async def _ensure_profile(session: AsyncSession, clock: Clock, user_id: uuid.UUID) -> KycProfile:
    profile = await session.scalar(
        select(KycProfile).where(KycProfile.user_id == user_id).with_for_update()
    )
    if profile is not None:
        return profile
    now = clock.now()
    profile = KycProfile(
        id=new_id(),
        user_id=user_id,
        status=ProfileStatus.NOT_STARTED.value,
        tier=0,
        approved_at=None,
        expires_at=None,
        current_submission_id=None,
        created_at=now,
        updated_at=now,
    )
    session.add(profile)
    await session.flush()
    return profile


async def open_submission(session: AsyncSession, user_id: uuid.UUID) -> KycSubmission | None:
    row: KycSubmission | None = await session.scalar(
        select(KycSubmission).where(
            KycSubmission.user_id == user_id, KycSubmission.status.in_(OPEN)
        )
    )
    return row


async def latest_submission(session: AsyncSession, user_id: uuid.UUID) -> KycSubmission | None:
    row: KycSubmission | None = await session.scalar(
        select(KycSubmission)
        .where(KycSubmission.user_id == user_id)
        .order_by(KycSubmission.created_at.desc())
        .limit(1)
    )
    return row


async def save_draft(
    session: AsyncSession,
    clock: Clock,
    keys: KycKeys,
    *,
    user_id: uuid.UUID,
    data: SubmissionInput,
) -> KycSubmission:
    """Create the draft, or update the fields of the open draft."""
    now = clock.now()
    _validate(data, now.date())
    profile = await _ensure_profile(session, clock, user_id)
    if profile.status == ProfileStatus.APPROVED.value:
        raise KycAlreadyVerifiedError()
    current = await open_submission(session, user_id)
    if current is not None and current.status != SubmissionStatus.DRAFT.value:
        raise KycInReviewError()
    number = data.document_number.strip().upper()
    fields = {
        "full_name": " ".join(data.full_name.split())[:120],
        "date_of_birth": data.date_of_birth,
        "document_type": data.document_type.value,
        "document_number_masked": mask_document_number(number),
        "document_number_hash": hmac_sha256_hex(keys.lookup_key, f"kyc-doc:{number}"),
        "address": " ".join(data.address.split())[:200],
        "updated_at": now,
    }
    if current is None:
        submission_id = new_id()
        current = KycSubmission(
            id=submission_id,
            profile_id=profile.id,
            user_id=user_id,
            status=SubmissionStatus.DRAFT.value,
            document_number_enc=keys.cipher.encrypt(number, associated_data=f"kyc:{submission_id}"),
            submitted_at=None,
            claimed_by=None,
            claimed_at=None,
            decided_at=None,
            created_at=now,
            **fields,
        )
        session.add(current)
    else:
        for name, value in fields.items():
            setattr(current, name, value)
        current.document_number_enc = keys.cipher.encrypt(
            number, associated_data=f"kyc:{current.id}"
        )
    await session.flush()
    return current


async def documents_of(session: AsyncSession, submission_id: uuid.UUID) -> list[KycDocument]:
    rows = await session.scalars(
        select(KycDocument)
        .where(KycDocument.submission_id == submission_id)
        .order_by(KycDocument.kind)
    )
    return list(rows.all())


async def attach_document(
    session: AsyncSession,
    clock: Clock,
    store: DocumentStore,
    *,
    user_id: uuid.UUID,
    kind: DocumentKind,
    file: CleanFile,
) -> KycDocument:
    """Store a cleaned file on the open draft (replacing an earlier file of that kind)."""
    submission = await open_submission(session, user_id)
    if submission is None:
        raise KycNotFoundError("save the form first")
    if submission.status != SubmissionStatus.DRAFT.value:
        raise KycInReviewError()
    existing = await session.scalar(
        select(KycDocument).where(
            KycDocument.submission_id == submission.id, KycDocument.kind == kind.value
        )
    )
    key = await store.put(file.content)
    if existing is not None:
        # The old file stays on disk: deleting it before this transaction commits could leave
        # a row without a file if the commit fails. Unreferenced files are only ciphertext.
        existing.storage_key = key
        existing.content_type = file.content_type
        existing.size_bytes = len(file.content)
        existing.sha256 = file.sha256
        existing.created_at = clock.now()
        await session.flush()
        return existing
    document = KycDocument(
        id=new_id(),
        submission_id=submission.id,
        kind=kind.value,
        storage_key=key,
        content_type=file.content_type,
        size_bytes=len(file.content),
        sha256=file.sha256,
        created_at=clock.now(),
    )
    session.add(document)
    await session.flush()
    return document


async def submit(session: AsyncSession, clock: Clock, *, user_id: uuid.UUID) -> KycSubmission:
    submission = await open_submission(session, user_id)
    if submission is None:
        raise KycNotFoundError()
    if submission.status != SubmissionStatus.DRAFT.value:
        raise KycInReviewError()
    have = {DocumentKind(d.kind) for d in await documents_of(session, submission.id)}
    if not required_kinds(DocumentType(submission.document_type)) <= have:
        raise KycDocumentsMissingError()
    now = clock.now()
    recent = await session.scalar(
        select(func.count())
        .select_from(KycSubmission)
        .where(
            KycSubmission.user_id == user_id,
            KycSubmission.submitted_at >= now - SUBMISSION_WINDOW,
        )
    )
    if int(recent or 0) >= MAX_SUBMISSIONS:
        raise KycTooManyAttemptsError()
    submission.status = SubmissionStatus.PENDING.value
    submission.submitted_at = now
    submission.updated_at = now
    profile = await _ensure_profile(session, clock, user_id)
    profile.status = ProfileStatus.PENDING.value
    profile.current_submission_id = submission.id
    profile.updated_at = now
    await session.flush()
    await _flag_duplicates(session, clock, submission)
    await _audit(session, clock, "kyc.submitted", submission, actor_type="USER", actor_id=user_id)
    await _event(session, clock, user_id, ProfileStatus.PENDING.value)
    return submission


async def other_users_with_document(session: AsyncSession, submission: KycSubmission) -> int:
    count = await session.scalar(
        select(func.count(func.distinct(KycSubmission.user_id))).where(
            KycSubmission.document_number_hash == submission.document_number_hash,
            KycSubmission.user_id != submission.user_id,
            KycSubmission.status != SubmissionStatus.DRAFT.value,
        )
    )
    return int(count or 0)


async def _flag_duplicates(session: AsyncSession, clock: Clock, submission: KycSubmission) -> None:
    """The same document number already used by someone else: flag for the reviewer."""
    if await other_users_with_document(session, submission):
        try:
            async with session.begin_nested():
                await set_flag(
                    session,
                    clock,
                    user_id=submission.user_id,
                    flag="DUPLICATE_DOCUMENT",
                    reason="document number already submitted by another account",
                    actor_id=None,
                )
        except RiskFlagExistsError:
            pass


# ---------------------------------------------------------------- reviewer side


async def queue(
    session: AsyncSession, *, status: SubmissionStatus | None, page: int, page_size: int
) -> tuple[list[KycSubmission], int]:
    statuses = (
        [status.value]
        if status
        else [SubmissionStatus.PENDING.value, SubmissionStatus.IN_REVIEW.value]
    )
    condition = KycSubmission.status.in_(statuses)
    total = await session.scalar(select(func.count()).select_from(KycSubmission).where(condition))
    rows = await session.scalars(
        select(KycSubmission)
        .where(condition)
        .order_by(KycSubmission.submitted_at.nulls_last(), KycSubmission.created_at)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(rows.all()), int(total or 0)


async def get_submission(session: AsyncSession, submission_id: uuid.UUID) -> KycSubmission:
    row = await session.get(KycSubmission, submission_id)
    if row is None or row.status == SubmissionStatus.DRAFT.value:
        raise KycNotFoundError()  # drafts are private to the customer
    return row


async def _lock(session: AsyncSession, submission_id: uuid.UUID) -> KycSubmission:
    row = await session.scalar(
        select(KycSubmission)
        .where(KycSubmission.id == submission_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.status == SubmissionStatus.DRAFT.value:
        raise KycNotFoundError()
    return row


async def claim(
    session: AsyncSession, clock: Clock, submission_id: uuid.UUID, *, reviewer_id: uuid.UUID
) -> KycSubmission:
    submission = await _lock(session, submission_id)
    if submission.status == SubmissionStatus.IN_REVIEW.value:
        if submission.claimed_by != reviewer_id:
            raise KycClaimedByOtherError()
        return submission
    if submission.status != SubmissionStatus.PENDING.value:
        raise KycInvalidStateError()
    now = clock.now()
    submission.status = SubmissionStatus.IN_REVIEW.value
    submission.claimed_by = reviewer_id
    submission.claimed_at = now
    submission.updated_at = now
    profile = await session.get(KycProfile, submission.profile_id, with_for_update=True)
    assert profile is not None  # noqa: S101 - FK
    profile.status = ProfileStatus.IN_REVIEW.value
    profile.updated_at = now
    await session.flush()
    await _audit(
        session, clock, "kyc.claimed", submission, actor_type="STAFF", actor_id=reviewer_id
    )
    return submission


async def decide(
    session: AsyncSession,
    clock: Clock,
    submission_id: uuid.UUID,
    *,
    reviewer_id: uuid.UUID,
    approve: bool,
    reason_code: RejectReason | None,
    note: str | None,
) -> KycSubmission:
    submission = await _lock(session, submission_id)
    if submission.status != SubmissionStatus.IN_REVIEW.value:
        raise KycInvalidStateError("claim the submission first")
    if submission.claimed_by != reviewer_id:
        raise KycClaimedByOtherError()
    if not approve and reason_code is None:
        raise ValidationFailedError(errors=[FieldError("reason_code", "REQUIRED", "required")])
    now = clock.now()
    profile = await session.get(KycProfile, submission.profile_id, with_for_update=True)
    assert profile is not None  # noqa: S101 - FK
    session.add(
        KycReview(
            id=new_id(),
            submission_id=submission.id,
            reviewer_id=reviewer_id,
            decision="APPROVE" if approve else "REJECT",
            reason_code=None if approve else reason_code.value if reason_code else None,
            note=(note or "")[:500] or None,
            at=now,
        )
    )
    submission.status = (SubmissionStatus.APPROVED if approve else SubmissionStatus.REJECTED).value
    submission.decided_at = now
    submission.updated_at = now
    profile.updated_at = now
    if approve:
        profile.status = ProfileStatus.APPROVED.value
        profile.tier = 1
        profile.approved_at = now
        profile.expires_at = now + APPROVAL_VALIDITY
        await set_tier_for_user(
            session,
            clock,
            submission.user_id,
            tier=1,
            reason=f"KYC approved ({submission.id})",
            actor_type="STAFF",
            actor_id=reviewer_id,
        )
    else:
        profile.status = ProfileStatus.REJECTED.value
    await session.flush()
    await _audit(
        session,
        clock,
        "kyc.approved" if approve else "kyc.rejected",
        submission,
        actor_type="STAFF",
        actor_id=reviewer_id,
        reason=note,
        details={"reason_code": reason_code.value if reason_code else None},
    )
    await _event(
        session,
        clock,
        submission.user_id,
        profile.status,
        reason_code=reason_code.value if reason_code and not approve else None,
    )
    return submission


async def reviews_of(session: AsyncSession, submission_id: uuid.UUID) -> list[KycReview]:
    rows = await session.scalars(
        select(KycReview).where(KycReview.submission_id == submission_id).order_by(KycReview.at)
    )
    return list(rows.all())


def reveal_document_number(keys: KycKeys, submission: KycSubmission) -> str:
    return keys.cipher.decrypt(
        submission.document_number_enc, associated_data=f"kyc:{submission.id}"
    )


# ---------------------------------------------------------------- document links


def link_signature(keys: KycKeys, document_id: uuid.UUID, viewer_id: uuid.UUID, exp: int) -> str:
    return hmac_sha256_hex(keys.link_key, f"{document_id}:{viewer_id}:{exp}")


def sign_link(
    keys: KycKeys, document_id: uuid.UUID, viewer_id: uuid.UUID, now: datetime, ttl: timedelta
) -> tuple[int, str]:
    exp = int((now + ttl).timestamp())
    return exp, link_signature(keys, document_id, viewer_id, exp)


def check_link(
    keys: KycKeys,
    document_id: uuid.UUID,
    *,
    viewer_id: uuid.UUID,
    exp: int,
    signature: str,
    now: datetime,
) -> None:
    """The link is bound to the staff member it was issued to and expires quickly."""
    expected = link_signature(keys, document_id, viewer_id, exp)
    if not hmac.compare_digest(expected, signature) or exp < int(now.timestamp()):
        raise KycLinkExpiredError()


async def read_document(
    session: AsyncSession,
    clock: Clock,
    store: DocumentStore,
    document_id: uuid.UUID,
    *,
    viewer_id: uuid.UUID,
) -> tuple[KycDocument, bytes]:
    document = await session.get(KycDocument, document_id)
    if document is None:
        raise KycNotFoundError()
    submission = await get_submission(session, document.submission_id)
    data = await store.get(document.storage_key)
    if hashlib.sha256(data).hexdigest() != document.sha256:
        raise KycNotFoundError("document integrity check failed")
    await _audit(
        session,
        clock,
        "kyc.document_viewed",
        submission,
        actor_type="STAFF",
        actor_id=viewer_id,
        details={"document_id": str(document.id), "kind": document.kind},
    )
    return document, data


# ---------------------------------------------------------------- jobs and the dev CLI


async def expire_approvals(session: AsyncSession, clock: Clock, *, batch: int = 100) -> int:
    """Worker job: approvals past their validity drop back to tier 0."""
    rows = (
        await session.scalars(
            select(KycProfile)
            .where(
                KycProfile.status == ProfileStatus.APPROVED.value,
                KycProfile.expires_at <= clock.now(),
            )
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for profile in rows:
        profile.status = ProfileStatus.EXPIRED.value
        profile.tier = 0
        profile.updated_at = clock.now()
        await set_tier_for_user(
            session, clock, profile.user_id, tier=0, reason="KYC approval expired"
        )
        await _event(session, clock, profile.user_id, ProfileStatus.EXPIRED.value)
    return len(rows)


async def record_simulated_tier(
    session: AsyncSession, clock: Clock, user_id: uuid.UUID, *, tier: int
) -> None:
    """Keep the profile in step with the development CLI ``set_kyc_tier``."""
    profile = await _ensure_profile(session, clock, user_id)
    now = clock.now()
    profile.tier = tier
    profile.updated_at = now
    if tier == 1:
        profile.status = ProfileStatus.APPROVED.value
        profile.approved_at = now
        profile.expires_at = now + APPROVAL_VALIDITY
    else:
        profile.status = ProfileStatus.NOT_STARTED.value
        profile.approved_at = None
        profile.expires_at = None
    await session.flush()
