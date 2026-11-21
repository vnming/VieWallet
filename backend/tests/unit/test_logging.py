from __future__ import annotations

import json
import logging

import pytest
import structlog

from app.core.logging import REDACTED, configure_logging, redact, redaction_processor


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "new_password",
        "pin",
        "transaction_pin",
        "access_token",
        "refreshToken",
        "Authorization",
        "set-cookie",
        "api_key",
        "totp_code",
        "client_secret",
        "account_number",
    ],
)
def test_sensitive_keys_are_redacted(key: str) -> None:
    event = redaction_processor(None, "info", {"event": "x", key: "s3cr3t"})
    assert event[key] == REDACTED


@pytest.mark.parametrize("key", ["shipping", "spinner", "pin_set", "opinion", "amount", "user_id"])
def test_safe_keys_are_kept(key: str) -> None:
    event = redaction_processor(None, "info", {"event": "x", key: "value"})
    assert event[key] == "value"


def test_nested_structures_are_redacted() -> None:
    value = {"user": {"email": "a@b.c", "password": "p"}, "items": [{"token": "t"}, ("pin", 1)]}
    assert redact(value) == {
        "user": {"email": "a@b.c", "password": REDACTED},
        "items": [{"token": REDACTED}, ("pin", 1)],
    }


def test_event_name_is_never_redacted() -> None:
    event = redaction_processor(None, "info", {"event": "password_changed"})
    assert event["event"] == "password_changed"


def test_json_output_contains_redacted_values(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json=True)
    try:
        structlog.get_logger("test").info("login_attempt", email="a@b.c", password="hunter2")
        line = capsys.readouterr().out.strip().splitlines()[-1]
        record = json.loads(line)
        assert record["event"] == "login_attempt"
        assert record["password"] == REDACTED
        assert record["email"] == "a@b.c"
        assert record["level"] == "info"
        assert "ts" in record
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()
