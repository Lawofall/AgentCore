"""Username handle policy: no ``@``, claim rules, system handles."""

import pytest

from agentcore.auth.usernames import (
    RESERVED_USERNAMES,
    assert_username_allowed,
    generate_username_handle,
    is_generated_handle,
    normalize_username_for_storage,
    validate_username_for_claim,
)
from agentcore.core.errors import ValidationError


def test_generate_username_handle_is_neutral():
    handle = generate_username_handle()
    assert handle.startswith("user_")
    assert "@" not in handle
    assert len(handle) == len("user_") + 8


def test_is_generated_handle():
    assert is_generated_handle("user_a3f90d12")
    assert is_generated_handle("USER_A3F90D12")
    assert not is_generated_handle("alice")
    assert not is_generated_handle("user_short")
    assert not is_generated_handle("user_a3f90d12extra")


def test_normalize_username_for_storage():
    assert normalize_username_for_storage("  Alice  ") == "alice"


def test_assert_username_allowed_rejects_at():
    with pytest.raises(ValidationError, match="@"):
        assert_username_allowed("ada@example.com")


def test_assert_username_allowed_strips():
    assert assert_username_allowed("  ada  ") == "ada"


def test_validate_username_for_claim_accepts_valid():
    assert validate_username_for_claim("alice") == "alice"
    assert validate_username_for_claim("a.b_c-1") == "a.b_c-1"


def test_validate_username_for_claim_rejects_length():
    with pytest.raises(ValidationError, match="3"):
        validate_username_for_claim("ab")
    with pytest.raises(ValidationError, match="3"):
        validate_username_for_claim("a" * 33)


def test_validate_username_for_claim_rejects_at_and_prefix():
    with pytest.raises(ValidationError, match="@"):
        validate_username_for_claim("a@b")
    with pytest.raises(ValidationError, match="user_"):
        validate_username_for_claim("user_abc12345")


def test_validate_username_for_claim_rejects_reserved():
    for name in RESERVED_USERNAMES:
        with pytest.raises(ValidationError, match="不可用"):
            validate_username_for_claim(name)


def test_validate_username_for_claim_rejects_bad_chars():
    with pytest.raises(ValidationError, match="字母"):
        validate_username_for_claim(".alice")
    with pytest.raises(ValidationError, match="字母"):
        validate_username_for_claim("alice-")

