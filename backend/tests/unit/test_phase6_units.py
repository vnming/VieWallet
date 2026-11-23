"""Phase 6 pure pieces: every risk rule with its evidence, rule validation and versions,
KYC file cleaning (EXIF removal, fake images, active PDFs), encrypted file storage, links."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from app.core.crypto import CryptoError, DataCipher
from app.core.errors import ValidationFailedError
from app.modules.kyc.documents import (
    MAX_BYTES,
    KycFileInvalidError,
    KycFileTooLargeError,
    LocalEncryptedStore,
    clean,
)
from app.modules.kyc.models import DocumentKind, DocumentType
from app.modules.kyc.service import (
    KycKeys,
    KycLinkExpiredError,
    SubmissionInput,
    _validate,
    check_link,
    mask_document_number,
    required_kinds,
    sign_link,
)
from app.modules.risk.domain import (
    DEFAULT_CONFIGS,
    Decision,
    RiskContext,
    RuleConfig,
    evaluate,
    rules_fingerprint,
    validate_config,
)

NOW = datetime(2026, 11, 23, 3, 0, tzinfo=UTC)  # 10:00 in Ho Chi Minh City
USER = uuid.uuid4()
CONFIG = {c.code: c for c in DEFAULT_CONFIGS}


def ctx(**overrides: object) -> RiskContext:
    values: dict[str, object] = {
        "user_id": USER,
        "txn_type": "TRANSFER",
        "amount_minor": 100_000,
        "currency": "VND",
        "now": NOW,
    }
    values.update(overrides)
    return RiskContext(**values)  # type: ignore[arg-type]


def hit(code: str, context: RiskContext) -> dict[str, str] | None:
    """Evidence of one rule alone (None when it does not match)."""
    result = evaluate(context, (CONFIG[code],))
    assert len(result.hits) <= 1
    return result.hits[0].evidence if result.hits else None


# ---------------------------------------------------------------- every rule, both sides


def test_velocity_count() -> None:
    assert hit("VELOCITY_COUNT", ctx(outgoing_count_10m=10)) == {"count_10m": "11"}
    assert hit("VELOCITY_COUNT", ctx(outgoing_count_10m=9)) is None


def test_velocity_amount() -> None:
    evidence = hit(
        "VELOCITY_AMOUNT",
        ctx(outgoing_amount_1h_minor=2_400_000, amount_minor=100_001, daily_limit_minor=5_000_000),
    )
    assert evidence == {"amount_1h": "2500001", "daily_limit": "5000000"}
    assert (
        hit(
            "VELOCITY_AMOUNT",
            ctx(outgoing_amount_1h_minor=2_400_000, daily_limit_minor=5_000_000),
        )
        is None
    )  # exactly 50 %
    assert hit("VELOCITY_AMOUNT", ctx(outgoing_amount_1h_minor=9**9)) is None  # no limit known


def test_amount_spike() -> None:
    assert hit("AMOUNT_SPIKE", ctx(amount_minor=2_500_001, median_outgoing_90d_minor=500_000)) == {
        "amount": "2500001",
        "median_90d": "500000",
    }
    assert hit("AMOUNT_SPIKE", ctx(amount_minor=2_500_000, median_outgoing_90d_minor=500_000)) is (
        None
    )  # exactly 5x
    assert hit("AMOUNT_SPIKE", ctx(amount_minor=1_900_000, median_outgoing_90d_minor=1)) is None
    assert hit("AMOUNT_SPIKE", ctx(amount_minor=9_000_000)) is None  # no history


def test_new_recipient_large() -> None:
    assert hit(
        "NEW_RECIPIENT_LARGE", ctx(amount_minor=5_000_001, first_time_counterparty=True)
    ) == {"amount": "5000001"}
    assert hit("NEW_RECIPIENT_LARGE", ctx(amount_minor=5_000_001)) is None
    assert (
        hit("NEW_RECIPIENT_LARGE", ctx(amount_minor=5_000_000, first_time_counterparty=True))
        is None
    )


def test_credential_change_cooldown_blocks_withdrawals_only() -> None:
    changed = NOW - timedelta(hours=1)
    transfer = evaluate(
        ctx(amount_minor=2_000_001, credentials_changed_at=changed),
        (CONFIG["CREDENTIAL_CHANGE_COOLDOWN"],),
    )
    assert transfer.hits[0].evidence == {"changed_at": changed.isoformat()}
    assert transfer.decision is Decision.ALLOW  # 50 < 70
    withdrawal = evaluate(
        ctx(txn_type="WITHDRAWAL", amount_minor=2_000_001, credentials_changed_at=changed),
        (CONFIG["CREDENTIAL_CHANGE_COOLDOWN"],),
    )
    assert withdrawal.decision is Decision.BLOCK
    old = ctx(amount_minor=9_000_000, credentials_changed_at=NOW - timedelta(hours=24))
    assert hit("CREDENTIAL_CHANGE_COOLDOWN", old) is None


def test_new_destination_cooldown() -> None:
    added = NOW - timedelta(minutes=59)
    result = evaluate(ctx(destination_added_at=added), (CONFIG["NEW_DESTINATION_COOLDOWN"],))
    assert result.decision is Decision.BLOCK
    assert result.hits[0].evidence == {"added_at": added.isoformat()}
    assert hit("NEW_DESTINATION_COOLDOWN", ctx(destination_added_at=NOW - timedelta(hours=1))) is (
        None
    )


def test_repeated_failures_and_duplicates() -> None:
    assert hit("REPEATED_FAILURES", ctx(failed_count_30m=3)) == {"failed_30m": "3"}
    assert hit("REPEATED_FAILURES", ctx(failed_count_30m=2)) is None
    wallet = uuid.uuid4()
    assert hit(
        "DUPLICATE_PATTERN", ctx(counterparty_wallet_id=wallet, same_amount_same_counterparty_2m=1)
    ) == {"same_in_2m": "1"}
    assert hit("DUPLICATE_PATTERN", ctx(same_amount_same_counterparty_2m=1)) is None


def test_watchlist_flag() -> None:
    assert hit("ACCOUNT_FLAG_WATCHLIST", ctx(flags=frozenset({"WATCHLIST"}))) == {
        "flag": "WATCHLIST"
    }
    assert hit("ACCOUNT_FLAG_WATCHLIST", ctx(flags=frozenset({"DUPLICATE_DOCUMENT"}))) is None


def test_new_account_large() -> None:
    created = NOW - timedelta(days=6, hours=23)
    assert hit("NEW_ACCOUNT_LARGE", ctx(amount_minor=5_000_001, account_created_at=created)) == {
        "account_created_at": created.isoformat(),
        "amount": "5000001",
    }
    week_old = NOW - timedelta(days=7)
    assert hit("NEW_ACCOUNT_LARGE", ctx(amount_minor=9_000_000, account_created_at=week_old)) is (
        None
    )
    assert hit("NEW_ACCOUNT_LARGE", ctx(amount_minor=5_000_000, account_created_at=created)) is (
        None
    )


def test_night_large_uses_local_time() -> None:
    night = datetime(2026, 11, 22, 20, 30, tzinfo=UTC)  # 03:30 in Ho Chi Minh City
    assert hit("NIGHT_LARGE", ctx(now=night, amount_minor=10_000_001)) == {
        "local_hour": "3",
        "amount": "10000001",
    }
    assert hit("NIGHT_LARGE", ctx(amount_minor=10_000_001)) is None  # 10:00 local
    five = datetime(2026, 11, 22, 22, 0, tzinfo=UTC)  # 05:00 local: window ended
    assert hit("NIGHT_LARGE", ctx(now=five, amount_minor=10_000_001)) is None


def test_scores_add_up_to_review() -> None:
    result = evaluate(
        ctx(
            amount_minor=6_000_000,
            account_created_at=NOW - timedelta(days=1),
            credentials_changed_at=NOW - timedelta(hours=1),
        )
    )
    assert {h.code for h in result.hits} == {"NEW_ACCOUNT_LARGE", "CREDENTIAL_CHANGE_COOLDOWN"}
    assert (result.score, result.decision) == (80, Decision.REVIEW)


# ---------------------------------------------------------------- configurable rules


def test_versions_change_knobs_and_fingerprint() -> None:
    stricter = RuleConfig("VELOCITY_COUNT", 70, params={"max_count": 3}, version=2)
    result = evaluate(ctx(outgoing_count_10m=3), (stricter,))
    assert result.decision is Decision.REVIEW
    off = RuleConfig("VELOCITY_COUNT", 70, enabled=False, params={"max_count": 3}, version=3)
    assert evaluate(ctx(outgoing_count_10m=3), (off,)).hits == ()
    assert rules_fingerprint((stricter,)) != rules_fingerprint((off,))
    assert rules_fingerprint(DEFAULT_CONFIGS).startswith("rs-")


@pytest.mark.parametrize(
    "config",
    [
        RuleConfig("NOPE", 10),
        RuleConfig("VELOCITY_COUNT", 101, params={"max_count": 1}),
        RuleConfig("VELOCITY_COUNT", 10, params={}),
        RuleConfig("VELOCITY_COUNT", 10, params={"max_count": 1, "extra": 1}),
        RuleConfig("VELOCITY_COUNT", 10, params={"max_count": -1}),
    ],
)
def test_invalid_configs_are_refused(config: RuleConfig) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - message varies by case
        validate_config(config)


def test_defaults_are_valid() -> None:
    for config in DEFAULT_CONFIGS:
        validate_config(config)


# ---------------------------------------------------------------- KYC files


def _jpeg_with_exif() -> bytes:
    img = Image.new("RGB", (64, 48), (200, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = "SimCam"  # Make
    exif[0x8825] = {2: (10.0, 46.0, 30.0)}  # GPS info
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


def test_images_are_re_encoded_without_metadata() -> None:
    original = _jpeg_with_exif()
    assert b"SimCam" in original
    cleaned = clean(original + b"<script>trailing junk</script>")
    assert cleaned.content_type == "image/jpeg"
    assert b"SimCam" not in cleaned.content
    assert b"<script>" not in cleaned.content
    with Image.open(io.BytesIO(cleaned.content)) as img:
        assert not img.getexif()
        assert img.size == (64, 48)
    png = io.BytesIO()
    Image.new("RGBA", (10, 10)).save(png, format="PNG")
    assert clean(png.getvalue()).content_type == "image/png"


def test_large_images_are_scaled_down() -> None:
    out = io.BytesIO()
    Image.new("L", (5000, 100)).save(out, format="PNG")
    with Image.open(io.BytesIO(clean(out.getvalue()).content)) as img:
        assert max(img.size) == 4000


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"GIF89a....",
        b"\xff\xd8\xff\xe0not really a jpeg",
        b"%PDF-1.7\n1 0 obj << /OpenAction 2 0 R >> endobj\n%%EOF",
        b"%PDF-1.7\n1 0 obj << /S /JavaScript /JS (app.alert(1)) >> endobj\n%%EOF",
        b"%PDF-1.7\n no end marker",
    ],
)
def test_bad_files_are_refused(content: bytes) -> None:
    with pytest.raises(KycFileInvalidError):
        clean(content)


def test_size_and_pdf() -> None:
    with pytest.raises(KycFileTooLargeError):
        clean(b"x" * (MAX_BYTES + 1))
    pdf = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer\n%%EOF\n"
    assert clean(pdf).content_type == "application/pdf"


async def test_store_encrypts_at_rest(tmp_path: Path) -> None:
    cipher = DataCipher({"k1": b"\x01" * 32}, "k1")
    store = LocalEncryptedStore(tmp_path, cipher)
    key = await store.put(b"fictional document pixels")
    files = list(tmp_path.rglob("*.bin"))  # noqa: ASYNC240 - test inspection
    assert len(files) == 1
    assert b"fictional" not in files[0].read_bytes()
    assert await store.get(key) == b"fictional document pixels"
    files[0].write_bytes(files[0].read_bytes()[:-1] + b"\x00")  # tampered
    with pytest.raises(CryptoError):
        await store.get(key)
    with pytest.raises(ValueError, match="invalid storage key"):
        await store.get("../../etc/passwd")


# ---------------------------------------------------------------- KYC rules


def test_submission_validation() -> None:
    good = SubmissionInput(
        "Nguyễn Văn An",
        date(1990, 5, 1),
        DocumentType.NATIONAL_ID,
        "012345678901",
        "12 Đường Hư Cấu, Quận Mô Phỏng",
    )
    _validate(good, date(2026, 11, 23))
    for bad in (
        SubmissionInput("An", good.date_of_birth, good.document_type, good.document_number, "x"),
        SubmissionInput(
            good.full_name, date(2010, 1, 1), good.document_type, good.document_number, good.address
        ),
        SubmissionInput(
            good.full_name, good.date_of_birth, DocumentType.PASSPORT, "012345678901", good.address
        ),
    ):
        with pytest.raises(ValidationFailedError):
            _validate(bad, date(2026, 11, 23))
    assert mask_document_number("012345678901") == "••••••••8901"
    assert required_kinds(DocumentType.PASSPORT) == {DocumentKind.FRONT, DocumentKind.SELFIE}
    assert DocumentKind.BACK in required_kinds(DocumentType.NATIONAL_ID)


def test_document_links_are_bound_and_short_lived() -> None:
    keys = KycKeys(DataCipher({"k1": b"\x02" * 32}, "k1"), b"l" * 32, b"s" * 32)
    doc, viewer = uuid.uuid4(), uuid.uuid4()
    exp, sig = sign_link(keys, doc, viewer, NOW, timedelta(minutes=5))
    check_link(keys, doc, viewer_id=viewer, exp=exp, signature=sig, now=NOW)
    with pytest.raises(KycLinkExpiredError):
        check_link(keys, doc, viewer_id=uuid.uuid4(), exp=exp, signature=sig, now=NOW)
    with pytest.raises(KycLinkExpiredError):
        check_link(
            keys, doc, viewer_id=viewer, exp=exp, signature=sig, now=NOW + timedelta(minutes=6)
        )
    with pytest.raises(KycLinkExpiredError):
        check_link(keys, doc, viewer_id=viewer, exp=exp + 600, signature=sig, now=NOW)
