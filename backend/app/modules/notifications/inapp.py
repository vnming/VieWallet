"""In-app notification inbox (notifications-pwa.md). Fed from outbox events.

Delivery is at-least-once, so rows are unique per (source event, user): a replayed event
never creates a second notification.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from string import Template
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import Clock
from app.core.db import Base, Database
from app.core.ids import new_id
from app.core.outbox import HandlerRegistry, OutboxMessage

CATEGORIES = (
    "TRANSACTION",
    "SECURITY",
    "KYC",
    "FINANCIAL_ALERT",
    "AI_INSIGHT",
    "SYSTEM",
    "PROMOTION",
)
TRANSACTION_TOPIC = "transaction.status_changed"


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            f"category IN ({', '.join(repr(c) for c in CATEGORIES)})", name="category_valid"
        ),
        UniqueConstraint("source_event_id", "user_id"),
        Index("ix_notifications_user_unread", "user_id", "read_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(20))
    type: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    priority: Mapped[str] = mapped_column(String(10), default="NORMAL")
    source_event_id: Mapped[uuid.UUID]
    read_at: Mapped[datetime | None]
    created_at: Mapped[datetime]


@dataclass(frozen=True, slots=True)
class InAppTemplate:
    title: dict[str, str]
    body: dict[str, str]
    priority: str = "NORMAL"


# Keyed by "<TYPE>:<new status>" (and "<TYPE>:<old>><new>" for the few that need both).
TEMPLATES: dict[str, InAppTemplate] = {
    "DEPOSIT:COMPLETED": InAppTemplate(
        {"vi": "Nạp tiền thành công", "en": "Deposit completed"},
        {
            "vi": "$amount đã vào ví của bạn. Mã $reference.",
            "en": "$amount was added to your wallet. Ref $reference.",
        },
    ),
    "DEPOSIT:FAILED": InAppTemplate(
        {"vi": "Nạp tiền không thành công", "en": "Deposit failed"},
        {
            "vi": "Lệnh nạp $amount không thành công, ví không bị trừ tiền. Mã $reference.",
            "en": "The $amount deposit failed; nothing was charged to your wallet. Ref $reference.",
        },
    ),
    "DEPOSIT:EXPIRED": InAppTemplate(
        {"vi": "Phiên nạp tiền đã hết hạn", "en": "Deposit session expired"},
        {
            "vi": "Lệnh nạp $amount đã hết hạn. Mã $reference.",
            "en": "The $amount deposit expired. Ref $reference.",
        },
    ),
    "WITHDRAWAL:PENDING": InAppTemplate(
        {"vi": "Đã nhận lệnh rút tiền", "en": "Withdrawal received"},
        {
            "vi": "$total đang được giữ trong lúc chuyển về ngân hàng. Mã $reference.",
            "en": "$total is on hold while it is sent to your bank. Ref $reference.",
        },
    ),
    "WITHDRAWAL:COMPLETED": InAppTemplate(
        {"vi": "Rút tiền thành công", "en": "Withdrawal completed"},
        {
            "vi": "$amount đã chuyển tới tài khoản ngân hàng của bạn. Mã $reference.",
            "en": "$amount was sent to your bank account. Ref $reference.",
        },
    ),
    "WITHDRAWAL:FAILED": InAppTemplate(
        {"vi": "Rút tiền không thành công", "en": "Withdrawal failed"},
        {
            "vi": "Ngân hàng không nhận lệnh rút. $total đã hoàn về ví. Mã $reference.",
            "en": "The bank did not accept the withdrawal. $total was returned to your wallet. Ref $reference.",
        },
        priority="HIGH",
    ),
    "WITHDRAWAL:PENDING>CANCELLED": InAppTemplate(
        {"vi": "Đã hủy lệnh rút tiền", "en": "Withdrawal cancelled"},
        {
            "vi": "$total đã hoàn về ví. Mã $reference.",
            "en": "$total was returned to your wallet. Ref $reference.",
        },
    ),
    "REVERSAL:COMPLETED": InAppTemplate(
        {"vi": "Khoản rút tiền bị trả lại", "en": "Withdrawal returned by the bank"},
        {
            "vi": "Ngân hàng trả lại khoản rút. $amount đã hoàn về ví. Mã $reference.",
            "en": "The bank returned the payout. $amount was credited back. Ref $reference.",
        },
        priority="HIGH",
    ),
    "REVERSAL/STAFF:COMPLETED:IN": InAppTemplate(
        {
            "vi": "Giao dịch được đảo, tiền đã hoàn về ví",
            "en": "Transaction reversed: money returned",
        },
        {
            "vi": "$amount đã được hoàn về ví của bạn. Mã $reference.",
            "en": "$amount was returned to your wallet. Ref $reference.",
        },
        priority="HIGH",
    ),
    "REVERSAL/STAFF:COMPLETED:OUT": InAppTemplate(
        {"vi": "Một giao dịch đã bị đảo", "en": "A transaction was reversed"},
        {
            "vi": "$amount đã được trả lại cho người chuyển. Mã $reference.",
            "en": "$amount was returned to the sender. Ref $reference.",
        },
        priority="HIGH",
    ),
    "TRANSFER:COMPLETED:OUT": InAppTemplate(
        {"vi": "Chuyển tiền thành công", "en": "Transfer completed"},
        {
            "vi": "Bạn đã chuyển $amount. Mã $reference.",
            "en": "You sent $amount. Ref $reference.",
        },
    ),
    "TRANSFER:COMPLETED:IN": InAppTemplate(
        {"vi": "Bạn nhận được tiền", "en": "You received money"},
        {
            "vi": "+$amount đã vào ví của bạn. Mã $reference.",
            "en": "+$amount arrived in your wallet. Ref $reference.",
        },
    ),
    "TRANSFER:PENDING:OUT": InAppTemplate(
        {"vi": "Chuyển tiền đang được kiểm tra", "en": "Transfer under review"},
        {
            "vi": "$total đang được giữ để kiểm tra an toàn (tối đa 72 giờ). Mã $reference.",
            "en": "$total is on hold for a safety check (up to 72 hours). Ref $reference.",
        },
        priority="HIGH",
    ),
    "TRANSFER:PENDING>CANCELLED:OUT": InAppTemplate(
        {"vi": "Chuyển tiền bị hủy sau kiểm tra", "en": "Transfer cancelled after review"},
        {
            "vi": "$total đã hoàn về ví của bạn. Mã $reference.",
            "en": "$total was returned to your wallet. Ref $reference.",
        },
        priority="HIGH",
    ),
    "PAYMENT:COMPLETED:OUT": InAppTemplate(
        {"vi": "Thanh toán thành công", "en": "Payment completed"},
        {
            "vi": "Bạn đã thanh toán $amount. Mã $reference.",
            "en": "You paid $amount. Ref $reference.",
        },
    ),
    "PAYMENT:COMPLETED:IN": InAppTemplate(
        {"vi": "Cửa hàng nhận được thanh toán", "en": "Payment received"},
        {
            "vi": "+$amount vào ví cửa hàng. Mã $reference.",
            "en": "+$amount to the merchant wallet. Ref $reference.",
        },
    ),
    "PAYMENT:PENDING:OUT": InAppTemplate(
        {"vi": "Thanh toán đang được kiểm tra", "en": "Payment under review"},
        {
            "vi": "$total đang được giữ để kiểm tra an toàn. Mã $reference.",
            "en": "$total is on hold for a safety check. Ref $reference.",
        },
        priority="HIGH",
    ),
    "PAYMENT:PENDING>CANCELLED:OUT": InAppTemplate(
        {"vi": "Thanh toán bị hủy sau kiểm tra", "en": "Payment cancelled after review"},
        {
            "vi": "$total đã hoàn về ví của bạn. Mã $reference.",
            "en": "$total was returned to your wallet. Ref $reference.",
        },
        priority="HIGH",
    ),
    "REFUND:COMPLETED:IN": InAppTemplate(
        {"vi": "Bạn được hoàn tiền", "en": "You got a refund"},
        {
            "vi": "+$amount được cửa hàng hoàn về ví. Mã $reference.",
            "en": "+$amount was refunded by the merchant. Ref $reference.",
        },
    ),
    "REFUND:COMPLETED:OUT": InAppTemplate(
        {"vi": "Đã hoàn tiền cho khách", "en": "Refund sent"},
        {
            "vi": "$amount đã được hoàn cho khách. Mã $reference.",
            "en": "$amount was refunded to the customer. Ref $reference.",
        },
    ),
    "ADJUSTMENT:COMPLETED": InAppTemplate(
        {"vi": "Số dư được điều chỉnh", "en": "Balance adjusted"},
        {
            "vi": "Số dư thay đổi $signed. Mã $reference.",
            "en": "Your balance changed by $signed. Ref $reference.",
        },
    ),
}


def format_vnd(amount_minor: int, locale: str) -> str:
    digits = f"{abs(amount_minor):,}"
    text = digits.replace(",", ".") + " ₫" if locale == "vi" else digits + " VND"
    return ("-" if amount_minor < 0 else "") + text


def template_for(
    txn_type: str, old: str, new: str, direction: str = "", subtype: str | None = None
) -> InAppTemplate | None:
    """Most specific first: subtype, then old>new, then the new status; each with the
    recipient's direction before without it."""
    kinds = [f"{txn_type}/{subtype}", txn_type] if subtype else [txn_type]
    for kind in kinds:
        for status in (f"{old}>{new}", new):
            for suffix in (f":{direction}", ""):
                found = TEMPLATES.get(f"{kind}:{status}{suffix}")
                if found is not None:
                    return found
    return None


