"""Cryptographic helpers: random secrets, hashing, Argon2id and AES-256-GCM.

Nothing here invents cryptography: Argon2id comes from argon2-cffi and AES-GCM from the
``cryptography`` package. These helpers only fix parameters and formats in one place.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12


class CryptoError(ValueError):
    """Invalid key material or ciphertext."""


def random_token(nbytes: int = 32) -> str:
    """URL-safe random token (session tokens, reset links)."""
    return secrets.token_urlsafe(nbytes)


def numeric_code(digits: int = 6) -> str:
    """Uniformly random numeric code, zero-padded (email verification codes)."""
    return f"{secrets.randbelow(10**digits):0{digits}d}"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hmac_sha256_hex(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def decode_key(encoded: str) -> bytes:
    """Decode a base64 (standard or URL-safe) 32-byte key."""
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=False)
    except (binascii.Error, ValueError) as exc:
        raise CryptoError("key is not valid base64") from exc
    if len(raw) != KEY_BYTES:
        raise CryptoError(f"key must decode to {KEY_BYTES} bytes")
    return raw


class SecretHasher:
    """Argon2id hashing for passwords and (peppered) PINs."""

    def __init__(self, *, time_cost: int, memory_kib: int, parallelism: int) -> None:
        self._hasher = PasswordHasher(
            time_cost=time_cost, memory_cost=memory_kib, parallelism=parallelism
        )
        # Pre-computed hash used to spend the same time when an account does not exist.
        self._dummy_hash = self._hasher.hash(random_token(16))

    def hash(self, secret: str) -> str:
        return self._hasher.hash(secret)

    def verify(self, encoded_hash: str, secret: str) -> bool:
        try:
            return self._hasher.verify(encoded_hash, secret)
        except VerifyMismatchError, VerificationError, InvalidHashError:
            return False

    def needs_rehash(self, encoded_hash: str) -> bool:
        return self._hasher.check_needs_rehash(encoded_hash)

    def dummy_verify(self, secret: str) -> None:
        """Burn the cost of one verification (timing-safe 'user not found' path)."""
        self.verify(self._dummy_hash, secret)

    # Argon2id is deliberately CPU- and memory-heavy (~100-250 ms). Running it on the event
    # loop would stall every other request, so async code uses these thread-offloaded
    # variants (argon2-cffi releases the GIL while hashing).
    async def ahash(self, secret: str) -> str:
        return await asyncio.to_thread(self.hash, secret)

    async def averify(self, encoded_hash: str, secret: str) -> bool:
        return await asyncio.to_thread(self.verify, encoded_hash, secret)

    async def adummy_verify(self, secret: str) -> None:
        await asyncio.to_thread(self.dummy_verify, secret)


_FILE_MAGIC = b"VWE1"


class DataCipher:
    """AES-256-GCM with a key id prefix so keys can be rotated.

    Format: ``v1.<key_id>.<base64url(nonce || ciphertext || tag)>``.
    """

    def __init__(self, keys: dict[str, bytes], current_key_id: str) -> None:
        if current_key_id not in keys:
            raise CryptoError("current key id is not in the key set")
        for key_id, key in keys.items():
            if "." in key_id or not key_id:
                raise CryptoError("key ids must be non-empty and contain no '.'")
            if len(key) != KEY_BYTES:
                raise CryptoError("AES-256-GCM keys must be 32 bytes")
        self._keys = {k: AESGCM(v) for k, v in keys.items()}
        self._current = current_key_id

    @classmethod
    def from_setting(cls, encoded_key: str, key_id: str = "k1") -> DataCipher:
        return cls({key_id: decode_key(encoded_key)}, key_id)

    def encrypt(self, plaintext: str, *, associated_data: str = "") -> str:
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = self._keys[self._current].encrypt(
            nonce, plaintext.encode(), associated_data.encode()
        )
        token = base64.urlsafe_b64encode(nonce + sealed).decode().rstrip("=")
        return f"v1.{self._current}.{token}"

    def encrypt_bytes(self, plaintext: bytes, *, associated_data: str = "") -> bytes:
        """Binary form for files: ``VWE1 | len(key_id) | key_id | nonce | ciphertext+tag``."""
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = self._keys[self._current].encrypt(nonce, plaintext, associated_data.encode())
        key_id = self._current.encode()
        return _FILE_MAGIC + bytes([len(key_id)]) + key_id + nonce + sealed

    def decrypt_bytes(self, blob: bytes, *, associated_data: str = "") -> bytes:
        if not blob.startswith(_FILE_MAGIC) or len(blob) < len(_FILE_MAGIC) + 1:
            raise CryptoError("malformed encrypted file")
        offset = len(_FILE_MAGIC)
        key_len = blob[offset]
        key_id = blob[offset + 1 : offset + 1 + key_len].decode(errors="replace")
        if key_id not in self._keys:
            raise CryptoError("unknown key id")
        body = blob[offset + 1 + key_len :]
        try:
            return self._keys[key_id].decrypt(
                body[:NONCE_BYTES], body[NONCE_BYTES:], associated_data.encode()
            )
        except (InvalidTag, ValueError) as exc:
            raise CryptoError("encrypted file cannot be decrypted") from exc

    def decrypt(self, token: str, *, associated_data: str = "") -> str:
        try:
            version, key_id, body = token.split(".", 2)
        except ValueError as exc:
            raise CryptoError("malformed ciphertext") from exc
        if version != "v1" or key_id not in self._keys:
            raise CryptoError("unknown ciphertext version or key id")
        try:
            raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            plaintext = self._keys[key_id].decrypt(
                raw[:NONCE_BYTES], raw[NONCE_BYTES:], associated_data.encode()
            )
        except (InvalidTag, binascii.Error, ValueError) as exc:
            raise CryptoError("ciphertext cannot be decrypted") from exc
        return plaintext.decode()
