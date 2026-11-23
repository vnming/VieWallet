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
from app.modules.notifications.inapp import Notification, register_inapp_handlers

__all__ = [
    "EMAIL_TOPIC",
    "EmailSender",
    "Notification",
    "OutgoingEmail",
    "RecordingEmailSender",
    "SmtpEmailSender",
    "register_handlers",
    "register_inapp_handlers",
    "request_email",
]
