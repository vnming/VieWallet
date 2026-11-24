"""Help center and support tickets (PRD FR-OPS-04, design-system `/help`, `/support/tickets`).

Articles are Markdown files in ``support/articles`` (``<slug>.<locale>.md`` with a small header)
seeded into ``help_articles``; search is accent-insensitive (``search_text`` is plain ASCII)
and every word must match.

Tickets: the customer opens one (optionally about one of their own transactions) and writes
messages; staff with ``ticket:manage:any`` reply, add internal notes and change the status. A
customer message on a resolved ticket reopens it. Messages are append-only.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import ConflictError, NotFoundError, register_titles
from app.core.ids import new_id
from app.core.outbox import publish
from app.core.rate_limit import RateLimit
from app.modules.audit.public import AuditEntry, record
from app.modules.identity.public import User
from app.modules.support.models import HelpArticle, Ticket, TicketMessage

register_titles(
    {
        "HELP_ARTICLE_NOT_FOUND": {"vi": "Không tìm thấy bài viết", "en": "Article not found"},
        "TICKET_NOT_FOUND": {"vi": "Không tìm thấy yêu cầu hỗ trợ", "en": "Ticket not found"},
        "TICKET_CLOSED": {
            "vi": "Yêu cầu đã đóng, vui lòng tạo yêu cầu mới",
            "en": "The ticket is closed; please open a new one",
        },
    }
)

ARTICLES_DIR = Path(__file__).parent / "articles"
TICKET_TOPIC = "support.ticket_updated"
# A person opens a handful of tickets, not a stream of them.
TICKETS_PER_USER = RateLimit("ticket_create_user", 5, timedelta(hours=1))
_REF_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"


class HelpArticleNotFoundError(NotFoundError):
    code = "HELP_ARTICLE_NOT_FOUND"


class TicketNotFoundError(NotFoundError):
    code = "TICKET_NOT_FOUND"


class TicketClosedError(ConflictError):
    code = "TICKET_CLOSED"


def plain(text: str) -> str:
    """Lower-case ASCII ("Rút tiền" -> "rut tien") for accent-insensitive search."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


# ---------------------------------------------------------------- articles


@dataclass(frozen=True, slots=True)
class ArticleFile:
    slug: str
    locale: str
    category: str
    title: str
    summary: str
    position: int
    body: str


def parse_article(path: Path) -> ArticleFile:
    slug, locale = path.name.removesuffix(".md").rsplit(".", 1)
    text = path.read_text(encoding="utf-8")
    header, _, body = text.partition("\n---\n")
    meta = dict(line.split(":", 1) for line in header.strip().splitlines() if ":" in line)
    return ArticleFile(
        slug=slug,
        locale=locale,
        category=meta["category"].strip(),
        title=meta["title"].strip(),
        summary=meta["summary"].strip(),
        position=int(meta.get("position", "0").strip()),
        body=body.strip(),
    )


def article_files() -> list[ArticleFile]:
    return [parse_article(p) for p in sorted(ARTICLES_DIR.glob("*.md"))]


async def seed_articles(session: AsyncSession, clock: Clock) -> int:
    """Upsert the bundled articles (content in the repository is the source of truth)."""
    files = article_files()
    for item in files:
        values = {
            "category": item.category,
            "title": item.title,
            "summary": item.summary,
            "body": item.body,
            "search_text": plain(f"{item.title}\n{item.summary}\n{item.body}"),
            "position": item.position,
            "updated_at": clock.now(),
        }
        stmt = insert(HelpArticle).values(id=new_id(), slug=item.slug, locale=item.locale, **values)
        await session.execute(
            stmt.on_conflict_do_update(index_elements=["slug", "locale"], set_=values)
        )
    return len(files)


_WORD = re.compile(r"[a-z0-9]+")
MIN_WORD = 2  # single letters would match every article


async def search_articles(
    session: AsyncSession, *, locale: str, query: str | None, limit: int = 30
) -> list[HelpArticle]:
    conditions: list[ColumnElement[bool]] = [HelpArticle.locale == locale]
    words = _WORD.findall(plain(query or ""))[:8]
    conditions += [HelpArticle.search_text.contains(w) for w in words if len(w) >= MIN_WORD]
    rows = await session.scalars(
        select(HelpArticle)
        .where(and_(*conditions))
        .order_by(HelpArticle.category, HelpArticle.position, HelpArticle.title)
        .limit(limit)
    )
    return list(rows.all())


async def get_article(session: AsyncSession, slug: str, locale: str) -> HelpArticle:
    article: HelpArticle | None = await session.scalar(
        select(HelpArticle).where(HelpArticle.slug == slug, HelpArticle.locale == locale)
    )
    if article is None:
        raise HelpArticleNotFoundError()
    return article


# ---------------------------------------------------------------- tickets


def new_ticket_reference() -> str:
    return "TK" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(8))


@dataclass(frozen=True, slots=True)
class NewTicket:
    category: str
    subject: str
    message: str
    transaction_reference: str | None = None


def _clean(text: str, limit: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text).strip()[:limit]


