"""Posting engine: the only code that changes balances (ADR-0002, ADR-0004).

Locking protocol for one journal (financial-processing.md §6):

1. ``SELECT ... FOR UPDATE`` the balance rows of customer accounts, sorted by account id.
2. Check that no non-negative account would go below zero (domain error before DB error).
3. Insert the journal and its postings.
4. Update customer balances (already locked), then system balances with atomic increments,
   again in account-id order. Everyone writes in the same global order, so no deadlocks.

The caller owns the transaction; nothing here commits or performs external I/O.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.context import get_correlation_id
from app.core.errors import AppError, ConflictError, register_titles
from app.core.ids import new_id
from app.core.money import Money
from app.modules.ledger.domain import (
    CHART,
    LOCKED_CODES,
    AccountCode,
    JournalDraft,
    JournalKind,
    PostingLine,
    Side,
    balance_delta,
    net_reversal,
)
from app.modules.ledger.models import AccountBalance, Journal, LedgerAccount, Posting

register_titles(
    {
        "INSUFFICIENT_BALANCE": {
            "vi": "Số dư khả dụng không đủ",
            "en": "Insufficient available balance",
        },
        "ALREADY_REVERSED": {
            "vi": "Bút toán đã được đảo trước đó",
            "en": "Journal already reversed",
        },
    }
)


class InsufficientBalanceError(AppError):
    code = "INSUFFICIENT_BALANCE"
    status = 422


class AlreadyReversedError(ConflictError):
    code = "ALREADY_REVERSED"


class UnknownAccountError(LookupError):
    pass


# ---------------------------------------------------------------- accounts


async def open_account(
    session: AsyncSession,
    clock: Clock,
    *,
    code: AccountCode,
    currency: str,
    owner_ref: str | None,
    name: str,
) -> LedgerAccount:
    """Create an account and its zero balance row (idempotent on code/currency/owner)."""
    spec = CHART[code]
    now = clock.now()
    account_id = new_id()
    inserted = await session.scalar(
        insert(LedgerAccount)
        .values(
            id=account_id,
            code=code.value,
            type=spec.type.value,
            normal_side=spec.normal_side.value,
            currency=currency,
            owner_ref=owner_ref,
            allow_negative=spec.allow_negative,
            name=name,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["code", "currency", "owner_ref"])
        .returning(LedgerAccount.id)
    )
    if inserted is not None:
        session.add(
            AccountBalance(
                account_id=inserted,
                currency=currency,
                balance_minor=0,
                allow_negative=spec.allow_negative,
                updated_at=now,
            )
        )
        await session.flush()
    account = await session.scalar(
        select(LedgerAccount).where(
            LedgerAccount.code == code.value,
            LedgerAccount.currency == currency,
            LedgerAccount.owner_ref.is_not_distinct_from(owner_ref),
        )
    )
    assert account is not None  # noqa: S101 - inserted or already present
    return account


async def system_account(
    session: AsyncSession, code: AccountCode, currency: str, owner_ref: str | None = None
) -> LedgerAccount:
    account = await session.scalar(
        select(LedgerAccount).where(
            LedgerAccount.code == code.value,
            LedgerAccount.currency == currency,
            LedgerAccount.owner_ref.is_not_distinct_from(owner_ref),
        )
    )
    if account is None:
        raise UnknownAccountError(f"{code} {currency} {owner_ref} is not provisioned (run seed)")
    return account


async def seed_system_accounts(session: AsyncSession, clock: Clock, currency: str) -> None:
    for code in (
        AccountCode.HOLD,
        AccountCode.FEE_REVENUE,
        AccountCode.ADJUSTMENT_EXPENSE,
        AccountCode.SUSPENSE,
    ):
        await open_account(
            session, clock, code=code, currency=currency, owner_ref=None, name=f"{code} {currency}"
        )


async def balance_of(session: AsyncSession, account_id: uuid.UUID) -> Money:
    row = await session.get(AccountBalance, account_id)
    if row is None:
        raise UnknownAccountError(str(account_id))
    return Money(row.balance_minor, row.currency)


async def balances_of(
    session: AsyncSession, account_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Money]:
    if not account_ids:
        return {}
    rows = (
        await session.scalars(
            select(AccountBalance).where(AccountBalance.account_id.in_(account_ids))
        )
    ).all()
    return {r.account_id: Money(r.balance_minor, r.currency) for r in rows}


async def lock_balances(
    session: AsyncSession, account_ids: list[uuid.UUID]
) -> dict[uuid.UUID, AccountBalance]:
    """Lock balance rows in ascending id order (the deadlock-free global order)."""
    if not account_ids:
        return {}
    rows = (
        await session.scalars(
            select(AccountBalance)
            .where(AccountBalance.account_id.in_(sorted(set(account_ids))))
            .order_by(AccountBalance.account_id)
            .with_for_update()
        )
    ).all()
    return {row.account_id: row for row in rows}


# ---------------------------------------------------------------- posting


@dataclass(frozen=True, slots=True)
class PostedJournal:
    journal: Journal
    balances: dict[uuid.UUID, Money]


async def post(
    session: AsyncSession,
    clock: Clock,
    draft: JournalDraft,
    *,
    transaction_id: uuid.UUID | None,
    created_by: str,
    reverses_journal_id: uuid.UUID | None = None,
) -> PostedJournal:
    account_ids = sorted({line.account_id for line in draft.lines})
    accounts = {
        a.id: a
        for a in (
            await session.scalars(select(LedgerAccount).where(LedgerAccount.id.in_(account_ids)))
        ).all()
    }
    missing = set(account_ids) - set(accounts)
    if missing:
        raise UnknownAccountError(f"unknown accounts: {sorted(map(str, missing))}")
    for account in accounts.values():
        if account.currency != draft.currency:
            raise ValueError(
                f"account {account.id} is {account.currency}, journal is {draft.currency}"
            )

    deltas: dict[uuid.UUID, int] = defaultdict(int)
    for line in draft.lines:
        account = accounts[line.account_id]
        deltas[line.account_id] += balance_delta(
            Side(account.normal_side), line.side, line.amount.amount_minor
        )

    locked_ids = [aid for aid in account_ids if AccountCode(accounts[aid].code) in LOCKED_CODES]
    locked = await lock_balances(session, locked_ids)
    for aid in locked_ids:
        row = locked[aid]
        if not row.allow_negative and row.balance_minor + deltas[aid] < 0:
            raise InsufficientBalanceError()

    now = clock.now()
    journal = Journal(
        id=new_id(),
        transaction_id=transaction_id,
        kind=draft.kind.value,
        currency=draft.currency,
        description=draft.description[:200],
        reverses_journal_id=reverses_journal_id,
        posted_at=now,
        correlation_id=get_correlation_id(),
        created_by=created_by,
    )
    session.add(journal)
    await session.flush()
    for seq, line in enumerate(draft.lines, start=1):
        session.add(
            Posting(
                id=new_id(),
                journal_id=journal.id,
                account_id=line.account_id,
                side=line.side.value,
                amount_minor=line.amount.amount_minor,
                currency=line.amount.currency,
                seq=seq,
            )
        )
    await session.flush()

    balances: dict[uuid.UUID, Money] = {}
    for aid in locked_ids:  # customer accounts: already locked, sorted
        row = locked[aid]
        row.balance_minor += deltas[aid]
        row.last_journal_id = journal.id
        row.updated_at = now
        balances[aid] = Money(row.balance_minor, row.currency)
    await session.flush()
    for aid in account_ids:  # system accounts last, same order
        if aid in locked:
            continue
        new_balance = await session.scalar(
            update(AccountBalance)
            .where(AccountBalance.account_id == aid)
            .values(
                balance_minor=AccountBalance.balance_minor + deltas[aid],
                last_journal_id=journal.id,
                updated_at=now,
            )
            .returning(AccountBalance.balance_minor)
        )
        balances[aid] = Money(int(new_balance or 0), draft.currency)
    return PostedJournal(journal=journal, balances=balances)


async def journal_draft(session: AsyncSession, journal_id: uuid.UUID) -> JournalDraft:
    journal = await session.get(Journal, journal_id)
    if journal is None:
        raise LookupError(str(journal_id))
    postings = (
        await session.scalars(
            select(Posting).where(Posting.journal_id == journal_id).order_by(Posting.seq)
        )
    ).all()
    return JournalDraft(
        kind=JournalKind(journal.kind),
        lines=tuple(
            PostingLine(p.account_id, Side(p.side), Money(p.amount_minor, p.currency))
            for p in postings
        ),
        description=journal.description,
    )


async def reverse(
    session: AsyncSession,
    clock: Clock,
    journal_id: uuid.UUID,
    *,
    transaction_id: uuid.UUID | None,
    created_by: str,
) -> PostedJournal:
    """Post the exact mirror of a journal. A journal can be reversed once (unique FK)."""
    already = await session.scalar(
        select(Journal.id).where(Journal.reverses_journal_id == journal_id)
    )
    if already is not None:
        raise AlreadyReversedError()
    original = await journal_draft(session, journal_id)
    if original.kind is JournalKind.REVERSAL:
        raise AlreadyReversedError("a reversal cannot itself be reversed; use an adjustment")
    return await post(
        session,
        clock,
        original.reversed(),
        transaction_id=transaction_id,
        created_by=created_by,
        reverses_journal_id=journal_id,
    )


async def reverse_transaction(
    session: AsyncSession,
    clock: Clock,
    journal_ids: list[uuid.UUID],
    *,
    transaction_id: uuid.UUID,
    created_by: str,
) -> PostedJournal:
    """Undo the net effect of a business transaction's journals with one REVERSAL journal.

    A held transfer has two journals (hold, release); mirroring only the last one would leave
    money on HOLD. Netting per account and posting the opposite gives the exact undo. The new
    journal points at the last original journal (unique FK), so it can be reversed only once.
    """
    if not journal_ids:
        raise LookupError("nothing to reverse")
    drafts = [await journal_draft(session, journal_id) for journal_id in journal_ids]
    if any(d.kind is JournalKind.REVERSAL for d in drafts):
        raise AlreadyReversedError("a reversal cannot itself be reversed; use an adjustment")
    last = journal_ids[-1]
    if await session.scalar(select(Journal.id).where(Journal.reverses_journal_id == last)):
        raise AlreadyReversedError()
    return await post(
        session,
        clock,
        net_reversal(drafts),
        transaction_id=transaction_id,
        created_by=created_by,
        reverses_journal_id=last,
    )


async def journals_of(session: AsyncSession, transaction_id: uuid.UUID) -> list[uuid.UUID]:
    """Journal ids of one business transaction, oldest first (UUIDv7 ids are time-ordered)."""
    rows = await session.scalars(
        select(Journal.id).where(Journal.transaction_id == transaction_id).order_by(Journal.id)
    )
    return list(rows.all())


# ---------------------------------------------------------------- invariants


@dataclass(frozen=True, slots=True)
class InvariantViolation:
    invariant: str
    subject: str
    expected: str
    actual: str


async def check_invariants(session: AsyncSession) -> list[InvariantViolation]:
    """I1-I4 of financial-processing.md §3 (I5 needs transaction data; see transactions)."""
    violations: list[InvariantViolation] = []

    # I1: every journal balances.
    rows = await session.execute(
        text(
            """
            SELECT journal_id,
                   SUM(CASE WHEN side = 'DEBIT' THEN amount_minor ELSE 0 END) AS debit,
                   SUM(CASE WHEN side = 'CREDIT' THEN amount_minor ELSE 0 END) AS credit
            FROM app.postings GROUP BY journal_id
            HAVING SUM(CASE WHEN side = 'DEBIT' THEN amount_minor ELSE -amount_minor END) <> 0
            """
        )
    )
    violations.extend(
        InvariantViolation("I1", f"journal:{r.journal_id}", str(r.debit), str(r.credit))
        for r in rows
    )

    # I2: projection equals the sum of postings in the normal direction.
    rows = await session.execute(
        text(
            """
            SELECT a.id, b.balance_minor AS stored,
                   COALESCE(SUM(CASE WHEN p.side = a.normal_side THEN p.amount_minor
                                     ELSE -p.amount_minor END), 0) AS computed
            FROM app.ledger_accounts a
            JOIN app.account_balances b ON b.account_id = a.id
            LEFT JOIN app.postings p ON p.account_id = a.id
            GROUP BY a.id, b.balance_minor
            HAVING b.balance_minor <> COALESCE(SUM(
                CASE WHEN p.side = a.normal_side THEN p.amount_minor ELSE -p.amount_minor END), 0)
            """
        )
    )
    violations.extend(
        InvariantViolation("I2", f"account:{r.id}", str(r.computed), str(r.stored)) for r in rows
    )

    # I3: non-negative accounts are not negative.
    rows = await session.execute(
        select(AccountBalance.account_id, AccountBalance.balance_minor).where(
            AccountBalance.allow_negative.is_(False), AccountBalance.balance_minor < 0
        )
    )
    violations.extend(
        InvariantViolation("I3", f"account:{r.account_id}", ">= 0", str(r.balance_minor))
        for r in rows
    )

    # I4: trial balance per currency (debit-normal total == credit-normal total).
    trial = await session.execute(
        text(
            """
            SELECT a.currency,
                   SUM(CASE WHEN a.normal_side = 'DEBIT' THEN b.balance_minor ELSE 0 END)
                       AS debit_side,
                   SUM(CASE WHEN a.normal_side = 'CREDIT' THEN b.balance_minor ELSE 0 END)
                       AS credit_side
            FROM app.ledger_accounts a JOIN app.account_balances b ON b.account_id = a.id
            GROUP BY a.currency
            """
        )
    )
    violations.extend(
        InvariantViolation("I4", f"currency:{r.currency}", str(r.debit_side), str(r.credit_side))
        for r in trial
        if r.debit_side != r.credit_side
    )
    return violations
