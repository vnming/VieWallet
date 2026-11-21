"""Database-level guards: role privileges, append-only trigger, migration/model drift."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.db import Database, sqlstate_of
from app.core.migration_ops import make_append_only_sql
from tests.conftest import IntegrationSettings
from tests.integration.conftest import alembic_config


async def test_currencies_are_seeded_reference_data(db: Database, migrator_db: Database) -> None:
    async with migrator_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO app.currencies (code, exponent, enabled) VALUES ('VND', 0, true) "
                "ON CONFLICT (code) DO NOTHING"
            )
        )
    async with db.transaction() as session:
        exponent = await session.scalar(
            text("SELECT exponent FROM app.currencies WHERE code = 'VND'")
        )
    assert exponent == 0


async def test_app_role_cannot_modify_reference_data(db: Database) -> None:
    with pytest.raises(ProgrammingError) as exc_info:
        async with db.transaction() as session:
            await session.execute(text("UPDATE app.currencies SET exponent = 2"))
    assert sqlstate_of(exc_info.value) == "42501"  # insufficient_privilege


async def test_app_role_cannot_run_ddl(db: Database) -> None:
    with pytest.raises(ProgrammingError) as exc_info:
        async with db.transaction() as session:
            await session.execute(text("CREATE TABLE app.should_not_exist (id int)"))
    assert sqlstate_of(exc_info.value) == "42501"


@pytest.fixture
async def append_only_table(migrator_db: Database) -> AsyncIterator[str]:
    async with migrator_db.transaction() as session:
        await session.execute(text("DROP TABLE IF EXISTS app.test_append_only"))
        await session.execute(
            text("CREATE TABLE app.test_append_only (id int PRIMARY KEY, note text)")
        )
        for statement in make_append_only_sql("test_append_only"):
            await session.execute(text(statement))
    yield "app.test_append_only"
    async with migrator_db.transaction() as session:
        await session.execute(text("DROP TABLE app.test_append_only"))


async def test_append_only_allows_insert_and_select(db: Database, append_only_table: str) -> None:
    async with db.transaction() as session:
        await session.execute(text(f"INSERT INTO {append_only_table} VALUES (1, 'a')"))  # noqa: S608
        assert await session.scalar(text(f"SELECT count(*) FROM {append_only_table}")) == 1  # noqa: S608


@pytest.mark.parametrize(
    "statement",
    ["UPDATE {t} SET note = 'b'", "DELETE FROM {t}", "TRUNCATE {t}"],
)
async def test_append_only_blocks_changes_even_for_the_owner(
    migrator_db: Database, append_only_table: str, statement: str
) -> None:
    async with migrator_db.transaction() as session:
        await session.execute(text(f"INSERT INTO {append_only_table} VALUES (1, 'a')"))  # noqa: S608
    with pytest.raises(DBAPIError) as exc_info:
        async with migrator_db.transaction() as session:
            await session.execute(text(statement.format(t=append_only_table)))
    assert sqlstate_of(exc_info.value) == "VW001"


@pytest.mark.parametrize("statement", ["UPDATE {t} SET note = 'b'", "DELETE FROM {t}"])
async def test_app_role_has_no_update_delete_grant(
    db: Database, append_only_table: str, statement: str
) -> None:
    with pytest.raises(ProgrammingError) as exc_info:
        async with db.transaction() as session:
            await session.execute(text(statement.format(t=append_only_table)))
    assert sqlstate_of(exc_info.value) == "42501"


async def test_models_match_migrations(migrated: IntegrationSettings) -> None:
    """Fails when an ORM model changes without a migration (`alembic check`)."""
    assert migrated.migration_database_url
    command.check(alembic_config(migrated.migration_database_url))
