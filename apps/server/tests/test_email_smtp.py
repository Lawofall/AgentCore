"""Generic SMTP mailer — fake smtplib only, never dials the network."""

from __future__ import annotations

import smtplib

import pytest

from agentcore.config import settings
from agentcore.mail.sender import (
    ConsoleEmailSender,
    EmailSendError,
    build_email_sender,
    is_smtp_configured,
)
from agentcore.mail.smtp import SmtpEmailSender


@pytest.fixture(autouse=True)
def _reset_fake_smtp():
    FakeSMTP.last = None
    BoomSMTP.last = None
    yield
    FakeSMTP.last = None
    BoomSMTP.last = None


class FakeSMTP:
    last: FakeSMTP | None = None

    def __init__(self, host, port=0, timeout=None, **_kwargs):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.sent: list = []
        type(self).last = self

    def starttls(self, *args, **kwargs):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, msg, *args, **kwargs):
        self.sent.append(msg)
        return {}

    def quit(self):
        return (221, b"bye")

    def close(self):
        return None


class BoomSMTP(FakeSMTP):
    def send_message(self, msg, *args, **kwargs):
        raise smtplib.SMTPRecipientsRefused({"ada@example.com": (550, b"no")})


def _configure_smtp(monkeypatch, *, tls_mode: str = "ssl") -> None:
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.local")
    monkeypatch.setattr(settings, "smtp_port", 465 if tls_mode == "ssl" else 587)
    monkeypatch.setattr(settings, "smtp_username", "noreply@mail.example.com")
    monkeypatch.setattr(settings, "smtp_password", "smtp-secret")
    monkeypatch.setattr(settings, "smtp_from_address", "noreply@mail.example.com")
    monkeypatch.setattr(settings, "smtp_from_name", "AgentCore")
    monkeypatch.setattr(settings, "smtp_tls_mode", tls_mode)


def test_unconfigured_without_host_or_from(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_from_address", "a@b.com")
    assert is_smtp_configured() is False
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.local")
    monkeypatch.setattr(settings, "smtp_from_address", "")
    assert is_smtp_configured() is False
    assert isinstance(build_email_sender(), ConsoleEmailSender)


def test_factory_picks_smtp_when_configured(monkeypatch):
    _configure_smtp(monkeypatch)
    sender = build_email_sender()
    assert isinstance(sender, SmtpEmailSender)


async def test_smtp_ssl_sends_code_without_network(monkeypatch):
    _configure_smtp(monkeypatch, tls_mode="ssl")
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    await SmtpEmailSender().send_verification_code(
        to="ada@example.com",
        purpose="register",
        code="654321",
        ttl_seconds=600,
    )
    client = FakeSMTP.last
    assert client is not None
    assert client.host == "smtp.test.local"
    assert client.port == 465
    assert client.started_tls is False
    assert client.logged_in == ("noreply@mail.example.com", "smtp-secret")
    assert len(client.sent) == 1
    msg = client.sent[0]
    assert "654321" in msg.get_content()
    assert "ada@example.com" in msg["To"]
    assert "noreply@mail.example.com" in msg["From"]
    assert "注册验证码" in msg["Subject"]


async def test_smtp_starttls_then_login(monkeypatch):
    _configure_smtp(monkeypatch, tls_mode="starttls")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    await SmtpEmailSender().send_verification_code(
        to="ada@example.com",
        purpose="password_reset",
        code="111222",
        ttl_seconds=600,
    )
    client = FakeSMTP.last
    assert client is not None
    assert client.started_tls is True
    assert client.logged_in == ("noreply@mail.example.com", "smtp-secret")
    assert "111222" in client.sent[0].get_content()


async def test_smtp_none_skips_tls_and_optional_auth(monkeypatch):
    _configure_smtp(monkeypatch, tls_mode="none")
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_password", "")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    await SmtpEmailSender().send_verification_code(
        to="ada@example.com",
        purpose="email_verify",
        code="000111",
        ttl_seconds=120,
    )
    client = FakeSMTP.last
    assert client is not None
    assert client.started_tls is False
    assert client.logged_in is None
    assert "000111" in client.sent[0].get_content()


async def test_smtp_transport_error_is_email_send_error(monkeypatch):
    _configure_smtp(monkeypatch, tls_mode="ssl")
    monkeypatch.setattr(smtplib, "SMTP_SSL", BoomSMTP)
    with pytest.raises(EmailSendError, match="smtp send failed"):
        await SmtpEmailSender().send_verification_code(
            to="ada@example.com",
            purpose="register",
            code="654321",
            ttl_seconds=600,
        )
