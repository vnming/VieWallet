"""KYC API (api-spec.md §4): the customer flow and the reviewer queue.

Uploads are the raw file as the request body (``Content-Type: image/jpeg|image/png|
application/pdf``), read as a stream and cut off past 5 MB, so no multipart parser is needed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from app.core.errors import NotFoundError
from app.modules.identity.public import (
    CustomerPrincipal,
    IdentityDep,
    Principal,
    require_permission,
)
from app.modules.kyc import service
from app.modules.kyc.documents import MAX_BYTES, DocumentStore, KycFileTooLargeError, aclean
from app.modules.kyc.models import (
    DocumentKind,
    DocumentType,
    KycDocument,
    KycSubmission,
    ProfileStatus,
    RejectReason,
    SubmissionStatus,
)

router = APIRouter(prefix="/api/v1/kyc", tags=["kyc"])
admin_router = APIRouter(prefix="/api/v1/admin/kyc", tags=["admin-kyc"])

CAN_REVIEW = require_permission("kyc:review:any")


class KycContext:
    """Built once per process (composition root)."""

    def __init__(self, keys: service.KycKeys, store: DocumentStore, link_ttl: timedelta) -> None:
        self.keys = keys
        self.store = store
        self.link_ttl = link_ttl


def kyc_context(request: Request) -> KycContext:
    ctx: KycContext = request.app.state.kyc
    return ctx


KycDep = Annotated[KycContext, Depends(kyc_context)]


class DraftIn(BaseModel):
    full_name: str = Field(min_length=3, max_length=120)
    date_of_birth: date
    document_type: DocumentType
    document_number: str = Field(min_length=8, max_length=20)
    address: str = Field(min_length=5, max_length=200)


class DocumentOut(BaseModel):
    id: uuid.UUID
    kind: DocumentKind
    content_type: str
    size_bytes: int
    uploaded_at: datetime


class SubmissionOut(BaseModel):
    id: uuid.UUID
    status: SubmissionStatus
    full_name: str
    date_of_birth: date
    document_type: DocumentType
    document_number_masked: str
    address: str
    documents: list[DocumentOut]
    required_documents: list[DocumentKind]
    submitted_at: datetime | None
    decided_at: datetime | None
    reject_reason: RejectReason | None


class KycStatusOut(BaseModel):
    status: ProfileStatus
    tier: int
    approved_at: datetime | None
    expires_at: datetime | None
    submission: SubmissionOut | None = Field(description="The open or most recent submission")


class ReviewOut(BaseModel):
    reviewer_id: uuid.UUID
    decision: str
    reason_code: RejectReason | None
    note: str | None
    at: datetime


class AdminDocumentOut(DocumentOut):
    url: str = Field(description="Signed link, valid a few minutes, bound to the viewer")


class AdminSubmissionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    status: SubmissionStatus
    full_name: str
    date_of_birth: date
    document_type: DocumentType
    document_number_masked: str
    address: str
    submitted_at: datetime | None
    claimed_by: uuid.UUID | None
    claimed_at: datetime | None
    decided_at: datetime | None


class AdminSubmissionDetailOut(AdminSubmissionOut):
    documents: list[AdminDocumentOut]
    reviews: list[ReviewOut]
    duplicate_document_accounts: int = Field(
        description="Other accounts that submitted the same document number"
    )
    can_decide: bool


class QueueOut(BaseModel):
    items: list[AdminSubmissionOut]
    page: int
    page_size: int
    total: int


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(APPROVE|REJECT)$")
    reason_code: RejectReason | None = None
    note: str | None = Field(default=None, max_length=500)


def _document_out(d: KycDocument) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        kind=DocumentKind(d.kind),
        content_type=d.content_type,
        size_bytes=d.size_bytes,
        uploaded_at=d.created_at,
    )


async def _submission_out(session: object, row: KycSubmission) -> SubmissionOut:
    documents = await service.documents_of(session, row.id)  # type: ignore[arg-type]
    reviews = await service.reviews_of(session, row.id)  # type: ignore[arg-type]
    reason = next((r.reason_code for r in reversed(reviews) if r.reason_code), None)
    return SubmissionOut(
        id=row.id,
        status=SubmissionStatus(row.status),
        full_name=row.full_name,
        date_of_birth=row.date_of_birth,
        document_type=DocumentType(row.document_type),
        document_number_masked=row.document_number_masked,
        address=row.address,
        documents=[_document_out(d) for d in documents],
        required_documents=sorted(service.required_kinds(DocumentType(row.document_type))),
        submitted_at=row.submitted_at,
        decided_at=row.decided_at,
        reject_reason=RejectReason(reason) if reason else None,
    )


async def _status(session: object, user_id: uuid.UUID) -> KycStatusOut:
    profile = await service.profile_of(session, user_id)  # type: ignore[arg-type]
    submission = await service.latest_submission(session, user_id)  # type: ignore[arg-type]
    return KycStatusOut(
        status=ProfileStatus(profile.status) if profile else ProfileStatus.NOT_STARTED,
        tier=profile.tier if profile else 0,
        approved_at=profile.approved_at if profile else None,
        expires_at=profile.expires_at if profile else None,
        submission=await _submission_out(session, submission) if submission else None,
    )


# ---------------------------------------------------------------- customer


@router.get("", response_model=KycStatusOut)
async def my_kyc(principal: CustomerPrincipal, ctx: IdentityDep) -> KycStatusOut:
    async with ctx.db.transaction() as session:
        return await _status(session, principal.user_id)


@router.put("/draft", response_model=KycStatusOut)
async def save_draft(
    body: DraftIn, principal: CustomerPrincipal, ctx: IdentityDep, kyc: KycDep
) -> KycStatusOut:
    async with ctx.db.transaction() as session:
        await service.save_draft(
            session,
            ctx.clock,
            kyc.keys,
            user_id=principal.user_id,
            data=service.SubmissionInput(
                full_name=body.full_name,
                date_of_birth=body.date_of_birth,
                document_type=body.document_type,
                document_number=body.document_number,
                address=body.address,
            ),
        )
        return await _status(session, principal.user_id)


async def _read_limited(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise KycFileTooLargeError()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BYTES:
            raise KycFileTooLargeError()
        chunks.append(chunk)
    return b"".join(chunks)


_BINARY = {"schema": {"type": "string", "format": "binary"}}


@router.put(
    "/draft/documents/{kind}",
    response_model=DocumentOut,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "The raw file (JPEG, PNG or PDF, at most 5 MB)",
            "content": {"image/jpeg": _BINARY, "image/png": _BINARY, "application/pdf": _BINARY},
        }
    },
)
async def upload_document(
    kind: DocumentKind,
    request: Request,
    principal: CustomerPrincipal,
    ctx: IdentityDep,
    kyc: KycDep,
) -> DocumentOut:
    cleaned = await aclean(await _read_limited(request))
    async with ctx.db.transaction() as session:
        document = await service.attach_document(
            session, ctx.clock, kyc.store, user_id=principal.user_id, kind=kind, file=cleaned
        )
        return _document_out(document)


@router.post("/submit", response_model=KycStatusOut)
async def submit(principal: CustomerPrincipal, ctx: IdentityDep) -> KycStatusOut:
    async with ctx.db.transaction() as session:
        await service.submit(session, ctx.clock, user_id=principal.user_id)
        return await _status(session, principal.user_id)


# ---------------------------------------------------------------- reviewer


def _admin_out(row: KycSubmission) -> AdminSubmissionOut:
    return AdminSubmissionOut(
        id=row.id,
        user_id=row.user_id,
        status=SubmissionStatus(row.status),
        full_name=row.full_name,
        date_of_birth=row.date_of_birth,
        document_type=DocumentType(row.document_type),
        document_number_masked=row.document_number_masked,
        address=row.address,
        submitted_at=row.submitted_at,
        claimed_by=row.claimed_by,
        claimed_at=row.claimed_at,
        decided_at=row.decided_at,
    )


async def _detail(
    session: object, *, ctx: IdentityDep, kyc: KycContext, row: KycSubmission, viewer: uuid.UUID
) -> AdminSubmissionDetailOut:
    now = ctx.clock.now()
    documents = []
    for d in await service.documents_of(session, row.id):  # type: ignore[arg-type]
        exp, sig = service.sign_link(kyc.keys, d.id, viewer, now, kyc.link_ttl)
        documents.append(
            AdminDocumentOut(
                **_document_out(d).model_dump(),
                url=f"/api/v1/admin/kyc/documents/{d.id}/content?exp={exp}&sig={sig}",
            )
        )
    reviews = await service.reviews_of(session, row.id)  # type: ignore[arg-type]
    return AdminSubmissionDetailOut(
        **_admin_out(row).model_dump(),
        documents=documents,
        reviews=[
            ReviewOut(
                reviewer_id=r.reviewer_id,
                decision=r.decision,
                reason_code=RejectReason(r.reason_code) if r.reason_code else None,
                note=r.note,
                at=r.at,
            )
            for r in reviews
        ],
        duplicate_document_accounts=await service.other_users_with_document(session, row),  # type: ignore[arg-type]
        can_decide=row.status == SubmissionStatus.IN_REVIEW.value and row.claimed_by == viewer,
    )


@admin_router.get("/submissions", response_model=QueueOut)
async def review_queue(
    ctx: IdentityDep,
    _principal: Principal = CAN_REVIEW,
    status: SubmissionStatus | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> QueueOut:
    async with ctx.db.transaction() as session:
        rows, total = await service.queue(session, status=status, page=page, page_size=page_size)
    return QueueOut(
        items=[_admin_out(r) for r in rows], page=page, page_size=page_size, total=total
    )


@admin_router.get("/submissions/{submission_id}", response_model=AdminSubmissionDetailOut)
async def review_detail(
    submission_id: uuid.UUID, ctx: IdentityDep, kyc: KycDep, principal: Principal = CAN_REVIEW
) -> AdminSubmissionDetailOut:
    async with ctx.db.transaction() as session:
        row = await service.get_submission(session, submission_id)
        return await _detail(session, ctx=ctx, kyc=kyc, row=row, viewer=principal.user_id)


@admin_router.post("/submissions/{submission_id}/claim", response_model=AdminSubmissionDetailOut)
async def claim(
    submission_id: uuid.UUID, ctx: IdentityDep, kyc: KycDep, principal: Principal = CAN_REVIEW
) -> AdminSubmissionDetailOut:
    async with ctx.db.transaction() as session:
        row = await service.claim(session, ctx.clock, submission_id, reviewer_id=principal.user_id)
        return await _detail(session, ctx=ctx, kyc=kyc, row=row, viewer=principal.user_id)


@admin_router.post("/submissions/{submission_id}/decision", response_model=AdminSubmissionDetailOut)
async def decide(
    submission_id: uuid.UUID,
    body: DecisionIn,
    ctx: IdentityDep,
    kyc: KycDep,
    principal: Principal = CAN_REVIEW,
) -> AdminSubmissionDetailOut:
    async with ctx.db.transaction() as session:
        row = await service.decide(
            session,
            ctx.clock,
            submission_id,
            reviewer_id=principal.user_id,
            approve=body.decision == "APPROVE",
            reason_code=body.reason_code,
            note=body.note,
        )
        return await _detail(session, ctx=ctx, kyc=kyc, row=row, viewer=principal.user_id)


@admin_router.get("/documents/{document_id}/content")
async def document_content(
    *,
    document_id: uuid.UUID,
    ctx: IdentityDep,
    kyc: KycDep,
    exp: int = Query(),
    sig: str = Query(max_length=64),
    principal: Principal = CAN_REVIEW,
) -> Response:
    service.check_link(
        kyc.keys,
        document_id,
        viewer_id=principal.user_id,
        exp=exp,
        signature=sig,
        now=ctx.clock.now(),
    )
    async with ctx.db.transaction() as session:
        document, data = await service.read_document(
            session, ctx.clock, kyc.store, document_id, viewer_id=principal.user_id
        )
    if not data:  # pragma: no cover - stored files are never empty
        raise NotFoundError()
    disposition = "attachment" if document.content_type == "application/pdf" else "inline"
    return Response(
        content=data,
        media_type=document.content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="kyc-{document.kind.lower()}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; sandbox",
        },
    )
