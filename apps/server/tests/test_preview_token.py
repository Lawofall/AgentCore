"""Cloud user-preview JWT + settings + error catalog (安全 · 五、第二刀)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from agentcore.config import settings
from agentcore.config.auth import AuthSettings
from agentcore.config.workspace import WorkspaceSettings
from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import AuthenticationError, PreviewUnavailableError
from agentcore.security import (
    create_access_token,
    create_account_token,
    create_folders_token,
    create_inference_token,
    create_preview_token,
    decode_access_token,
    decode_account_token,
    decode_folders_token,
    decode_inference_token,
    decode_preview_token,
)

_JWT_ALGORITHM = "HS256"


def _mint_preview(**overrides):
    kwargs = {
        "conversation_id": "conv-1",
        "process_id": "proc-1",
        "port": 5173,
        **overrides,
    }
    return create_preview_token("user-1", **kwargs)


def _encode(claims: dict) -> str:
    now = datetime.now(UTC)
    payload = {
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
        **claims,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)


def test_preview_token_roundtrip():
    token = _mint_preview()
    claims = decode_preview_token(token)
    assert claims.user_id == "user-1"
    assert claims.conversation_id == "conv-1"
    assert claims.process_id == "proc-1"
    assert claims.port == 5173
    unverified = jwt.get_unverified_claims(token)
    assert unverified["type"] == "preview"
    assert unverified["sub"] == "user-1"
    assert unverified["cid"] == "conv-1"
    assert unverified["pid"] == "proc-1"
    assert unverified["port"] == 5173


def test_preview_token_default_ttl_is_15_minutes():
    token = _mint_preview()
    claims = jwt.get_unverified_claims(token)
    assert claims["exp"] - claims["iat"] == 15 * 60
    assert AuthSettings().preview_token_expire_minutes == 15
    assert settings.preview_token_expire_minutes == 15


def test_preview_token_rejects_other_types():
    others = (
        create_access_token("user-1", audience="product"),
        create_inference_token("user-1"),
        create_folders_token("user-1"),
        create_account_token("user-1"),
    )
    for other in others:
        with pytest.raises(AuthenticationError):
            decode_preview_token(other)


def test_other_decoders_reject_preview_token():
    preview = _mint_preview()
    with pytest.raises(AuthenticationError):
        decode_access_token(preview)
    with pytest.raises(AuthenticationError):
        decode_inference_token(preview)
    with pytest.raises(AuthenticationError):
        decode_folders_token(preview)
    with pytest.raises(AuthenticationError):
        decode_account_token(preview)


def test_preview_token_rejects_expired():
    expired = _mint_preview(expires_delta=timedelta(minutes=-1))
    with pytest.raises(AuthenticationError):
        decode_preview_token(expired)


def test_preview_token_rejects_missing_claims():
    incomplete = _encode({"sub": "user-1", "type": "preview"})
    with pytest.raises(AuthenticationError):
        decode_preview_token(incomplete)


def test_preview_token_rejects_non_int_port():
    token = _encode(
        {
            "sub": "user-1",
            "type": "preview",
            "cid": "conv-1",
            "pid": "proc-1",
            "port": True,
        }
    )
    with pytest.raises(AuthenticationError):
        decode_preview_token(token)


def test_preview_workspace_settings_defaults():
    ws = WorkspaceSettings()
    assert ws.preview_bind_host == "127.0.0.1"
    assert ws.preview_bind_port == 8787
    assert ws.preview_public_base_url == ""


def test_preview_unavailable_error_is_503_catalogued():
    err = PreviewUnavailableError()
    assert err.code == ErrorCode.PREVIEW_UNAVAILABLE
    assert err.status_code == 503
    assert err.retryable is False
