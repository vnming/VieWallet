"""Crypto helpers, password/PIN policies, CSRF middleware, e-mail templates, RBAC catalog."""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis

from app.core.clock import FixedClock
from app.core.crypto import (
    CryptoError,
    DataCipher,
    SecretHasher,
    decode_key,
    numeric_code,
    random_token,
)
from app.core.errors import register_exception_handlers, register_titles
from app.core.middleware import CsrfProtectionMiddleware
from app.core.rate_limit import RateLimit, RateLimiter
from app.modules.access.catalog import PERMISSION_CODES, ROLES, SENSITIVE_PERMISSIONS
from app.modules.identity.domain import pin_policy
from app.modules.identity.domain.password_policy import check_password
from app.modules.notifications.templates import TEMPLATES, UnknownTemplateError, render


def key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


class TestCrypto:
    def test_random_material(self) -> None:
        assert len(random_token(32)) >= 43
        assert random_token() != random_token()
        codes = {numeric_code(6) for _ in range(200)}
        assert all(len(c) == 6 and c.isdigit() for c in codes)
        assert len(codes) > 150

    @pytest.mark.parametrize("bad", ["", "not-base64!!", base64.b64encode(b"short").decode()])
    def test_decode_key_rejects_bad_keys(self, bad: str) -> None:
        with pytest.raises(CryptoError):
            decode_key(bad)

    def test_decode_key_accepts_standard_and_urlsafe(self) -> None:
        raw = os.urandom(32)
        assert decode_key(base64.b64encode(raw).decode()) == raw
        assert decode_key(base64.urlsafe_b64encode(raw).decode().rstrip("=")) == raw

    def test_cipher_round_trip_and_randomized(self) -> None:
        cipher = DataCipher.from_setting(key())
        a, b = cipher.encrypt("mã bí mật"), cipher.encrypt("mã bí mật")
        assert a != b  # random nonce
        assert a.startswith("v1.k1.")
        assert cipher.decrypt(a) == "mã bí mật"

    def test_cipher_detects_tampering_wrong_context_and_wrong_key(self) -> None:
        cipher = DataCipher.from_setting(key())
        token = cipher.encrypt("123456", associated_data="ctx-a")
        with pytest.raises(CryptoError):
            cipher.decrypt(token, associated_data="ctx-b")
        tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
        with pytest.raises(CryptoError):
            cipher.decrypt(tampered, associated_data="ctx-a")
        with pytest.raises(CryptoError):
            DataCipher.from_setting(key()).decrypt(token, associated_data="ctx-a")
        with pytest.raises(CryptoError):
            cipher.decrypt("garbage")

    def test_cipher_key_rotation(self) -> None:
        old, new = decode_key(key()), decode_key(key())
        legacy = DataCipher({"k1": old}, "k1").encrypt("secret")
        rotated = DataCipher({"k1": old, "k2": new}, "k2")
        assert rotated.decrypt(legacy) == "secret"
        assert rotated.encrypt("x").startswith("v1.k2.")

    def test_cipher_rejects_bad_configuration(self) -> None:
        with pytest.raises(CryptoError):
            DataCipher({"k1": b"x" * 32}, "k2")
        with pytest.raises(CryptoError):
            DataCipher({"k.1": b"x" * 32}, "k.1")
        with pytest.raises(CryptoError):
            DataCipher({"k1": b"short"}, "k1")

    def test_secret_hasher(self) -> None:
        hasher = SecretHasher(time_cost=1, memory_kib=8, parallelism=1)
        encoded = hasher.hash("correct horse battery staple")
        assert encoded.startswith("$argon2id$")
        assert hasher.verify(encoded, "correct horse battery staple")
        assert not hasher.verify(encoded, "wrong")
        assert not hasher.verify("not-a-hash", "x")
        stronger = SecretHasher(time_cost=2, memory_kib=16, parallelism=1)
        assert stronger.needs_rehash(encoded)
        hasher.dummy_verify("anything")  # must not raise


