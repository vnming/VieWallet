"""Public interface of the notifications module."""

from app.modules.notifications.email import (
    EMAIL_TOPIC,
    EmailSender,
    OutgoingEmail,
    RecordingEmailSender,
    SmtpEmailSender,
    register_handlers,
    request_email,
)

__all__ = [
    "EMAIL_TOPIC",
    "EmailSender",
    "OutgoingEmail",
    "RecordingEmailSender",
    "SmtpEmailSender",
    "register_handlers",
    "request_email",
]
