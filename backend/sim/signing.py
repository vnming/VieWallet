"""Callback signatures: ``v1=<hex HMAC-SHA256(secret, timestamp + "." + raw_body)>``."""

from __future__ import annotations

import hashlib
import hmac


def signature(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "v1=" + mac.hexdigest()