async def test_hasher_async_variants_do_not_block_the_event_loop() -> None:
    hasher = SecretHasher(time_cost=2, memory_kib=16 * 1024, parallelism=1)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    encoded = await hasher.ahash("correct horse battery staple")
    assert await hasher.averify(encoded, "correct horse battery staple")
    assert not await hasher.averify(encoded, "wrong")
    await hasher.adummy_verify("x")
    task.cancel()
    assert ticks > 10  # the loop kept running while hashing happened in a thread


class TestPasswordPolicy:
    def test_accepts_long_unrelated_passphrase(self) -> None:
        assert (
            check_password(
                "trời xanh mây trắng 2026", email="lan@example.com", full_name="Lan Tran"
            )
            == []
        )

    @pytest.mark.parametrize(
        ("password", "code"),
        [
            ("short1!", "TOO_SHORT"),
            ("x" * 129, "TOO_LONG"),
            ("password123", "TOO_COMMON"),
            ("Matkhau123", "TOO_COMMON"),
            ("ababababababab", "TOO_COMMON"),
            ("nguyenlan-2026-xyz", "CONTAINS_PERSONAL_INFO"),
            ("my-Tran-family-key", "CONTAINS_PERSONAL_INFO"),
        ],
    )
    def test_rejects(self, password: str, code: str) -> None:
        codes = {
            v.code
            for v in check_password(password, email="nguyenlan@example.com", full_name="Lan Tran")
        }
        assert code in codes


class TestPinPolicy:
    @pytest.mark.parametrize(
        "pin",
        [
            "000000",
            "111111",
            "123456",
            "654321",
            "012345",
            "121212",
            "123123",
            "112233",
            "12345",
            "abcdef",
        ],
    )
    def test_weak_pins(self, pin: str) -> None:
        assert pin_policy.is_weak(pin)

    @pytest.mark.parametrize("pin", ["270419", "835162", "402957"])
    def test_acceptable_pins(self, pin: str) -> None:
        assert not pin_policy.is_weak(pin)

    def test_five_failures_lock_for_thirty_minutes(self) -> None:
        now = datetime(2026, 11, 21, tzinfo=UTC)
        state = pin_policy.PinState(0, None, 0, None, False)
        remaining = []
        for _ in range(4):
            outcome = pin_policy.register_failure(state, now)
            state = outcome.state
            remaining.append(outcome.remaining_attempts)
        assert remaining == [4, 3, 2, 1]
        outcome = pin_policy.register_failure(state, now)
        assert outcome.locked
        assert outcome.state.locked_until == now + timedelta(minutes=30)
        assert pin_policy.is_locked(outcome.state, now + timedelta(minutes=29))
        assert not pin_policy.is_locked(outcome.state, now + timedelta(minutes=31))

    def test_third_lock_in_24h_requires_reset(self) -> None:
        now = datetime(2026, 11, 21, tzinfo=UTC)
        state = pin_policy.PinState(0, None, 0, None, False)
        for lock in range(3):
            for _ in range(5):
                state = pin_policy.register_failure(state, now).state
            now += timedelta(minutes=31)
            assert state.requires_reset is (lock == 2)
        assert pin_policy.is_locked(state, now + timedelta(days=30))

    def test_lock_window_expires(self) -> None:
        now = datetime(2026, 11, 21, tzinfo=UTC)
        state = pin_policy.PinState(0, None, 2, now - timedelta(hours=25), False)
        for _ in range(5):
            state = pin_policy.register_failure(state, now).state
        assert state.lock_count == 1
        assert not state.requires_reset

    def test_success_clears_failures(self) -> None:
        state = pin_policy.PinState(3, None, 1, None, False)
        assert pin_policy.register_success(state).failed_attempts == 0