def render(template: InAppTemplate, locale: str, params: Mapping[str, str]) -> tuple[str, str]:
    lang = locale if locale in template.title else "vi"
    return template.title[lang], Template(template.body[lang]).safe_substitute(params)


async def notify_transaction(db: Database, clock: Clock, message: OutboxMessage) -> None:
    payload = message.payload
    recipients: list[dict[str, Any]] = list(payload.get("recipients", []))
    if not recipients:
        return
    subtype = payload.get("subtype")
    amount = int(str(payload["amount"]))
    fee = int(str(payload.get("fee", "0")))
    async with db.transaction() as session:
        for recipient in recipients:
            template = template_for(
                str(payload["type"]),
                str(payload["from"]),
                str(payload["to"]),
                str(recipient.get("direction", "")),
                str(subtype) if subtype else None,
            )
            if template is None:
                continue
            locale = str(recipient.get("locale", "vi"))
            outgoing = recipient.get("direction") == "OUT"
            params = {
                "amount": format_vnd(amount, locale),
                "total": format_vnd(amount + (fee if outgoing else 0), locale),
                "signed": ("-" if outgoing else "+") + format_vnd(amount, locale),
                "reference": str(payload["reference"]),
            }
            title, body = render(template, locale, params)
            await session.execute(
                insert(Notification)
                .values(
                    id=new_id(),
                    user_id=uuid.UUID(str(recipient["user_id"])),
                    category="TRANSACTION",
                    type=f"transaction.{str(payload['type']).lower()}.{str(payload['to']).lower()}",
                    title=title,
                    body=body,
                    data={
                        "reference": payload["reference"],
                        "transaction_type": payload["type"],
                        "status": payload["to"],
                        "link": f"/history/{payload['reference']}",
                    },
                    priority=template.priority,
                    source_event_id=message.id,
                    created_at=clock.now(),
                )
                .on_conflict_do_nothing(index_elements=["source_event_id", "user_id"])
            )


