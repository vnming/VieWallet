"""Structured JSON logging with secret/PII redaction (structlog + stdlib bridge)."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "***"
# A key is sensitive when one of its words (split on `_`, `-`, `.` and camelCase) is listed
# here, or when the whole key is listed in SENSITIVE_KEYS. Word matching avoids false
# positives such as "shipping" containing "pin".
SENSITIVE_WORDS = frozenset(
    {"password", "passwd", "pin", "secret", "token", "authorization", "cookie", "otp", "totp"}
)
SENSITIVE_KEYS = frozenset({"apikey", "api_key", "card_number", "account_number", "set_cookie"})
# Keys that contain a sensitive word but only carry a non-secret flag.
SAFE_KEYS = frozenset({"pin_set", "pin_locked"})

_WORD_SPLIT = re.compile(r"[_\-.\s]+|(?<=[a-z0-9])(?=[A-Z])")


def _is_sensitive(key: str) -> bool:
    normalized = key.replace("-", "_").lower()
    if normalized in SAFE_KEYS:
        return False
    if normalized in SENSITIVE_KEYS:
        return True
    return any(word.lower() in SENSITIVE_WORDS for word in _WORD_SPLIT.split(key) if word)


def redact(value: Any) -> Any:
    """Return a copy of ``value`` with sensitive mapping keys masked, recursively."""
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and _is_sensitive(k) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(redact(v) for v in value)
    return value


def redaction_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):
        if key == "event":
            continue
        if _is_sensitive(key):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = redact(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", *, json: bool = True) -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        redaction_processor,
    ]
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Our middleware writes access logs; silence duplicates from uvicorn.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
