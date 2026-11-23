"""Turn a customer into a simulated merchant (development/simulation only).

    python -m app.scripts.create_merchant --email shop@example.com --name "Quán Mây"

With ``--create-user`` a verified demo customer is created first; its password is read from
stdin so it never appears in shell history or process lists:

    echo "<password>" | python -m app.scripts.create_merchant --create-user \
        --email cafe-may@viewallet.local --owner "Lê Thu Mây" --name "Cà phê Mây (mô phỏng)"

The merchant gets its own MERCHANT wallet. Every change is audited.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from redis.asyncio import Redis

from app.core.clock import SystemClock
from app.core.config import Environment, get_settings
from app.core.db import Database
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.rate_limit import RateLimiter
from app.modules.identity.public import IdentityServices, create_verified_customer
from app.modules.payments.public import create_merchant

ALLOWED = {Environment.DEVELOPMENT, Environment.SIMULATION, Environment.TEST}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a simulated VieWallet merchant")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True, help="display name shown to payers")
    parser.add_argument("--legal-name", default=None, help="fictional legal name")
    parser.add_argument("--mcc", default="5814", help="4-digit merchant category code")
    parser.add_argument("--create-user", action="store_true")
    parser.add_argument("--owner", default="Chủ cửa hàng mô phỏng", help="with --create-user")
    return parser.parse_args(argv)


async def run(argv: list[str], password: str | None) -> str:
    args = parse_args(argv)
    settings = get_settings()
    if settings.environment not in ALLOWED:
        raise SystemExit("create_merchant is disabled outside development/simulation")
    configure_logging(settings.log_level, json=settings.log_json)
    clock = SystemClock()
    db = Database(settings.database_url, pool_size=1, application_name="viewallet-merchant")
    redis = Redis.from_url(settings.redis_url)
    try:
        if args.create_user:
            if not password:
                raise SystemExit("--create-user needs a password on stdin")
            ctx = IdentityServices.build(settings, db, clock, RateLimiter(redis, clock))
            await create_verified_customer(
                ctx, email=args.email, full_name=args.owner, password=password
            )
        async with db.transaction() as session:
            merchant = await create_merchant(
                session,
                clock,
                email=args.email.strip().lower(),
                display_name=args.name,
                legal_name=args.legal_name or f"{args.name} (hư cấu)",
                mcc=args.mcc,
                reason="simulated merchant (CLI)",
            )
    finally:
        await redis.aclose()
        await db.dispose()
    return str(merchant.id)


def main() -> None:
    argv = sys.argv[1:]
    password = sys.stdin.readline().rstrip("\n") if "--create-user" in argv else None
    try:
        merchant_id = asyncio.run(
            run(argv, password),
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None,
        )
    except AppError as exc:
        raise SystemExit(f"error: {exc.code}: {exc.detail or ''}") from exc
    sys.stdout.write(f"{merchant_id}\n")


if __name__ == "__main__":
    main()