async def _event(session: AsyncSession, clock: Clock, ticket: Ticket, kind: str) -> None:
    locale = await session.scalar(select(User.locale).where(User.id == ticket.user_id))
    await publish(
        session,
        clock,
        topic=TICKET_TOPIC,
        aggregate_type="support_ticket",
        aggregate_id=str(ticket.id),
        payload={
            "user_id": str(ticket.user_id),
            "locale": locale or "vi",
            "reference": ticket.reference,
            "subject": ticket.subject,
            "status": ticket.status,
            "kind": kind,
        },
    )


async def open_ticket(
    session: AsyncSession, clock: Clock, *, user_id: uuid.UUID, ticket: NewTicket
) -> Ticket:
    now = clock.now()
    row = Ticket(
        id=new_id(),
        reference=new_ticket_reference(),
        user_id=user_id,
        category=ticket.category,
        subject=_clean(ticket.subject, 120),
        status="OPEN",
        transaction_reference=ticket.transaction_reference,
        assigned_to=None,
        created_at=now,
        updated_at=now,
        closed_at=None,
    )
    session.add(row)
    await session.flush()
    session.add(
        TicketMessage(
            id=new_id(),
            ticket_id=row.id,
            author_type="CUSTOMER",
            author_id=user_id,
            body=_clean(ticket.message, 4000),
            internal=False,
            created_at=now,
        )
    )
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="support.ticket_opened",
            actor_type="USER",
            actor_id=user_id,
            target_type="support_ticket",
            target_id=str(row.id),
            details={"category": row.category, "transaction": row.transaction_reference},
        ),
    )
    return row


async def customer_tickets(session: AsyncSession, user_id: uuid.UUID) -> list[Ticket]:
    rows = await session.scalars(
        select(Ticket).where(Ticket.user_id == user_id).order_by(Ticket.updated_at.desc())
    )
    return list(rows.all())


async def get_ticket(
    session: AsyncSession, reference: str, *, user_id: uuid.UUID | None, lock: bool = False
) -> Ticket:
    """``user_id`` set: only the owner's ticket (others get 404). None: staff."""
    stmt = select(Ticket).where(Ticket.reference == reference.upper())
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    ticket: Ticket | None = await session.scalar(stmt)
    if ticket is None or (user_id is not None and ticket.user_id != user_id):
        raise TicketNotFoundError()
    return ticket


async def messages_of(
    session: AsyncSession, ticket_id: uuid.UUID, *, include_internal: bool
) -> list[TicketMessage]:
    conditions: list[ColumnElement[bool]] = [TicketMessage.ticket_id == ticket_id]
    if not include_internal:
        conditions.append(TicketMessage.internal.is_(False))
    rows = await session.scalars(
        select(TicketMessage)
        .where(*conditions)
        .order_by(TicketMessage.created_at, TicketMessage.id)
    )
    return list(rows.all())


async def customer_reply(
    session: AsyncSession, clock: Clock, reference: str, *, user_id: uuid.UUID, body: str
) -> Ticket:
    ticket = await get_ticket(session, reference, user_id=user_id, lock=True)
    if ticket.status == "CLOSED":
        raise TicketClosedError()
    now = clock.now()
    session.add(
        TicketMessage(
            id=new_id(),
            ticket_id=ticket.id,
            author_type="CUSTOMER",
            author_id=user_id,
            body=_clean(body, 4000),
            internal=False,
            created_at=now,
        )
    )
    ticket.status = "OPEN"  # waiting for staff again (reopens a resolved ticket)
    ticket.updated_at = now
    await session.flush()
    return ticket


async def staff_reply(
    session: AsyncSession,
    clock: Clock,
    reference: str,
    *,
    staff_id: uuid.UUID,
    body: str,
    internal: bool,
    status: str | None,
) -> Ticket:
    ticket = await get_ticket(session, reference, user_id=None, lock=True)
    if ticket.status == "CLOSED":
        raise TicketClosedError()
    now = clock.now()
    if body.strip():
        session.add(
            TicketMessage(
                id=new_id(),
                ticket_id=ticket.id,
                author_type="STAFF",
                author_id=staff_id,
                body=_clean(body, 4000),
                internal=internal,
                created_at=now,
            )
        )
    before = ticket.status
    if status is not None:
        ticket.status = status
        ticket.closed_at = now if status == "CLOSED" else None
    elif body.strip() and not internal:
        ticket.status = "PENDING_CUSTOMER"
    ticket.assigned_to = ticket.assigned_to or staff_id
    ticket.updated_at = now
    await session.flush()
    await record(
        session,
        clock,
        AuditEntry(
            action="support.ticket_updated",
            actor_type="STAFF",
            actor_id=staff_id,
            target_type="support_ticket",
            target_id=str(ticket.id),
            details={"internal": internal, "before": before, "after": ticket.status},
        ),
    )
    if body.strip() and not internal:
        await _event(session, clock, ticket, "STAFF_REPLY")
    elif ticket.status != before:
        await _event(session, clock, ticket, "STATUS")
    return ticket


async def staff_queue(
    session: AsyncSession, *, status: str | None, page: int, page_size: int
) -> tuple[list[Ticket], int]:
    conditions: list[ColumnElement[bool]] = [Ticket.status == status] if status else []
    total = await session.scalar(select(func.count()).select_from(Ticket).where(*conditions))
    rows = await session.scalars(
        select(Ticket)
        .where(*conditions)
        .order_by(Ticket.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(rows.all()), int(total or 0)
