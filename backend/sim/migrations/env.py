"""Alembic environment for the simulator database (role ``viewallet_sim``)."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, pool, text

import sim.models  # noqa: F401 - registers the tables
from sim.db import SIM_SCHEMA, SimBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SimBase.metadata


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIM_", env_file=".env", extra="ignore")
    database_url: str | None = None


def database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("SIM_DATABASE_URL")
    url = url or _Settings().database_url
    if not url:
        raise RuntimeError("SIM_DATABASE_URL is not set")
    return url


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    return name == SIM_SCHEMA if type_ == "schema" else True


def run() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SIM_SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=SIM_SCHEMA,
            include_schemas=True,
            include_name=include_name,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run()
