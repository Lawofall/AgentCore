"""Outbound mail — Protocol + console sink + optional generic SMTP."""

from agentcore.mail.sender import (
    ConsoleEmailSender,
    EmailSender,
    EmailSendError,
    build_email_sender,
    is_smtp_configured,
)

__all__ = [
    "ConsoleEmailSender",
    "EmailSendError",
    "EmailSender",
    "build_email_sender",
    "is_smtp_configured",
]
