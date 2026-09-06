"""Security primitives: password hashing, JWT tokens, CSRF, refresh tokens,
at-rest secret encryption (BYOK API keys).

Split by concern under ``agentcore.security.*``. Historical
``from agentcore.security import X`` stays via lazy re-export so importing
``keys`` (sidecar git / BYOK) does not pull pwdlib or python-jose — those
belong to the cloud auth process, not the desktop turn path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentcore.security.csrf import (
        CsrfRejectReason,
        csrf_reject_reason,
        sign_csrf_token,
        verify_csrf_token,
    )
    from agentcore.security.keys import KeyEncryptor
    from agentcore.security.passwords import hash_password, verify_password
    from agentcore.security.refresh import (
        generate_refresh_token,
        generate_temp_password,
        hash_refresh_token,
    )
    from agentcore.security.tokens import (
        create_access_token,
        create_account_token,
        create_folders_token,
        create_inference_token,
        create_mfa_pending_token,
        create_preview_token,
        decode_access_token,
        decode_access_token_claims,
        decode_access_token_family,
        decode_access_token_mfa_verified,
        decode_account_token,
        decode_folders_token,
        decode_inference_token,
        decode_mfa_pending_token,
        decode_preview_token,
    )

__all__ = [
    "CsrfRejectReason",
    "KeyEncryptor",
    "create_access_token",
    "create_account_token",
    "create_folders_token",
    "create_inference_token",
    "create_mfa_pending_token",
    "create_preview_token",
    "csrf_reject_reason",
    "decode_access_token",
    "decode_access_token_claims",
    "decode_access_token_family",
    "decode_access_token_mfa_verified",
    "decode_account_token",
    "decode_folders_token",
    "decode_inference_token",
    "decode_mfa_pending_token",
    "decode_preview_token",
    "generate_refresh_token",
    "generate_temp_password",
    "hash_password",
    "hash_refresh_token",
    "sign_csrf_token",
    "verify_csrf_token",
    "verify_password",
]

_LAZY: dict[str, tuple[str, str]] = {
    "CsrfRejectReason": ("agentcore.security.csrf", "CsrfRejectReason"),
    "csrf_reject_reason": ("agentcore.security.csrf", "csrf_reject_reason"),
    "sign_csrf_token": ("agentcore.security.csrf", "sign_csrf_token"),
    "verify_csrf_token": ("agentcore.security.csrf", "verify_csrf_token"),
    "KeyEncryptor": ("agentcore.security.keys", "KeyEncryptor"),
    "hash_password": ("agentcore.security.passwords", "hash_password"),
    "verify_password": ("agentcore.security.passwords", "verify_password"),
    "generate_refresh_token": ("agentcore.security.refresh", "generate_refresh_token"),
    "generate_temp_password": ("agentcore.security.refresh", "generate_temp_password"),
    "hash_refresh_token": ("agentcore.security.refresh", "hash_refresh_token"),
    "create_access_token": ("agentcore.security.tokens", "create_access_token"),
    "create_account_token": ("agentcore.security.tokens", "create_account_token"),
    "create_folders_token": ("agentcore.security.tokens", "create_folders_token"),
    "create_inference_token": ("agentcore.security.tokens", "create_inference_token"),
    "create_mfa_pending_token": ("agentcore.security.tokens", "create_mfa_pending_token"),
    "create_preview_token": ("agentcore.security.tokens", "create_preview_token"),
    "decode_access_token": ("agentcore.security.tokens", "decode_access_token"),
    "decode_access_token_claims": ("agentcore.security.tokens", "decode_access_token_claims"),
    "decode_access_token_family": ("agentcore.security.tokens", "decode_access_token_family"),
    "decode_access_token_mfa_verified": (
        "agentcore.security.tokens",
        "decode_access_token_mfa_verified",
    ),
    "decode_account_token": ("agentcore.security.tokens", "decode_account_token"),
    "decode_folders_token": ("agentcore.security.tokens", "decode_folders_token"),
    "decode_inference_token": ("agentcore.security.tokens", "decode_inference_token"),
    "decode_mfa_pending_token": ("agentcore.security.tokens", "decode_mfa_pending_token"),
    "decode_preview_token": ("agentcore.security.tokens", "decode_preview_token"),
}


def __getattr__(name: str) -> Any:
    spec = _LAZY.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = spec
    from importlib import import_module

    value = getattr(import_module(mod_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
