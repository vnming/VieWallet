"""Clock, ids, outbox backoff, DB retry and migration SQL helpers (no database)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import FixedClock, SystemClock
from app.core.config import Environment, Settings
from app.core.db import Database, is_retryable_db_error, run_in_transaction
from app.core.idempotency import request_fingerprint
from app.core.ids import new_id
from app.core.migration_ops import make_append_only_sql
from app.core.outbox import retry_delay


class TestClock:
    def test_system_clock_is_aware_utc(self) -> None:
        now = SystemClock().now()
        assert now.tzinfo is UTC

    def test_fixed_clock(self) -> None:
        clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
        clock.advance(timedelta(minutes=5))
        assert clock.now() == datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        clock.set(datetime(2027, 1, 1, tzinfo=UTC))
        assert clock.now().year == 2027

    def test_fixed_clock_rejects_naive(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            FixedClock(datetime(2026, 1, 1))  # noqa: DTZ001
        clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
        with pytest.raises(ValueError, match="timezone-aware"):
            clock.set(datetime(2026, 1, 1))  # noqa: DTZ001


def test_ids_are_uuid7_and_time_ordered() -> None:
    ids = [new_id() for _ in range(100)]
    assert all(i.version == 7 for i in ids)
    assert ids == sorted(ids)


def test_request_fingerprint_is_canonical() -> None:
    a = request_fingerprint({"amount": "100", "note": "tiền cơm", "nested": {"b": 1, "a": 2}})
    b = request_fingerprint({"nested": {"a": 2, "b": 1}, "note": "tiền cơm", "amount": "100"})
    c = request_fingerprint({"amount": "101", "note": "tiền cơm", "nested": {"b": 1, "a": 2}})
    assert a == b
    assert a != c
    assert len(a) == 64


class TestRetryDelay:
    def test_exponential_with_cap(self) -> None:
        base, cap = timedelta(seconds=5), timedelta(hours=1)
        delays = [retry_delay(n, base=base, cap=cap) for n in range(1, 12)]
        assert delays[:4] == [timedelta(seconds=s) for s in (5, 10, 20, 40)]
        assert delays[-1] == cap
        assert delays == sorted(delays)

    def test_huge_attempt_does_not_overflow(self) -> None:
        assert retry_delay(10_000, base=timedelta(seconds=5), cap=timedelta(hours=1)) == timedelta(
            hours=1
        )

    def test_rejects_zero(self) -> None:
        with pytest.raises(ValueError, match="attempt"):
            retry_delay(0, base=timedelta(seconds=1), cap=timedelta(seconds=2))


class _OrigError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def db_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("stmt", {}, _OrigError(sqlstate))


class FakeDatabase:
    """Stands in for Database: counts transactions, never touches a real DB."""

    def __init__(self) -> None:
        self.transactions = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        self.transactions += 1
        yield cast(AsyncSession, object())


def failing(times: int, sqlstate: str) -> Callable[[AsyncSession], Awaitable[str]]:
    calls = {"n": 0}

    async def operation(_session: AsyncSession) -> str:
        calls["n"] += 1
        if calls["n"] <= times:
            raise db_error(sqlstate)
        return "done"

    return operation


class TestRunInTransaction:
    @pytest.mark.parametrize("sqlstate", ["40001", "40P01", "55P03"])
    async def test_retries_retryable_errors(self, sqlstate: str) -> None:
        fake = FakeDatabase()
        result = await run_in_transaction(
            cast(Database, fake), failing(2, sqlstate), attempts=3, base_delay=0
        )
        assert result == "done"
        assert fake.transactions == 3

    async def test_gives_up_after_attempts(self) -> None:
        fake = FakeDatabase()
        with pytest.raises(DBAPIError):
            await run_in_transaction(
                cast(Database, fake), failing(5, "40P01"), attempts=3, base_delay=0
            )
        assert fake.transactions == 3

    async def test_does_not_retry_other_errors(self) -> None:
        fake = FakeDatabase()
        with pytest.raises(DBAPIError):
            await run_in_transaction(
                cast(Database, fake), failing(1, "23505"), attempts=3, base_delay=0
            )
        assert fake.transactions == 1

    async def test_rejects_zero_attempts(self) -> None:
        with pytest.raises(ValueError, match="attempts"):
            await run_in_transaction(cast(Database, FakeDatabase()), failing(0, "x"), attempts=0)

    def test_is_retryable(self) -> None:
        assert is_retryable_db_error(db_error("40001"))
        assert not is_retryable_db_error(db_error("23505"))
        assert not is_retryable_db_error(RuntimeError())


class TestAppendOnlySql:
    def test_generates_trigger_and_grants(self) -> None:
        sql = make_append_only_sql("journals")
        assert any("BEFORE UPDATE OR DELETE ON app.journals" in s for s in sql)
        assert any("BEFORE TRUNCATE ON app.journals" in s for s in sql)
        assert any(s.startswith("REVOKE UPDATE, DELETE, TRUNCATE") for s in sql)

    @pytest.mark.parametrize("bad", ["journals; DROP TABLE x", "Journals", "1abc", ""])
    def test_rejects_unsafe_identifiers(self, bad: str) -> None:
        with pytest.raises(ValueError, match="unsafe"):
            make_append_only_sql(bad)


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VW_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
    settings: Any = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql+psycopg://u@h/db",
        environment=Environment.PRODUCTION_LIKE,
    )
    assert settings.expose_api_docs is False
