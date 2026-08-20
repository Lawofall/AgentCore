"""Email-code helpers: generate, hash, normalize, legacy-register gate."""

import pytest

from agentcore.auth.email_codes import (
    CODE_LENGTH,
    generate_email_code,
    hash_email_code,
    is_legacy_register_enabled,
    normalize_email,
    verify_email_code,
)
from agentcore.config import settings
from agentcore.core.errors import ValidationError


def test_generate_email_code_is_six_digits():
    code = generate_email_code()
    assert len(code) == CODE_LENGTH
    assert code.isdigit()


def test_hash_email_code_is_not_plaintext():
    code = "123456"
    hashed = hash_email_code(code)
    assert hashed != code
    assert verify_email_code(code, hashed) is True
    assert verify_email_code("000000", hashed) is False


def test_verify_email_code_dummy_when_missing():
    assert verify_email_code("123456", None) is False


def test_normalize_email_strips_and_lowers():
    assert normalize_email("  Ada@Example.COM ") == "ada@example.com"


@pytest.mark.parametrize("raw", ["", "   ", "not-an-email", "a@b", "@x.com", "a@"])
def test_normalize_email_rejects_junk(raw):
    with pytest.raises(ValidationError, match="有效邮箱"):
        normalize_email(raw)


def test_legacy_register_ignores_debug(monkeypatch):
    monkeypatch.setattr(settings, "legacy_register_enabled", False)
    monkeypatch.setattr(settings, "debug", False)
    assert is_legacy_register_enabled() is False
    monkeypatch.setattr(settings, "debug", True)
    assert is_legacy_register_enabled() is False


def test_legacy_register_explicit_override(monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "legacy_register_enabled", True)
    assert is_legacy_register_enabled() is True
    monkeypatch.setattr(settings, "legacy_register_enabled", False)
    monkeypatch.setattr(settings, "debug", True)
    assert is_legacy_register_enabled() is False
