"""Alembic environment. Runs migrations synchronously with the migrator role."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, pool, text

# Import every module that defines ORM tables so autogenerate/check sees the full metadata.
import app.core.idempotency
import app.core.jobs
import app.core.models
import app.core.outbox
import app.modules.access.models
import app.modules.audit.models
import app.modules.identity.models
import app.modules.kyc.models
import app.modules.ledger.models
import app.modules.notifications.inapp
import app.modules.operations.models
import app.modules.operations.recovery
import app.modules.payments.models
import app.modules.pricing.models
import app.modules.providers.models
import app.modules.risk.models
import app.modules.support.models
import app.modules.transactions.categories
import app.modules.transactions.exports
import app.modules.transactions.models
import app.modules.wallet.destinations
import app.modules.wallet.models
import app.modules.wallet.recipients  # noqa: F401
from app.core.db import APP_SCHEMA, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    """Explicit URL (tests) > VW_MIGRATION_DATABASE_URL env var > backend/.env."""
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("VW_MIGRATION_DATABASE_URL")
    if not url:
        url = MigrationSettings().migration_database_url
    if not url:
        raise RuntimeError("VW_MIGRATION_DATABASE_URL is not set")
    return url


class MigrationSettings(BaseSettings):
    """Only the setting migrations need, so unrelated settings cannot break migrations."""

    model_config = SettingsConfigDict(env_prefix="VW_", env_file=".env", extra="ignore")
    migration_database_url: str | None = None


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    if type_ == "schema":
        return name == APP_SCHEMA
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_schema=APP_SCHEMA,
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        # The version table lives in the app schema, so the schema must exist first.
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {APP_SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=APP_SCHEMA,
            include_schemas=True,
            include_name=include_name,
            compare_type=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
