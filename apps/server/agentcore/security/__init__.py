"""Security primitives: password hashing, JWT tokens, CSRF, refresh tokens,
at-rest secret encryption (BYOK API keys).

Split by concern under ``agentcore.security.*``; this package re-exports the
historical flat import path ``from agentcore.security import X``.
"""

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