KYC_TOPIC = "kyc.status_changed"
KYC_TEMPLATES: dict[str, InAppTemplate] = {
    "PENDING": InAppTemplate(
        {"vi": "Đã nhận hồ sơ xác minh", "en": "Verification received"},
        {
            "vi": "Hồ sơ KYC (mô phỏng) của bạn đang chờ duyệt.",
            "en": "Your (simulated) KYC submission is waiting for review.",
        },
    ),
    "APPROVED": InAppTemplate(
        {"vi": "Xác minh danh tính thành công", "en": "Identity verified"},
        {
            "vi": "Tài khoản đã lên hạn mức cấp 1: rút tiền và hạn mức cao hơn đã mở.",
            "en": "Your account is now tier 1: withdrawals and higher limits are open.",
        },
        priority="HIGH",
    ),
    "REJECTED": InAppTemplate(
        {"vi": "Hồ sơ xác minh chưa được duyệt", "en": "Verification not approved"},
        {
            "vi": "Lý do: $reason. Bạn có thể nộp lại hồ sơ.",
            "en": "Reason: $reason. You can submit again.",
        },
        priority="HIGH",
    ),
    "EXPIRED": InAppTemplate(
        {"vi": "Xác minh danh tính đã hết hạn", "en": "Verification expired"},
        {
            "vi": "Vui lòng xác minh lại để giữ hạn mức cấp 1.",
            "en": "Please verify again to keep tier 1 limits.",
        },
        priority="HIGH",
    ),
}
KYC_REASONS = {
    "BLURRY_IMAGE": {"vi": "ảnh mờ", "en": "blurry image"},
    "MISMATCH": {"vi": "thông tin không khớp giấy tờ", "en": "details do not match"},
    "EXPIRED_DOCUMENT": {"vi": "giấy tờ hết hạn", "en": "expired document"},
    "SUSPECTED_FRAUD": {"vi": "cần kiểm tra thêm", "en": "needs further checks"},
    "OTHER": {"vi": "khác", "en": "other"},
}


