"""6-digit email verification codes: generate, hash, normalize."""

from __future__ import annotations

import re
import secrets

from agentcore.core.errors import ValidationError
from agentcore.security import hash_password, verify_password

CODE_LENGTH = 6
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Timing equalizer when no challenge row exists (same posture as login).
_DUMMY_CODE_HASH = hash_password("000000")


def generate_email_code() -> str:
    return f"{secrets.randbelow(10**CODE_LENGTH):0{CODE_LENGTH}d}"


def hash_email_code(code: str) -> str:
    return hash_password(code)


def verify_email_code(code: str, stored_hash: str | None) -> bool:
    """Compare ``code`` to a stored hash, or to a dummy hash when none exists."""
    return verify_password(code, stored_hash or _DUMMY_CODE_HASH)


def normalize_email(raw: str | None) -> str:
    """Strip + lowercase. Rejects empty / oversized / obviously invalid shapes.

    No Gmail-dot / plus-alias folding — out of scope this round.
    """
    email = (raw or "").strip().lower()
    if not email or len(email) > 255 or _EMAIL_RE.match(email) is None:
        raise ValidationError("请输入有效邮箱")
    return email


def is_legacy_register_enabled() -> bool:
    """Public immediate-create ``/register`` hatch.

    Off unless ``LEGACY_REGISTER_ENABLED`` is set. Not coupled to DEBUG — a
    production triage ``DEBUG=true`` must not reopen unverified signup.
    """
    from agentcore.config import settings

    return bool(settings.legacy_register_enabled)
