"""Console mailer: debug prints the body; production never logs the code."""

from agentcore.config import settings
from agentcore.mail.sender import ConsoleEmailSender, format_verification_body


def test_format_verification_body_includes_code_and_ttl():
    body = format_verification_body(purpose="register", code="123456", ttl_seconds=600)
    assert "123456" in body
    assert "10 分钟" in body


async def test_console_sender_logs_body_in_debug(monkeypatch, capsys):
    monkeypatch.setattr(settings, "debug", True)
    await ConsoleEmailSender().send_verification_code(
        to="ada@example.com",
        purpose="register",
        code="654321",
        ttl_seconds=600,
    )
    captured = capsys.readouterr()
    assert "654321" in captured.out
    assert "ada@example.com" in captured.out


async def test_console_sender_omits_code_when_not_debug(monkeypatch, capsys):
    monkeypatch.setattr(settings, "debug", False)
    await ConsoleEmailSender().send_verification_code(
        to="ada@example.com",
        purpose="register",
        code="654321",
        ttl_seconds=600,
    )
    captured = capsys.readouterr()
    assert "654321" not in captured.out
