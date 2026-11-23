"""Create a verified demo customer (development/simulation only).

    echo "<password>" | python -m app.scripts.create_customer \
        --email an@example.com --name "Nguyễn Văn An"

The password is read from stdin. Used by demos and E2E journeys that are not about sign-up
(sign-up itself is rate limited per IP and covered by its own journey).
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

ALLOWED = {Environment.DEVELOPMENT, Environment.SIMULATION, Environment.TEST}


async def run(argv: list[str], password: str) -> str:
    parser = argparse.ArgumentParser(description="Create a verified demo customer")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    settings = get_settings()
    if settings.environment not in ALLOWED:
        raise SystemExit("create_customer is disabled outside development/simulation")
    if not password:
        raise SystemExit("a password is required on stdin")
    configure_logging(settings.log_level, json=settings.log_json)
    clock = SystemClock()
    db = Database(settings.database_url, pool_size=1, application_name="viewallet-customer")
    redis = Redis.from_url(settings.redis_url)
    try:
        ctx = IdentityServices.build(settings, db, clock, RateLimiter(redis, clock))
        user_id = await create_verified_customer(
            ctx, email=args.email, full_name=args.name, password=password
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
        raise SystemExit(f"error: {exc.code}: {exc.detail or ''}") from exc
    sys.stdout.write(f"{user_id}\n")


if __name__ == "__main__":
    main()
