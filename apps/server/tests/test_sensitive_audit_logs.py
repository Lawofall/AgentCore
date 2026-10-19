"""P4-5: sensitive-op audit events (login fail / MFA / BYOK key) hit the catalog logger."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.auth.mfa import AdminMfaService
from agentcore.auth.service import AuthService, _subject_hash
from agentcore.config import settings
from agentcore.core.errors import AuthenticationError
from agentcore.security import create_mfa_pending_token, hash_password
from agentcore.security.keys import KeyEncryptor
from tests.conftest import LogSpy
from tests.test_auth_service import (
    _PW,
    FakeCredentials,
    FakeRefreshTokens,
    FakeUsers,
    _make,
)

_MASTER_KEY = "a" * 64

pytestmark = pytest.mark.anyio


class FakeMfa:
    def __init__(
        self,
        *,
        enrolled: bool = True,
        accept_code: str = "123456",
        accept_recovery: str | None = None,
    ) -> None:
        self.enrolled = enrolled
        self.accept_code = accept_code
        self.accept_recovery = accept_recovery

    async def is_enrolled(self, user_id: str) -> bool:
        return self.enrolled

    async def verify_code(self, *, user_id: str, code: str) -> bool:
        return code == self.accept_code

    async def verify_recovery_code(self, *, user_id: str, code: str) -> bool:
        return bool(self.accept_recovery) and code == self.accept_recovery


def _make_admin_with_mfa(**mfa_kw):
    users = FakeUsers()
    creds = FakeCredentials()
    tokens = FakeRefreshTokens()
    mfa = FakeMfa(**mfa_kw)
    svc = AuthService(
        users=users,
        credentials=creds,
        refresh_tokens=tokens,
        mfa=mfa,
    )
    return svc, users, creds, tokens, mfa


async def test_login_wrong_password_emits_auth_login_failed(monkeypatch):
    import agentcore.auth.service as mod

    spy = LogSpy()
    monkeypatch.setattr(mod, "logger", spy)
    svc, _u, _c, _t = _make()
    user = await svc.register(username="auditpw", password=_PW)
    with pytest.raises(AuthenticationError):
        await svc.login(username="auditpw", password="wrong-pw")
    kw = spy.get("auth.login_failed")
    assert kw["reason"] == "password"
    assert kw["user_id"] == user.user_id
    assert "password" not in kw


async def test_login_unknown_user_emits_subject_hash(monkeypatch):
    import agentcore.auth.service as mod

    spy = LogSpy()
    monkeypatch.setattr(mod, "logger", spy)
    svc, *_ = _make()
    with pytest.raises(AuthenticationError):
        await svc.login(username="GhostUser", password=_PW)
    kw = spy.get("auth.login_failed")
    assert kw["reason"] == "unknown"
    assert kw["subject"] == _subject_hash("GhostUser")
    assert "user_id" not in kw


async def test_mfa_login_wrong_code_emits_auth_login_failed(monkeypatch):
    import agentcore.auth.service as mod

    spy = LogSpy()
    monkeypatch.setattr(mod, "logger", spy)
    svc, users, creds, *_ = _make_admin_with_mfa(accept_code="123456")
    admin = await users.create(username="mfabad", display_name="A", role="admin")
    await creds.create(user_id=admin.user_id, password_hash=hash_password(_PW))
    pending = create_mfa_pending_token(admin.user_id, audience="admin")
    with pytest.raises(AuthenticationError):
        await svc.complete_mfa_login(pending_token=pending, code="000000")
    kw = spy.get("auth.login_failed")
    assert kw["reason"] == "mfa"
    assert kw["method"] == "totp"
    assert kw["user_id"] == admin.user_id
    assert "code" not in kw


async def test_mfa_recovery_success_emits_recovery_used(monkeypatch):
    import agentcore.auth.service as mod

    spy = LogSpy()
    monkeypatch.setattr(mod, "logger", spy)
    svc, users, creds, *_ = _make_admin_with_mfa(accept_recovery="deadbeef")
    admin = await users.create(username="mfarec", display_name="A", role="admin")
    await creds.create(user_id=admin.user_id, password_hash=hash_password(_PW))
    pending = create_mfa_pending_token(admin.user_id, audience="admin")
    await svc.complete_mfa_login(pending_token=pending, recovery_code="deadbeef")
    kw = spy.get("auth.mfa_recovery_used")
    assert kw["user_id"] == admin.user_id
    assert not any(n == "auth.login_failed" for n, _ in spy.events)


async def test_mfa_confirm_setup_emits_enrolled(monkeypatch):
    import agentcore.auth.mfa as mfa_mod

    spy = LogSpy()
    monkeypatch.setattr(mfa_mod, "logger", spy)
    monkeypatch.setattr(settings, "encryption_key", _MASTER_KEY)
    monkeypatch.setattr(mfa_mod, "enforce_mfa_verify_rate_limit", AsyncMock())

    enc = KeyEncryptor(_MASTER_KEY)
    secret = "JBSWY3DPEHPK3PXP"
    enc_secret = enc.encrypt(secret.encode())

    repo = AsyncMock()
    repo.get_by_user_id.return_value = SimpleNamespace(totp_secret_enc=enc_secret)
    repo.enable = AsyncMock()

    monkeypatch.setattr(
        mfa_mod.pyotp.TOTP,
        "verify",
        lambda self, code, valid_window=0: code == "999999",
    )

    svc = AdminMfaService(mfa_repo=repo)
    result = await svc.confirm_setup(user_id="admin-1", code="999999")
    assert len(result.recovery_codes) == 8
    kw = spy.get("auth.mfa_enrolled")
    assert kw["user_id"] == "admin-1"
    # Never leak secrets / codes into the audit line.
    joined = " ".join(f"{k}={v}" for k, v in kw.items())
    assert secret not in joined
    assert result.recovery_codes[0] not in joined


async def test_llm_provider_key_updated_and_deleted_emit(monkeypatch):
    import agentcore.api.routes.llm_providers as routes

    spy = LogSpy()
    monkeypatch.setattr(routes, "logger", spy)

    service = AsyncMock()
    service.update_provider = AsyncMock(
        return_value=SimpleNamespace(
            id="prov-1",
            label="x",
            base_url="https://api.example",
            vendor_id="custom",
            status="unchecked",
            masked_key="••••1234",
            supports_tools=None,
            message=None,
            created_at=None,
            updated_at=None,
        )
    )
    service.delete_provider = AsyncMock()

    user = SimpleNamespace(user_id="u1")
    body = MagicMock()
    body.label = None
    body.api_key = "sk-new-secret-key"
    body.base_url = None
    body.model_fields_set = {"api_key"}

    await routes.update_llm_provider("prov-1", body, user, service)
    assert spy.get("llm_provider.key_updated") == {
        "user_id": "u1",
        "provider_id": "prov-1",
    }
    # Plaintext key must not appear in structured kwargs.
    assert all("sk-new" not in str(kw) for _, kw in spy.events)

    await routes.delete_llm_provider("prov-1", user, service)
    assert spy.get("llm_provider.deleted") == {
        "user_id": "u1",
        "provider_id": "prov-1",
    }
