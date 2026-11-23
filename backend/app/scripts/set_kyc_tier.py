"""Set a customer's KYC tier (development/simulation only, until the KYC flow of Phase 6).

    python -m app.scripts.set_kyc_tier --email lan@example.com --tier 1

Withdrawals need tier 1 (financial-processing.md §8.2). The change is audited.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.clock import SystemClock
from app.core.config import Environment, get_settings
from app.core.db import Database
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.modules.identity.public import set_kyc_tier
from app.modules.kyc.public import record_simulated_tier

ALLOWED = {Environment.DEVELOPMENT, Environment.SIMULATION, Environment.TEST}


async def run(argv: list[str]) -> str:
    parser = argparse.ArgumentParser(description="Set a customer's simulated KYC tier")
    parser.add_argument("--email", required=True)
    parser.add_argument("--tier", type=int, choices=[0, 1], required=True)
    args = parser.parse_args(argv)
    settings = get_settings()
    if settings.environment not in ALLOWED:
        raise SystemExit("set_kyc_tier is disabled outside development/simulation")
    configure_logging(settings.log_level, json=settings.log_json)
    db = Database(settings.database_url, pool_size=1, application_name="viewallet-set-kyc-tier")
    try:
        async with db.transaction() as session:
            clock = SystemClock()
            user_id = await set_kyc_tier(
                session,
                clock,
                email=args.email,
                tier=args.tier,
                reason="simulated KYC (development CLI)",
            )
            await record_simulated_tier(session, clock, user_id, tier=args.tier)
    finally:
        await db.dispose()
    return str(user_id)


def main() -> None:
    try:
        user_id = asyncio.run(
            run(sys.argv[1:]),
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None,
        )
    except AppError as exc:
        raise SystemExit(f"error: {exc.code}") from exc
    sys.stdout.write(f"{user_id}\n")


if __name__ == "__main__":
    main()
