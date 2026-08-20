"""Outbound verification mail.

A Protocol plus two sinks: console/dev, and (when configured) generic SMTP.
No provider SDK — swapping Aliyun / Tencent SES / SendCloud is config.
Unconfigured SMTP keeps the old sink: DEBUG prints the body; non-DEBUG logs
``email.unconfigured`` and never raises, so local/CI signup is not blocked.
"""

from typing import Protocol

from agentcore.config import settings
from agentcore.core.logging import get_logger

logger = get_logger(__name__)

PURPOSE_REGISTER = "register"
PURPOSE_PASSWORD_RESET = "password_reset"
PURPOSE_EMAIL_VERIFY = "email_verify"

_SUBJECTS = {
    PURPOSE_REGISTER: "注册验证码",
    PURPOSE_PASSWORD_RESET: "重置密码验证码",
    PURPOSE_EMAIL_VERIFY: "邮箱验证码",
}


class EmailSendError(Exception):
    """Transport accepted the request but could not hand off the message."""


class EmailSender(Protocol):
    async def send_verification_code(
        self,
        *,
        to: str,
        purpose: str,
        code: str,
        ttl_seconds: int,
    ) -> None: ...


def verification_subject(purpose: str) -> str:
    return _SUBJECTS.get(purpose, "验证码")


def format_verification_body(*, purpose: str, code: str, ttl_seconds: int) -> str:
    minutes = max(1, ttl_seconds // 60)
    label = verification_subject(purpose)
    return f"您的{label}是 {code}，{minutes} 分钟内有效。请勿泄露给他人。"


def is_smtp_configured() -> bool:
    """Host + from-address are the minimum to attempt a real send."""
    return bool(settings.smtp_host.strip() and settings.smtp_from_address.strip())


def build_email_sender() -> EmailSender:
    if is_smtp_configured():
        from agentcore.mail.smtp import SmtpEmailSender

        return SmtpEmailSender()
    return ConsoleEmailSender()


class ConsoleEmailSender:
    """Dev sink: print + structured log. Production: log unconfigured, no code."""

    async def send_verification_code(
        self,
        *,
        to: str,
        purpose: str,
        code: str,
        ttl_seconds: int,
    ) -> None:
        subject = verification_subject(purpose)
        body = format_verification_body(
            purpose=purpose, code=code, ttl_seconds=ttl_seconds
        )
        if settings.debug:
            logger.info(
                "email.dev_outbound",
                to=to,
                purpose=purpose,
                subject=subject,
                body=body,
            )
            print(f"[agentcore mail] to={to} subject={subject}\n{body}", flush=True)
            return
        logger.info("email.unconfigured", purpose=purpose, subject=subject)
