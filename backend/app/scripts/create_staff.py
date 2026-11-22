"""Create a staff account (there is no public staff sign-up).

Usage (password read from stdin so it never appears in shell history or process lists):

    echo "<password>" | python -m app.scripts.create_staff \
        --email ops@viewallet.local --name "Ops Demo" --role OPERATIONS --role SUPPORT

Staff must enroll TOTP at their first admin sign-in.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from redis.asyncio import Redis

from app.core.clock import SystemClock
from app.core.config import get_settings
from app.core.db import Database
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.rate_limit import RateLimiter
from app.modules.identity.public import IdentityServices, create_staff


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a VieWallet staff account")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", action="append", required=True, help="repeatable")
    return parser.parse_args(argv)


async def run(argv: list[str], password: str) -> str:
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    clock = SystemClock()
    db = Database(settings.database_url, pool_size=1, application_name="viewallet-create-staff")
    redis = Redis.from_url(settings.redis_url)
    try:
        ctx = IdentityServices.build(settings, db, clock, RateLimiter(redis, clock))
        user_id = await create_staff(
            ctx, email=args.email, full_name=args.name, password=password, roles=args.role
        )
    finally:
        await redis.aclose()
        await db.dispose()
    return str(user_id)


def main() -> None:
    password = sys.stdin.readline().rstrip("\n")
    try:
        user_id = asyncio.run(
            run(sys.argv[1:], password),
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None,
        )
    except AppError as exc:
        raise SystemExit(
            f"error: {exc.code}: {exc.detail or ''} {[e.to_dict() for e in exc.errors]}"
        ) from exc
    sys.stdout.write(f"{user_id}\n")


if __name__ == "__main__":
    main()
