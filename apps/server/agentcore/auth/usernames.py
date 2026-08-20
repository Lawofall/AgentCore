"""Username policy: handles must never contain ``@``.

``@`` is reserved so ``/login`` and ``/token`` can treat the same ``username``
field as either a normalized email or a handle without ambiguity.
"""

from __future__ import annotations

import re
import secrets

from agentcore.core.errors import ValidationError

HANDLE_PREFIX = "user_"
_HANDLE_SUFFIX_BYTES = 4  # 8 hex chars → ``user_`` + 8 = 13
ALLOCATE_ATTEMPTS = 16
USERNAME_MIN_LEN = 3
USERNAME_MAX_LEN = 32
USERNAME_COOLDOWN_DAYS = 14

RESERVED_USERNAMES = frozenset(
    {"admin", "official", "agentcore", "support", "system"}
)

_USERNAME_CLAIM_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")


def generate_username_handle() -> str:
    """Neutral public handle. Never derived from an email local-part."""
    return f"{HANDLE_PREFIX}{secrets.token_hex(_HANDLE_SUFFIX_BYTES)}"


def normalize_username_for_storage(username: str) -> str:
    """Canonical lowercase form stored in ``users.username``."""
    return username.strip().lower()


def is_generated_handle(username: str) -> bool:
    """True when ``username`` is a system-allocated ``user_`` + 8 hex handle."""
    lowered = normalize_username_for_storage(username)
    if not lowered.startswith(HANDLE_PREFIX):
        return False
    suffix = lowered[len(HANDLE_PREFIX):]
    return len(suffix) == 8 and all(c in "0123456789abcdef" for c in suffix)


def assert_username_allowed(username: str) -> str:
    """Strip and reject empty / ``@``-containing handles (login routing)."""
    username = username.strip()
    if not username:
        raise ValidationError("请输入用户名")
    if "@" in username:
        raise ValidationError("用户名不能包含 @")
    return username


def validate_username_for_claim(username: str) -> str:
    """Validate a self-selected username before persistence."""
    username = normalize_username_for_storage(username)
    if len(username) < USERNAME_MIN_LEN or len(username) > USERNAME_MAX_LEN:
        raise ValidationError("用户名长度须为 3–32 个字符")
    if "@" in username:
        raise ValidationError("用户名不能包含 @")
    if username.startswith(HANDLE_PREFIX):
        raise ValidationError("用户名不能使用 user_ 前缀")
    if username in RESERVED_USERNAMES:
        raise ValidationError("该用户名不可用")
    if not _USERNAME_CLAIM_RE.fullmatch(username):
        raise ValidationError(
            "用户名仅可使用字母、数字、点、下划线或连字符，且须以字母或数字开头和结尾"
        )
    return username
