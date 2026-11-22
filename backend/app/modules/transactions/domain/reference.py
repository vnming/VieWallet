"""Transaction references: ``VW`` + ``YYMMDD`` + 10 Crockford base32 characters (§12)."""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
RANDOM_LENGTH = 10
BUSINESS_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
REFERENCE_RE = re.compile(r"^VW[0-9]{6}[0-9A-HJKMNP-TV-Z]{10}$")


def new_reference(now: datetime) -> str:
    day = now.astimezone(BUSINESS_TZ).strftime("%y%m%d")
    suffix = "".join(secrets.choice(CROCKFORD) for _ in range(RANDOM_LENGTH))
    return f"VW{day}{suffix}"


def is_reference(value: str) -> bool:
    return bool(REFERENCE_RE.match(value))
