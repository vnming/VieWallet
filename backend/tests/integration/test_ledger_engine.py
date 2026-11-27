"""Posting engine against PostgreSQL: locking, DB guards (FT-25, FT-26), reversal (FT-23),
concurrency and invariant checks."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.clock import FixedClock
from app.core.db import Database, sqlstate_of
from app.core.money import Money
from app.modules.ledger.public import (
    AccountBalance,
    AccountCode,
    AlreadyReversedError,
    InsufficientBalanceError,
    Journal,
    JournalDraft,
    JournalKind,
    UnknownAccountError,
    balance_of,
    check_invariants,
    credit,
    debit,
    open_account,
    post,
    reverse,
    system_account,
)
from app.modules.wallet.public import Wallet
from tests.integration.finance_helpers import fund, new_wallet


def vnd(amount: int) -> Money:
    return Money(amount, "VND")


async def transfer(
    db: Database, clock: FixedClock, source: Wallet, target: Wallet, amount: int
) -> uuid.UUID:
    async with db.transaction() as session:
        posted = await post(
            session,
            clock,
            JournalDraft(
                JournalKind.TRANSFER,
                (
                    debit(source.ledger_account_id, vnd(amount)),
                    credit(target.ledger_account_id, vnd(amount)),
                ),
            ),
            transaction_id=None,
            created_by="test",
        )
        return posted.journal.id


async def balance(db: Database, wallet: Wallet) -> int:
    async with db.transaction() as session:
        return (await balance_of(session, wallet.ledger_account_id)).amount_minor


async def test_posting_moves_money_and_keeps_invariants(db: Database, clock: FixedClock) -> None:
    a, b = await new_wallet(db, clock), await new_wallet(db, clock)
    await fund(db, clock, a, 100_000)
    await transfer(db, clock, a, b, 30_000)
    assert (await balance(db, a), await balance(db, b)) == (70_000, 30_000)
    async with db.transaction() as session:
        assert await check_invariants(session) == []
        journal = await session.scalar(select(Journal).order_by(Journal.id.desc()))
        assert journal is not None
        assert journal.created_by == "test"


async def test_insufficient_balance_changes_nothing(db: Database, clock: FixedClock) -> None:
    a, b = await new_wallet(db, clock), await new_wallet(db, clock)
    await fund(db, clock, a, 1_000)
    with pytest.raises(InsufficientBalanceError):
        await transfer(db, clock, a, b, 1_001)
    assert (await balance(db, a), await balance(db, b)) == (1_000, 0)


async def test_unknown_account_and_currency_mismatch(db: Database, clock: FixedClock) -> None:
    a = await new_wallet(db, clock)
    async with db.transaction() as session:
        with pytest.raises(UnknownAccountError):
            await post(
                session,
                clock,
                JournalDraft(
                    JournalKind.TRANSFER,
                    (debit(uuid.uuid4(), vnd(1)), credit(a.ledger_account_id, vnd(1))),
                ),
                transaction_id=None,
                created_by="test",
            )
        with pytest.raises(UnknownAccountError):
            await system_account(session, AccountCode.HOLD, "USD")
    async with db.transaction() as session:
        usd = await open_account(
            session, clock, code=AccountCode.SUSPENSE, currency="USD", owner_ref="t", name="t"
        )
        with pytest.raises(ValueError, match="journal is VND"):
            await post(
                session,
                clock,
                JournalDraft(
                    JournalKind.ADJUSTMENT,
                    (debit(usd.id, vnd(1)), credit(a.ledger_account_id, vnd(1))),
                ),
                transaction_id=None,
                created_by="test",
            )
        await session.rollback()


async def test_open_account_is_idempotent(db: Database, clock: FixedClock) -> None:
    async with db.transaction() as session:
        first = await open_account(
            session, clock, code=AccountCode.SUSPENSE, currency="VND", owner_ref="idem", name="x"
        )
        again = await open_account(
            session, clock, code=AccountCode.SUSPENSE, currency="VND", owner_ref="idem", name="y"
        )
        assert first.id == again.id
        await session.rollback()


async def test_reversal_once_only(db: Database, clock: FixedClock) -> None:
    """FT-23: the second reversal is refused (service check and UNIQUE reverses_journal_id)."""
    a, b = await new_wallet(db, clock), await new_wallet(db, clock)
    await fund(db, clock, a, 50_000)
    original = await transfer(db, clock, a, b, 20_000)
    async with db.transaction() as session:
        mirror = await reverse(session, clock, original, transaction_id=None, created_by="test")
    assert (await balance(db, a), await balance(db, b)) == (50_000, 0)
    async with db.transaction() as session:
        with pytest.raises(AlreadyReversedError):
            await reverse(session, clock, original, transaction_id=None, created_by="test")
        with pytest.raises(AlreadyReversedError):
            await reverse(session, clock, mirror.journal.id, transaction_id=None, created_by="test")
    # Bypassing the service: the unique constraint still refuses a second reversal row.
    with pytest.raises(DBAPIError) as excinfo:
        async with db.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO app.journals (id, kind, currency, description,"
                    " reverses_journal_id,"
                    " posted_at, created_by) VALUES (:id, 'REVERSAL', 'VND', '', :orig, now(), 't')"
                ),
                {"id": uuid.uuid4(), "orig": original},
            )
    assert sqlstate_of(excinfo.value) == "23505"


async def test_reversal_cannot_overdraw_the_receiver(db: Database, clock: FixedClock) -> None:
    a, b, c = [await new_wallet(db, clock) for _ in range(3)]
    await fund(db, clock, a, 10_000)
    original = await transfer(db, clock, a, b, 10_000)
    await transfer(db, clock, b, c, 10_000)  # receiver already spent it
    async with db.transaction() as session:
        with pytest.raises(InsufficientBalanceError):
            await reverse(session, clock, original, transaction_id=None, created_by="test")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE app.postings SET amount_minor = amount_minor + 1",
        "DELETE FROM app.postings",
        "UPDATE app.journals SET description = 'x'",
        "DELETE FROM app.journals",
        "UPDATE app.transaction_events SET message = 'x'",
        "DELETE FROM app.transaction_events",
        "DELETE FROM app.risk_assessments",
        "DELETE FROM app.account_balances",
        "DELETE FROM app.ledger_accounts",
        "UPDATE app.fee_rules SET fixed_minor = 0",
        "DELETE FROM app.limit_rules",
        "UPDATE app.limit_rules SET max_amount_minor = 0",
        "DELETE FROM app.wallets",
        "TRUNCATE app.postings",
    ],
)
async def test_append_only_and_read_only_tables(
    db: Database, clock: FixedClock, statement: str
) -> None:
    """FT-25: the application role cannot rewrite history (trigger VW001 or no privilege)."""
    a, b = await new_wallet(db, clock), await new_wallet(db, clock)
    await fund(db, clock, a, 5_000)
    await transfer(db, clock, a, b, 1_000)
    with pytest.raises(DBAPIError) as excinfo:
        async with db.transaction() as session:
            await session.execute(text(statement))
    assert sqlstate_of(excinfo.value) in {"VW001", "42501"}


async def test_triggers_block_even_the_table_owner(
    migrator_db: Database, db: Database, clock: FixedClock
) -> None:
    a, b = await new_wallet(db, clock), await new_wallet(db, clock)
    await fund(db, clock, a, 5_000)
    await transfer(db, clock, a, b, 1_000)
    for statement in ("UPDATE app.postings SET amount_minor = 1", "DELETE FROM app.journals"):
        with pytest.raises(DBAPIError) as excinfo:
            async with migrator_db.transaction() as session:
                await session.execute(text(statement))
        assert sqlstate_of(excinfo.value) == "VW001"


async def insert_raw_journal(
    db: Database, lines: list[tuple[uuid.UUID, str, int, str]], *, keep: bool = True
) -> None:
    async with db.transaction() as session:
        journal_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO app.journals (id, kind, currency, description, posted_at, created_by)"
                " VALUES (:id, 'ADJUSTMENT', 'VND', 'raw', now(), 'attacker')"
            ),
            {"id": journal_id},
        )
        for seq, (account, side, amount, currency) in enumerate(lines, start=1):
            await session.execute(
                text(
                    "INSERT INTO app.postings (id, journal_id, account_id, side, amount_minor,"
                    " currency, seq) VALUES (gen_random_uuid(), :j, :a, :s, :m, :c, :q)"
                ),
                {"j": journal_id, "a": account, "s": side, "m": amount, "c": currency, "q": seq},
            )
        if not keep:
            # Fire the deferred checks now, then discard: a raw journal leaves balances stale.
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            await session.rollback()


async def test_deferred_trigger_rejects_unbalanced_journals(
    db: Database, clock: FixedClock
) -> None:
    """FT-26: SQL that bypasses the domain cannot commit an unbalanced journal."""
    a = await new_wallet(db, clock)
    async with db.transaction() as session:
        expense = (await system_account(session, AccountCode.ADJUSTMENT_EXPENSE, "VND")).id
    wallet = a.ledger_account_id
    cases = [
        [(expense, "DEBIT", 1_000, "VND"), (wallet, "CREDIT", 999, "VND")],  # unbalanced
        [(expense, "DEBIT", 1_000, "VND")],  # single line
        [],  # journal without postings
        [(expense, "DEBIT", 1_000, "VND"), (wallet, "CREDIT", 1_000, "USD")],  # currency
    ]
    for lines in cases:
        with pytest.raises(DBAPIError) as excinfo:
            await insert_raw_journal(db, lines)
        assert sqlstate_of(excinfo.value) == "VW002", lines
    await insert_raw_journal(
        db, [(expense, "DEBIT", 1, "VND"), (wallet, "CREDIT", 1, "VND")], keep=False
    )


async def test_non_negative_check_is_the_last_line_of_defence(
    db: Database, clock: FixedClock
) -> None:
    a = await new_wallet(db, clock)
    with pytest.raises(DBAPIError) as excinfo:
        async with db.transaction() as session:
            await session.execute(
                text("UPDATE app.account_balances SET balance_minor = -1 WHERE account_id = :a"),
                {"a": a.ledger_account_id},
            )
    assert sqlstate_of(excinfo.value) == "23514"


async def test_concurrent_debits_never_overdraw(db: Database, clock: FixedClock) -> None:
    """20 concurrent 1 000 debits against 7 000: exactly 7 succeed."""
    source, target = await new_wallet(db, clock), await new_wallet(db, clock)
    await fund(db, clock, source, 7_000)
    results = await asyncio.gather(
        *(transfer(db, clock, source, target, 1_000) for _ in range(20)), return_exceptions=True
    )
    ok = [r for r in results if isinstance(r, uuid.UUID)]
    refused = [r for r in results if isinstance(r, InsufficientBalanceError)]
    assert (len(ok), len(refused)) == (7, 13)
    assert (await balance(db, source), await balance(db, target)) == (0, 7_000)


async def test_opposite_transfers_do_not_deadlock(db: Database, clock: FixedClock) -> None:
    """A->B and B->A at the same time: ordered locking means no deadlock, totals preserved."""
    a, b = await new_wallet(db, clock), await new_wallet(db, clock)
    await fund(db, clock, a, 100_000)
    await fund(db, clock, b, 100_000)
    jobs = [transfer(db, clock, a, b, 100) for _ in range(15)]
    jobs += [transfer(db, clock, b, a, 100) for _ in range(15)]
    await asyncio.wait_for(asyncio.gather(*jobs), timeout=30)
    assert (await balance(db, a), await balance(db, b)) == (100_000, 100_000)


async def test_invariant_check_detects_tampering(
    migrator_db: Database, db: Database, clock: FixedClock
) -> None:
    a = await new_wallet(db, clock)
    await fund(db, clock, a, 10_000)
    async with migrator_db.transaction() as session:
        await session.execute(
            text(
                "UPDATE app.account_balances SET balance_minor = balance_minor + 5"
                " WHERE account_id = :a"
            ),
            {"a": a.ledger_account_id},
        )
        found = {(v.invariant, v.subject) for v in await check_invariants(session)}
        assert ("I2", f"account:{a.ledger_account_id}") in found
        assert any(inv == "I4" for inv, _ in found)
        await session.rollback()
    async with db.transaction() as session:
        assert await check_invariants(session) == []
        row = await session.get(AccountBalance, a.ledger_account_id)
        assert row is not None
        assert row.balance_minor == 10_000
