"""Dependencies shared by the identity services (built once per process)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.crypto import DataCipher, SecretHasher, decode_key, hmac_sha256_hex
from app.core.db import Database
from app.core.rate_limit import RateLimit, RateLimiter
from app.modules.audit.public import AuditEntry, record


@dataclass(frozen=True, slots=True)
class RequestMeta:
    ip: str | None = None
    user_agent: str | None = None


# Rate limits (security.md §2.3).
LOGIN_PER_ACCOUNT_IP = RateLimit("login_account_ip", 5, timedelta(minutes=15))
LOGIN_PER_IP = RateLimit("login_ip", 20, timedelta(minutes=15))
REGISTER_PER_IP = RateLimit("register_ip", 10, timedelta(hours=1))
EMAIL_SEND_PER_ADDRESS = RateLimit("email_send_address", 3, timedelta(hours=1))
EMAIL_SEND_PER_IP = RateLimit("email_send_ip", 20, timedelta(hours=1))
CODE_CHECK_PER_IP = RateLimit("code_check_ip", 30, timedelta(minutes=15))
MFA_PER_SESSION = RateLimit("mfa_session", 5, timedelta(minutes=15))

ACCOUNT_LOCK_THRESHOLD = 10
ACCOUNT_LOCK_BASE = timedelta(minutes=15)
ACCOUNT_LOCK_MAX = timedelta(hours=24)

EMAIL_CODE_TTL = timedelta(minutes=15)
EMAIL_CODE_MAX_ATTEMPTS = 5
RESET_LINK_TTL = timedelta(minutes=30)
MFA_PENDING_TTL = timedelta(minutes=10)
STEP_UP_WINDOW = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class IdentityServices:
    settings: Settings
    db: Database
    clock: Clock
    hasher: SecretHasher
    cipher: DataCipher
    rate_limiter: RateLimiter
    pin_key: bytes
    code_key: bytes

    @classmethod
    def build(
        cls, settings: Settings, db: Database, clock: Clock, rate_limiter: RateLimiter
    ) -> IdentityServices:
        pepper = decode_key(settings.pin_pepper)
        return cls(
            settings=settings,
            db=db,
            clock=clock,
            hasher=SecretHasher(
                time_cost=settings.argon2_time_cost,
                memory_kib=settings.argon2_memory_kib,
                parallelism=settings.argon2_parallelism,
            ),
            cipher=DataCipher.from_setting(settings.data_encryption_key),
            rate_limiter=rate_limiter,
            # Separate sub-keys so one secret never serves two purposes.
            pin_key=bytes.fromhex(hmac_sha256_hex(pepper, "viewallet/pin")),
            code_key=bytes.fromhex(hmac_sha256_hex(pepper, "viewallet/verification-codes")),
        )

    def hash_code(self, purpose: str, user_id: uuid.UUID, code: str) -> str:
        """Keyed hash for short codes: a DB dump alone cannot brute-force them offline."""
        return hmac_sha256_hex(self.code_key, f"{purpose}:{user_id}:{code}")

    def pin_input(self, pin: str) -> str:
        return hmac_sha256_hex(self.pin_key, pin)

    async def audit(
        self,
        session: AsyncSession,
        *,
        action: str,
        actor_type: str,
        actor_id: uuid.UUID | None,
        meta: RequestMeta,
        target_id: uuid.UUID | None = None,
        result: str = "SUCCESS",
        details: dict[str, object] | None = None,
    ) -> None:
        await record(
            session,
            self.clock,
            AuditEntry(
                action=action,
                actor_type=actor_type,
                actor_id=actor_id,
                target_type="user" if target_id else None,
                target_id=str(target_id) if target_id else None,
                result=result,
                details=dict(details) if details else None,
                ip=meta.ip,
                user_agent=meta.user_agent,
            ),
        )
