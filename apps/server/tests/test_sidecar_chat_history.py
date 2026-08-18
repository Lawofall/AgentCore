"""Sidecar prefers cloud-assembled history unless the caller already confirmed one."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentcore.account.credentials import AccountCloudError, AccountCredentials
from agentcore.sidecar.chat_history import coerce_history_rows, resolve_sidecar_turn_history

pytestmark = pytest.mark.anyio


def test_coerce_history_rows_keeps_ledger_drops_junk():
    rows = coerce_history_rows(
        [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b", "evidence_ledger": [{"id": "1"}]},
            {"role": "system", "content": "no"},
            {"role": "user"},
            "x",
        ]
    )
    assert rows == [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b", "evidence_ledger": [{"id": "1"}]},
    ]


async def test_resolve_uses_fallback_without_creds():
    out = await resolve_sidecar_turn_history(
        "c1",
        creds=None,
        fallback=[{"role": "user", "content": "stamped"}],
    )
    assert out == [{"role": "user", "content": "stamped"}]


async def test_resolve_skips_cloud_when_not_prefer_cloud(monkeypatch):
    async def _ok(*_a, **_k):
        raise AssertionError("cloud must not be called")

    monkeypatch.setattr("agentcore.sidecar.chat_history.cloud_chat_context", _ok)
    out = await resolve_sidecar_turn_history(
        "c1",
        creds=AccountCredentials(api_key="k", base_url="https://x/v1/account"),
        fallback=[{"role": "user", "content": "desktop"}],
        prefer_cloud=False,
    )
    assert out == [{"role": "user", "content": "desktop"}]


async def test_resolve_skips_cloud_for_confirmed_empty_when_not_prefer_cloud(monkeypatch):
    calls = {"n": 0}

    async def _ok(*_a, **_k):
        calls["n"] += 1
        return {"history": [{"role": "user", "content": "should-not"}]}

    monkeypatch.setattr("agentcore.sidecar.chat_history.cloud_chat_context", _ok)
    out = await resolve_sidecar_turn_history(
        "c1",
        creds=AccountCredentials(api_key="k", base_url="https://x/v1/account"),
        fallback=[],
        prefer_cloud=False,
    )
    assert out == []
    assert calls["n"] == 0


async def test_resolve_prefers_cloud(monkeypatch):
    async def _ok(creds, *, conversation_id):
        assert conversation_id == "c1"
        assert creds.api_key == "k"
        return {"history": [{"role": "assistant", "content": "from-cloud"}]}

    monkeypatch.setattr("agentcore.sidecar.chat_history.cloud_chat_context", _ok)
    out = await resolve_sidecar_turn_history(
        "c1",
        creds=AccountCredentials(api_key="k", base_url="https://x/v1/account"),
        fallback=[{"role": "user", "content": "stamped"}],
    )
    assert out == [{"role": "assistant", "content": "from-cloud"}]


async def test_resolve_falls_back_on_cloud_error(monkeypatch):
    async def _boom(*_a, **_k):
        raise AccountCloudError("down", code="account_cloud_unreachable")

    monkeypatch.setattr("agentcore.sidecar.chat_history.cloud_chat_context", _boom)
    out = await resolve_sidecar_turn_history(
        "c1",
        creds=AccountCredentials(api_key="k", base_url="https://x/v1/account"),
        fallback=[{"role": "user", "content": "stamped"}],
    )
    assert out == [{"role": "user", "content": "stamped"}]


async def test_resolve_raises_when_cloud_fails_without_confirmed_fallback(monkeypatch):
    from agentcore.sidecar.chat_history import ChatContextUnavailableError

    calls = {"n": 0}

    async def _boom(*_a, **_k):
        calls["n"] += 1
        raise AccountCloudError("down", code="account_cloud_unreachable")

    monkeypatch.setattr("agentcore.sidecar.chat_history.cloud_chat_context", _boom)
    with pytest.raises(ChatContextUnavailableError, match="未能加载对话历史"):
        await resolve_sidecar_turn_history(
            "c1",
            creds=AccountCredentials(api_key="k", base_url="https://x/v1/account"),
            fallback=None,
        )
    assert calls["n"] == 1


async def test_resolve_keeps_confirmed_empty_fallback_on_cloud_error(monkeypatch):
    async def _boom(*_a, **_k):
        raise AccountCloudError("down", code="account_cloud_unreachable")

    monkeypatch.setattr("agentcore.sidecar.chat_history.cloud_chat_context", _boom)
    out = await resolve_sidecar_turn_history(
        "c1",
        creds=AccountCredentials(api_key="k", base_url="https://x/v1/account"),
        fallback=[],
    )
    assert out == []


async def test_resolve_retries_server_error_then_succeeds(monkeypatch):
    calls = {"n": 0}

    async def _flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AccountCloudError("5xx", code="account_cloud_server")
        return {"history": [{"role": "user", "content": "after-retry"}]}

    monkeypatch.setattr("agentcore.sidecar.chat_history.cloud_chat_context", _flaky)
    out = await resolve_sidecar_turn_history(
        "c1",
        creds=AccountCredentials(api_key="k", base_url="https://x/v1/account"),
        fallback=None,
    )
    assert out == [{"role": "user", "content": "after-retry"}]
    assert calls["n"] == 2


async def test_assemble_owned_chat_context_404(monkeypatch):
    from agentcore.conversation.chat_context import assemble_owned_chat_context
    from agentcore.core.errors import NotFoundError

    class _Repo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

    monkeypatch.setattr(
        "agentcore.conversation.chat_context.ConversationRepository",
        _Repo,
    )
    with pytest.raises(NotFoundError):
        await assemble_owned_chat_context(SimpleNamespace(), "c1", user_id="u1")
