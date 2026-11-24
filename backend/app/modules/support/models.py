"""Help center articles and support tickets (database.md §3.9)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id
from app.modules.identity.public import User

# Tables referenced by foreign keys must be registered in this process's metadata too.
_FOREIGN_KEY_TARGETS = (User,)

TICKET_STATUSES = ("OPEN", "PENDING_CUSTOMER", "RESOLVED", "CLOSED")
TICKET_CATEGORIES = ("TRANSACTION", "ACCOUNT", "KYC", "SECURITY", "OTHER")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class HelpArticle(Base):
    __tablename__ = "help_articles"
    __table_args__ = (
        UniqueConstraint("slug", "locale"),
        CheckConstraint("locale IN ('vi', 'en')", name="locale_valid"),
        Index("ix_help_articles_locale_category", "locale", "category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(80))
    locale: Mapped[str] = mapped_column(String(2))
    category: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    # Lower-case ASCII of title + summary + body: accent-insensitive search without extensions.
    search_text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime]


class Ticket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        CheckConstraint(_in("status", TICKET_STATUSES), name="status_valid"),
        CheckConstraint(_in("category", TICKET_CATEGORIES), name="category_valid"),
        Index("ix_support_tickets_user_updated", "user_id", "updated_at"),
        Index("ix_support_tickets_status_updated", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    reference: Mapped[str] = mapped_column(String(12), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"))
    category: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20))
    transaction_reference: Mapped[str | None] = mapped_column(String(18))
    assigned_to: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]


class TicketMessage(Base):
    """Conversation of a ticket (append-only: trigger + grants). Internal notes are staff-only."""

    __tablename__ = "support_ticket_messages"
    __table_args__ = (
        CheckConstraint("author_type IN ('CUSTOMER', 'STAFF')", name="author_type_valid"),
        CheckConstraint("NOT internal OR author_type = 'STAFF'", name="internal_is_staff"),
        Index("ix_support_ticket_messages_ticket_at", "ticket_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.support_tickets.id"))
    author_type: Mapped[str] = mapped_column(String(10))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"))
    body: Mapped[str] = mapped_column(Text)
    internal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]