async def notify_kyc(db: Database, clock: Clock, message: OutboxMessage) -> None:
    payload = message.payload
    status = str(payload["status"])
    template = KYC_TEMPLATES.get(status)
    if template is None:
        return
    locale = str(payload.get("locale", "vi"))
    lang = locale if locale in ("vi", "en") else "vi"
    reason = KYC_REASONS.get(str(payload.get("reason_code") or "OTHER"), KYC_REASONS["OTHER"])
    title, body = render(template, locale, {"reason": reason[lang]})
    async with db.transaction() as session:
        await session.execute(
            insert(Notification)
            .values(
                id=new_id(),
                user_id=uuid.UUID(str(payload["user_id"])),
                category="KYC",
                type=f"kyc.{status.lower()}",
                title=title,
                body=body,
                data={"link": "/kyc", "status": status},
                priority=template.priority,
                source_event_id=message.id,
                created_at=clock.now(),
            )
            .on_conflict_do_nothing(index_elements=["source_event_id", "user_id"])
        )


SUPPORT_TOPIC = "support.ticket_updated"
SUPPORT_TEMPLATES: dict[str, InAppTemplate] = {
    "STAFF_REPLY": InAppTemplate(
        {"vi": "Hỗ trợ đã trả lời", "en": "Support replied"},
        {
            "vi": "Yêu cầu $reference: $subject",
            "en": "Ticket $reference: $subject",
        },
    ),
    "STATUS": InAppTemplate(
        {"vi": "Yêu cầu hỗ trợ được cập nhật", "en": "Ticket updated"},
        {
            "vi": "Yêu cầu $reference: $subject",
            "en": "Ticket $reference: $subject",
        },
    ),
}


async def notify_ticket(db: Database, clock: Clock, message: OutboxMessage) -> None:
    payload = message.payload
    template = SUPPORT_TEMPLATES.get(str(payload.get("kind")))
    if template is None:
        return
    locale = str(payload.get("locale", "vi"))
    title, body = render(
        template,
        locale,
        {"reference": str(payload["reference"]), "subject": str(payload["subject"])},
    )
    async with db.transaction() as session:
        await session.execute(
            insert(Notification)
            .values(
                id=new_id(),
                user_id=uuid.UUID(str(payload["user_id"])),
                category="SYSTEM",
                type=f"support.{str(payload['kind']).lower()}",
                title=title,
                body=body,
                data={"link": f"/support/tickets/{payload['reference']}"},
                priority="NORMAL",
                source_event_id=message.id,
                created_at=clock.now(),
            )
            .on_conflict_do_nothing(index_elements=["source_event_id", "user_id"])
        )


def register_inapp_handlers(registry: HandlerRegistry, db: Database, clock: Clock) -> None:
    async def handler(message: OutboxMessage) -> None:
        await notify_transaction(db, clock, message)

    async def kyc_handler(message: OutboxMessage) -> None:
        await notify_kyc(db, clock, message)

    registry.register(TRANSACTION_TOPIC, handler)
    registry.register(KYC_TOPIC, kyc_handler)

    async def ticket_handler(message: OutboxMessage) -> None:
        await notify_ticket(db, clock, message)

    registry.register(SUPPORT_TOPIC, ticket_handler)


# ---------------------------------------------------------------- queries for the API


@dataclass(frozen=True, slots=True)
class NotificationPage:
    items: list[Notification]
    next_cursor: uuid.UUID | None


async def list_for_user(
    db: Database,
    user_id: uuid.UUID,
    *,
    limit: int,
    cursor: uuid.UUID | None,
    unread_only: bool,
) -> NotificationPage:
    conditions = [Notification.user_id == user_id]
    if cursor is not None:
        conditions.append(Notification.id < cursor)
    if unread_only:
        conditions.append(Notification.read_at.is_(None))
    async with db.transaction() as session:
        rows = list(
            (
                await session.scalars(
                    select(Notification)
                    .where(*conditions)
                    .order_by(Notification.id.desc())
                    .limit(limit + 1)
                )
            ).all()
        )
    items = rows[:limit]
    return NotificationPage(items, items[-1].id if len(rows) > limit and items else None)


async def unread_count(db: Database, user_id: uuid.UUID) -> int:
    async with db.transaction() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            )
            or 0
        )


async def mark_read(
    db: Database, clock: Clock, user_id: uuid.UUID, notification_id: uuid.UUID | None
) -> int:
    """Mark one (or, with ``None``, every) unread notification of the user as read."""
    conditions = [Notification.user_id == user_id, Notification.read_at.is_(None)]
    if notification_id is not None:
        conditions.append(Notification.id == notification_id)
    async with db.transaction() as session:
        result = await session.execute(
            update(Notification).where(*conditions).values(read_at=clock.now())
        )
    return int(result.rowcount)  # type: ignore[attr-defined]
