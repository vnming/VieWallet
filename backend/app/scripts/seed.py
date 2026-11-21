"""Idempotent reference-data seed. Run after migrations: ``python -m app.scripts.seed``.

Uses the migrator role. Demo data (fictional users, transactions) is a separate command added
in later phases and always goes through the services.
"""

from __future__ import annotations

import asyncio
import sys

import structlog
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.core.db import Database
from app.core.logging import configure_logging
from app.core.models import CurrencyRecord

logger = structlog.get_logger("app.seed")

# Only VND is enabled (assumption 1 in the Phase 0 report); USD exists to prove the
# multi-currency model but stays disabled.
CURRENCIES = [
    {"code": "VND", "exponent": 0, "enabled": True},
    {"code": "USD", "exponent": 2, "enabled": False},
]


async def seed(db: Database) -> None:
    async with db.transaction() as session:
        for currency in CURRENCIES:
            stmt = insert(CurrencyRecord).values(**currency)
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[CurrencyRecord.code],
                    set_={"exponent": stmt.excluded.exponent},
                )
            )
    logger.info("seed_completed", currencies=[c["code"] for c in CURRENCIES])


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    if not settings.migration_database_url:
        raise SystemExit("VW_MIGRATION_DATABASE_URL is required for seeding")
    db = Database(settings.migration_database_url, pool_size=1, application_name="viewallet-seed")
    try:
        await seed(db)
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(
        _main(), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    )
