"""Generic SMTP transport for verification mail.

stdlib ``smtplib`` only — no vendor SDK. Aliyun DirectMail / Tencent SES /
SendCloud all speak SMTP; swapping providers is a config change.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.headerregistry import Address
from email.message import EmailMessage

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.mail.sender import (
    EmailSendError,
    format_verification_body,
    verification_subject,
)

logger = get_logger(__name__)

_SMTP_TIMEOUT_SECONDS = 15.0
_TLS_MODES = frozenset({"ssl", "starttls", "none"})


def _from_header(address: str, display_name: str) -> str:
    local, _, domain = address.partition("@")
    if not local or not domain:
        raise EmailSendError("smtp from address is invalid")
    name = display_name.strip()
    return str(Address(display_name=name, username=local, domain=domain))


def _send_sync(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    tls_mode: str,
    from_header: str,
    to: str,
    subject: str,
    body: str,
) -> None:
    message = EmailMessage()
    message["From"] = from_header
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    timeout = _SMTP_TIMEOUT_SECONDS
    if tls_mode == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout)
    else:
        client = smtplib.SMTP(host, port, timeout=timeout)
    try:
        if tls_mode == "starttls":
            client.starttls()
        if username:
            client.login(username, password)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except smtplib.SMTPException:
            client.close()


class SmtpEmailSender:
    """Hand off a verification code via configured SMTP."""

    async def send_verification_code(
        self,
        *,
        to: str,
        purpose: str,
        code: str,
        ttl_seconds: int,
    ) -> None:
        host = settings.smtp_host.strip()
        from_addr = settings.smtp_from_address.strip()
        tls_mode = settings.smtp_tls_mode
        if tls_mode not in _TLS_MODES:
            raise EmailSendError("smtp tls mode is invalid")
        subject = verification_subject(purpose)
        body = format_verification_body(
            purpose=purpose, code=code, ttl_seconds=ttl_seconds
        )
        try:
            from_header = _from_header(from_addr, settings.smtp_from_name)
            await asyncio.to_thread(
                _send_sync,
                host=host,
                port=settings.smtp_port,
                username=settings.smtp_username.strip(),
                password=settings.smtp_password,
                tls_mode=tls_mode,
                from_header=from_header,
                to=to,
                subject=subject,
                body=body,
            )
        except EmailSendError:
            raise
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            logger.warning(
                "email.send_failed",
                purpose=purpose,
                error=type(exc).__name__,
            )
            raise EmailSendError("smtp send failed") from exc
        logger.info("email.sent", purpose=purpose)
