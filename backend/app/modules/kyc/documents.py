"""KYC document files: validation, sanitising and encrypted storage (security.md §5).

* Images (JPEG/PNG) are decoded and re-encoded with Pillow: the bytes we keep are pixels we
  produced, so metadata (EXIF, GPS) and anything appended to the file are gone, and a file
  that only claims to be an image is rejected.
* PDFs are kept as uploaded but refused when they contain active content markers, and are
  only ever served as a download.
* Files are encrypted (AES-256-GCM, associated data = storage key) before they touch disk and
  live outside the database under random keys.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image, UnidentifiedImageError

from app.core.crypto import DataCipher
from app.core.errors import ValidationFailedError, register_titles

register_titles(
    {
        "KYC_FILE_INVALID": {
            "vi": "Tệp không hợp lệ (chỉ nhận JPEG, PNG hoặc PDF, tối đa 5 MB)",
            "en": "Invalid file (JPEG, PNG or PDF up to 5 MB only)",
        },
        "KYC_FILE_TOO_LARGE": {"vi": "Tệp lớn hơn 5 MB", "en": "The file is larger than 5 MB"},
    }
)

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000  # decompression-bomb guard (~ 8000 x 5000)
MAX_SIDE = 4000  # images are scaled down to this before storage
_PDF_ACTIVE = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFile|OpenAction|AA|RichMedia)\b")

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class KycFileInvalidError(ValidationFailedError):
    code = "KYC_FILE_INVALID"


class KycFileTooLargeError(ValidationFailedError):
    code = "KYC_FILE_TOO_LARGE"


@dataclass(frozen=True, slots=True)
class CleanFile:
    content: bytes
    content_type: str
    sha256: str


def _clean_image(content: bytes) -> CleanFile:
    try:
        with Image.open(io.BytesIO(content)) as probe:
            probe.verify()  # structure check; the object is unusable afterwards
        with Image.open(io.BytesIO(content)) as img:
            fmt = img.format
            if fmt not in ("JPEG", "PNG"):
                raise KycFileInvalidError()
            img.load()
            if max(img.size) > MAX_SIDE:
                img.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            if fmt == "JPEG":
                rgb = img.convert("RGB")
                rgb.save(out, format="JPEG", quality=90, optimize=True)  # no exif= ⇒ none kept
                content_type = "image/jpeg"
            else:
                mode = img.mode if img.mode in ("RGB", "RGBA", "L", "LA") else "RGBA"
                img.convert(mode).save(out, format="PNG", optimize=True)
                content_type = "image/png"
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise KycFileInvalidError() from exc
    data = out.getvalue()
    return CleanFile(data, content_type, hashlib.sha256(data).hexdigest())


def clean(content: bytes) -> CleanFile:
    """Validate and sanitise one upload. CPU-bound: call through ``aclean``."""
    if len(content) > MAX_BYTES:
        raise KycFileTooLargeError()
    if not content:
        raise KycFileInvalidError()
    if content.startswith(b"%PDF-"):
        if _PDF_ACTIVE.search(content) or b"%%EOF" not in content[-1024:]:
            raise KycFileInvalidError()
        return CleanFile(content, "application/pdf", hashlib.sha256(content).hexdigest())
    return _clean_image(content)


async def aclean(content: bytes) -> CleanFile:
    return await asyncio.to_thread(clean, content)


class DocumentStore(Protocol):
    async def put(self, data: bytes) -> str: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...


_KEY = re.compile(r"^[0-9a-f]{40}$")


class LocalEncryptedStore:
    """Encrypted files on a local volume (development/simulation). S3-compatible later."""

    def __init__(self, root: Path, cipher: DataCipher) -> None:
        self._root = root
        self._cipher = cipher

    def _path(self, key: str) -> Path:
        if not _KEY.match(key):  # never let a stored key escape the root directory
            raise ValueError("invalid storage key")
        return self._root / key[:2] / f"{key}.bin"

    def _write(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        sealed = self._cipher.encrypt_bytes(data, associated_data=f"kyc:{key}")
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(sealed)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def put(self, data: bytes) -> str:
        key = secrets.token_hex(20)
        await asyncio.to_thread(self._write, key, data)
        return key

    async def get(self, key: str) -> bytes:
        sealed = await asyncio.to_thread(self._path(key).read_bytes)
        return self._cipher.decrypt_bytes(sealed, associated_data=f"kyc:{key}")

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)