class TestTemplates:
    @pytest.mark.parametrize("name", sorted(TEMPLATES))
    @pytest.mark.parametrize("locale", ["vi", "en"])
    def test_every_template_renders(self, name: str, locale: str) -> None:
        params = dict.fromkeys(
            ("name", "code", "minutes", "link", "until", "detail", "new_email"), "X"
        )
        subject, body = render(name, locale, params)
        assert subject
        assert "MÔ PHỎNG" in body or "SIMULATED" in body

    def test_unknown_locale_falls_back_to_vietnamese(self) -> None:
        subject, _ = render("password_changed", "fr", {"name": "Lan"})
        assert "Mật khẩu" in subject

    def test_errors(self) -> None:
        with pytest.raises(UnknownTemplateError):
            render("nope", "vi", {})
        with pytest.raises(KeyError):
            render("verify_email", "vi", {"name": "Lan"})


class TestRbacCatalog:
    def test_super_admin_has_every_permission(self) -> None:
        super_admin = next(r for r in ROLES if r.code == "SUPER_ADMIN")
        assert set(super_admin.permissions) == PERMISSION_CODES

    def test_customers_only_have_own_scope(self) -> None:
        for role in ROLES:
            if not role.is_staff:
                assert all(p.endswith(":own") for p in role.permissions)

    def test_separation_of_duties(self) -> None:
        roles = {r.code: set(r.permissions) for r in ROLES}
        assert "adjustment:approve:any" not in roles["OPERATIONS"]
        assert "kyc:review:any" not in roles["SUPPORT"]
        assert "audit:verify:any" in SENSITIVE_PERMISSIONS

    def test_title_registration_rules(self) -> None:
        with pytest.raises(ValueError, match="vi and en"):
            register_titles({"X_ONLY_VI": {"vi": "x"}})
        with pytest.raises(ValueError, match="already registered"):
            register_titles({"NOT_FOUND": {"vi": "khác", "en": "other"}})


def csrf_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(
        CsrfProtectionMiddleware, trusted_origins=frozenset({"http://127.0.0.1:8080"})
    )

    @app.post("/api/v1/thing")
    async def thing() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/api/v1/thing")
    async def read_thing() -> dict[str, str]:
        return {"ok": "yes"}

    @app.post("/api/v1/webhooks/providers/sim")
    async def webhook() -> dict[str, str]:
        return {"ok": "yes"}

    return app


@pytest.fixture
async def csrf_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=csrf_app()), base_url="http://viewallet.test"
    ) as http:
        yield http


class TestCsrf:
    async def test_unsafe_request_without_header_is_rejected(
        self, csrf_client: httpx.AsyncClient
    ) -> None:
        response = await csrf_client.post("/api/v1/thing")
        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_FAILED"

    async def test_header_and_trusted_origin_pass(self, csrf_client: httpx.AsyncClient) -> None:
        response = await csrf_client.post(
            "/api/v1/thing", headers={"X-VW-CSRF": "1", "Origin": "http://127.0.0.1:8080"}
        )
        assert response.status_code == 200

    async def test_untrusted_origin_is_rejected(self, csrf_client: httpx.AsyncClient) -> None:
        response = await csrf_client.post(
            "/api/v1/thing", headers={"X-VW-CSRF": "1", "Origin": "https://evil.example"}
        )
        assert response.status_code == 403

    async def test_safe_methods_and_webhooks_are_exempt(
        self, csrf_client: httpx.AsyncClient
    ) -> None:
        assert (await csrf_client.get("/api/v1/thing")).status_code == 200
        assert (await csrf_client.post("/api/v1/webhooks/providers/sim")).status_code == 200


async def test_rate_limiter_fails_open_without_redis() -> None:
    redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    try:
        limiter = RateLimiter(redis, FixedClock(datetime(2026, 1, 1, tzinfo=UTC)))
        result = await limiter.hit(RateLimit("x", 1, timedelta(minutes=1)), "s")
        assert result.allowed
        await limiter.reset(RateLimit("x", 1, timedelta(minutes=1)), "s")
    finally:
        await redis.aclose()
